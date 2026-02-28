"""Diagnosis Agent (Investigator) — correlates pod failures with events.

Targets the three most common failure modes:
  - CrashLoopBackOff
  - OOMKilled
  - ImagePullBackOff / ErrImagePull

Returns ranked hypotheses as ``Finding`` objects with evidence pointers
to specific events and containerStatuses.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models import (
    Category,
    Evidence,
    Finding,
    RemediationDetail,
    Severity,
)

logger = logging.getLogger("clustergpt.investigator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pod_key(pod: dict) -> tuple[str, str]:
    meta = pod.get("metadata", {})
    return meta.get("namespace", "default"), meta.get("name", "unknown")


def _find_events_for_pod(
    events: list[dict],
    namespace: str,
    pod_name: str,
) -> list[dict]:
    """Return events whose involvedObject matches the given pod."""
    matching: list[dict] = []
    for ev in events:
        obj = ev.get("involvedObject", {})
        if (
            obj.get("kind") == "Pod"
            and obj.get("namespace") == namespace
            and obj.get("name") == pod_name
        ):
            matching.append(ev)
    return matching


def _evidence_from_event(ev: dict) -> Evidence:
    obj = ev.get("involvedObject", {})
    return Evidence(
        kind="Event",
        namespace=obj.get("namespace", ""),
        name=obj.get("name", ""),
        timestamp=ev.get("lastTimestamp", ev.get("metadata", {}).get("creationTimestamp", "")),
        pointer=f"Reason: {ev.get('reason', '?')} — {ev.get('message', '')[:120]}",
    )


def _evidence_from_pod(ns: str, name: str, detail: str = "") -> Evidence:
    return Evidence(
        kind="Pod",
        namespace=ns,
        name=name,
        pointer=detail or f"kubectl describe pod {name} -n {ns}",
    )


# ---------------------------------------------------------------------------
# Failure detectors
# ---------------------------------------------------------------------------

def _detect_crashloop(pod: dict, events: list[dict]) -> Finding | None:
    """Detect CrashLoopBackOff from containerStatuses."""
    ns, name = _pod_key(pod)
    for cs in pod.get("status", {}).get("containerStatuses", []):
        waiting = cs.get("state", {}).get("waiting", {})
        if waiting.get("reason") == "CrashLoopBackOff":
            restart_count = cs.get("restartCount", 0)
            ctr_name = cs.get("name", "?")
            exit_code = (
                cs.get("lastState", {})
                .get("terminated", {})
                .get("exitCode", "?")
            )

            ev_list: list[Evidence] = [
                _evidence_from_pod(
                    ns, name,
                    f"Container '{ctr_name}' CrashLoopBackOff — "
                    f"restarts: {restart_count}, last exit code: {exit_code}",
                ),
            ]
            for ev in _find_events_for_pod(events, ns, name)[:3]:
                ev_list.append(_evidence_from_event(ev))

            return Finding(
                id=f"crashloop:{ns}/{name}/{ctr_name}",
                category=Category.reliability,
                severity=Severity.critical,
                summary=(
                    f"Pod {ns}/{name} container '{ctr_name}' is in "
                    f"CrashLoopBackOff (restarts: {restart_count}, "
                    f"exit code: {exit_code}). Likely an application "
                    f"crash on startup or configuration error."
                ),
                evidence=ev_list,
                remediation=RemediationDetail(
                    description=(
                        f"Check logs for container '{ctr_name}'. Common causes: "
                        f"missing env vars, bad config, entrypoint error."
                    ),
                    kubectl=[
                        f"kubectl logs {name} -n {ns} -c {ctr_name} --previous",
                        f"kubectl describe pod {name} -n {ns}",
                    ],
                    patch_yaml="",
                ),
            )
    return None


def _detect_oomkilled(pod: dict, events: list[dict]) -> Finding | None:
    """Detect OOMKilled from lastState.terminated."""
    ns, name = _pod_key(pod)
    for cs in pod.get("status", {}).get("containerStatuses", []):
        terminated = cs.get("lastState", {}).get("terminated", {})
        if terminated.get("reason") == "OOMKilled":
            ctr_name = cs.get("name", "?")
            restart_count = cs.get("restartCount", 0)

            # Try to find the memory limit for a better remediation.
            containers = (
                pod.get("spec", {}).get("containers", [])
            )
            current_limit = "unknown"
            for c in containers:
                if c.get("name") == ctr_name:
                    current_limit = (
                        c.get("resources", {})
                        .get("limits", {})
                        .get("memory", "not set")
                    )

            ev_list: list[Evidence] = [
                _evidence_from_pod(
                    ns, name,
                    f"Container '{ctr_name}' OOMKilled — "
                    f"restarts: {restart_count}, current memory limit: {current_limit}",
                ),
            ]
            for ev in _find_events_for_pod(events, ns, name)[:3]:
                ev_list.append(_evidence_from_event(ev))

            return Finding(
                id=f"oomkilled:{ns}/{name}/{ctr_name}",
                category=Category.reliability,
                severity=Severity.critical,
                summary=(
                    f"Pod {ns}/{name} container '{ctr_name}' was OOMKilled "
                    f"(memory limit: {current_limit}). The container exceeded "
                    f"its memory limit and was terminated by the kernel."
                ),
                evidence=ev_list,
                remediation=RemediationDetail(
                    description=(
                        f"Increase memory limit for '{ctr_name}' or investigate "
                        f"memory leaks. Current limit: {current_limit}."
                    ),
                    kubectl=[
                        f"kubectl set resources deployment -n {ns} "
                        f"$(kubectl get pod {name} -n {ns} -o jsonpath='{{.metadata.ownerReferences[0].name}}' "
                        f"| sed 's/-[a-z0-9]*$//') "
                        f"-c {ctr_name} --limits=memory=512Mi",
                    ],
                    patch_yaml=(
                        f"# Increase memory limit for container {ctr_name}\n"
                        f"resources:\n"
                        f"  limits:\n"
                        f"    memory: \"512Mi\""
                    ),
                ),
            )
    return None


def _detect_imagepull(pod: dict, events: list[dict]) -> Finding | None:
    """Detect ImagePullBackOff / ErrImagePull."""
    ns, name = _pod_key(pod)
    for cs in pod.get("status", {}).get("containerStatuses", []):
        waiting = cs.get("state", {}).get("waiting", {})
        reason = waiting.get("reason", "")
        if reason in ("ImagePullBackOff", "ErrImagePull"):
            ctr_name = cs.get("name", "?")
            image = cs.get("image", "unknown")

            ev_list: list[Evidence] = [
                _evidence_from_pod(
                    ns, name,
                    f"Container '{ctr_name}' {reason} — image: {image}",
                ),
            ]
            for ev in _find_events_for_pod(events, ns, name)[:3]:
                ev_list.append(_evidence_from_event(ev))

            return Finding(
                id=f"imagepull:{ns}/{name}/{ctr_name}",
                category=Category.reliability,
                severity=Severity.high,
                summary=(
                    f"Pod {ns}/{name} container '{ctr_name}' cannot pull "
                    f"image '{image}' ({reason}). Likely causes: image does "
                    f"not exist, tag missing, or registry auth not configured."
                ),
                evidence=ev_list,
                remediation=RemediationDetail(
                    description=(
                        f"Verify image '{image}' exists and is accessible. "
                        f"Check imagePullSecrets if using a private registry."
                    ),
                    kubectl=[
                        f"kubectl describe pod {name} -n {ns}",
                        f"kubectl get secrets -n {ns} -o name | grep docker",
                    ],
                    patch_yaml="",
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DETECTORS = [_detect_crashloop, _detect_oomkilled, _detect_imagepull]


def investigate(snapshot: dict[str, Any]) -> list[Finding]:
    """Scan pods for failure states and return diagnosis findings.

    Parameters
    ----------
    snapshot:
        Cluster snapshot dict.

    Returns
    -------
    list[Finding]
        Ranked by severity (critical first).
    """
    findings: list[Finding] = []
    events = snapshot.get("events", [])

    for pod in snapshot.get("pods", []):
        for detector in _DETECTORS:
            try:
                finding = detector(pod, events)
                if finding:
                    findings.append(finding)
            except Exception as exc:
                ns, name = _pod_key(pod)
                logger.warning(
                    "Detector %s failed for pod %s/%s: %s",
                    detector.__name__, ns, name, exc,
                )

    # Sort: critical first.
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f.severity.value, 99))
    return findings
