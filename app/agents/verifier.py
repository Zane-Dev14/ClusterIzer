"""Verifier Agent — post-remediation health checks.

After a remediation is applied, the verifier re-fetches the target object
and checks that the expected state is now correct.
"""

from __future__ import annotations

import logging
from typing import Any

from app import config
from app.models import Finding

logger = logging.getLogger("clustergpt.verifier")


def verify(
    kubeconfig: str,
    applied_findings: list[Finding],
) -> list[dict[str, Any]]:
    """Re-check cluster state for each applied remediation.

    Parameters
    ----------
    kubeconfig:
        Path to kubeconfig.
    applied_findings:
        Findings whose remediations were applied.

    Returns
    -------
    list[dict]
        One entry per finding: ``{"finding_id", "status", "detail"}``.
    """
    results: list[dict[str, Any]] = []
    for finding in applied_findings:
        try:
            result = _verify_single(kubeconfig, finding)
            results.append(result)
        except Exception as exc:
            logger.warning("Verification failed for %s: %s", finding.id, exc)
            results.append({
                "finding_id": finding.id,
                "status": "error",
                "detail": str(exc),
            })
    return results


def _verify_single(kubeconfig: str, finding: Finding) -> dict[str, Any]:
    """Verify a single remediation by re-checking the target object."""
    if not finding.evidence:
        return {
            "finding_id": finding.id,
            "status": "skip",
            "detail": "No evidence pointer — cannot verify.",
        }

    ev = finding.evidence[0]
    kind = ev.kind
    ns = ev.namespace
    name = ev.name

    try:
        from kubernetes import client, config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig)
        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()

        if kind == "Deployment":
            dep = apps_v1.read_namespaced_deployment(name, ns or "default")
            status = dep.status
            ready = status.ready_replicas or 0
            desired = dep.spec.replicas or 1
            if ready >= desired:
                return {
                    "finding_id": finding.id,
                    "status": "pass",
                    "detail": f"Deployment {ns}/{name}: {ready}/{desired} replicas ready.",
                }
            return {
                "finding_id": finding.id,
                "status": "fail",
                "detail": f"Deployment {ns}/{name}: only {ready}/{desired} replicas ready.",
            }

        if kind == "Pod":
            pod = v1.read_namespaced_pod(name, ns or "default")
            phase = pod.status.phase
            restart_counts = [
                cs.restart_count
                for cs in (pod.status.container_statuses or [])
            ]
            total_restarts = sum(restart_counts)
            if phase == "Running" and total_restarts == 0:
                return {
                    "finding_id": finding.id,
                    "status": "pass",
                    "detail": f"Pod {ns}/{name} is Running with 0 restarts.",
                }
            return {
                "finding_id": finding.id,
                "status": "fail",
                "detail": (
                    f"Pod {ns}/{name} phase={phase}, "
                    f"restart_counts={restart_counts}."
                ),
            }

    except ImportError:
        pass  # kubernetes client not available, try kubectl
    except Exception as exc:
        logger.debug("K8s client verification error: %s", exc)

    # Fallback: subprocess
    return _verify_via_kubectl(kubeconfig, finding)


def _verify_via_kubectl(kubeconfig: str, finding: Finding) -> dict[str, Any]:
    """Verify via kubectl subprocess as a fallback."""
    import subprocess

    ev = finding.evidence[0]
    kind = ev.kind.lower()
    ns_flag = f"-n {ev.namespace}" if ev.namespace else ""
    cmd = (
        f"kubectl get {kind} {ev.name} {ns_flag} "
        f"--kubeconfig {kubeconfig} -o jsonpath='{{.status.phase}}'"
    )
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip()
        if output in ("Running", "Active", "Bound"):
            return {
                "finding_id": finding.id,
                "status": "pass",
                "detail": f"{ev.kind} {ev.namespace}/{ev.name}: {output}",
            }
        return {
            "finding_id": finding.id,
            "status": "fail",
            "detail": f"{ev.kind} {ev.namespace}/{ev.name}: {output or 'unknown'}",
        }
    except Exception as exc:
        return {
            "finding_id": finding.id,
            "status": "error",
            "detail": f"kubectl verification failed: {exc}",
        }
