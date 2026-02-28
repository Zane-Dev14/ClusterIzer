"""Remediation Agent — generates kubectl commands and YAML patches.

**Safety**: never auto-applies unless ``--apply`` is passed AND the user
confirms interactively (or ``--yes`` is set for CI).

Usage::

    from app.agents.remediation import generate_remediations, apply_remediation
    enriched = generate_remediations(findings)
    result = apply_remediation(enriched[0], kubeconfig, apply=True)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app import config
from app.models import Finding, RemediationDetail

logger = logging.getLogger("clustergpt.remediation")


# ---------------------------------------------------------------------------
# Enrichment — ensure every finding has remediation detail
# ---------------------------------------------------------------------------

def generate_remediations(findings: list[Finding]) -> list[Finding]:
    """Return a copy of *findings* with remediation fields enriched.

    Most rules already generate remediation detail.  This function fills in
    any that are missing or incomplete.
    """
    enriched: list[Finding] = []
    for f in findings:
        if not f.remediation.kubectl and not f.remediation.patch_yaml:
            # Generic fallback
            f = f.model_copy(update={
                "remediation": RemediationDetail(
                    description=(
                        f"Review and address finding: {f.summary[:100]}"
                    ),
                    kubectl=[
                        _generic_kubectl(f),
                    ],
                    patch_yaml="",
                ),
            })
        enriched.append(f)
    return enriched


def _generic_kubectl(finding: Finding) -> str:
    """Produce a best-effort kubectl command from evidence."""
    if not finding.evidence:
        return "# No evidence — manual investigation required."
    ev = finding.evidence[0]
    kind = ev.kind.lower()
    ns_flag = f" -n {ev.namespace}" if ev.namespace else ""
    return f"kubectl describe {kind} {ev.name}{ns_flag}"


# ---------------------------------------------------------------------------
# Apply (safety-gated)
# ---------------------------------------------------------------------------

def apply_remediation(
    finding: Finding,
    kubeconfig: str = config.DEFAULT_KUBECONFIG,
    apply: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Attempt to apply a remediation to the cluster.

    Parameters
    ----------
    finding:
        The finding whose remediation should be applied.
    kubeconfig:
        Path to the kubeconfig file.
    apply:
        If ``True``, actually patch the cluster (subject to *dry_run*).
    dry_run:
        If ``True`` (default), only emit the patch without side-effects.

    Returns
    -------
    dict
        ``{"finding_id": str, "applied": bool, "dry_run": bool,
           "kubectl": list[str], "patch_yaml": str, "error": str | None}``
    """
    result: dict[str, Any] = {
        "finding_id": finding.id,
        "applied": False,
        "dry_run": dry_run,
        "kubectl": finding.remediation.kubectl,
        "patch_yaml": finding.remediation.patch_yaml,
        "error": None,
    }

    if not apply:
        return result

    if dry_run:
        logger.info(
            "[DRY-RUN] Would apply remediation for %s", finding.id
        )
        return result

    # --- Actual apply path ---
    try:
        _backup_current_state(finding, kubeconfig)
        _apply_via_k8s_client(finding, kubeconfig)
        result["applied"] = True
        logger.info("Successfully applied remediation for %s", finding.id)
    except Exception as exc:
        result["error"] = str(exc)
        logger.error("Failed to apply remediation for %s: %s", finding.id, exc)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _backup_current_state(finding: Finding, kubeconfig: str) -> None:
    """Save the current manifest of the target object to backups/."""
    if not finding.evidence:
        return
    ev = finding.evidence[0]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_name = f"{ev.namespace}_{ev.name}_{ts}.json"
    backup_path = Path(config.BACKUP_DIR) / backup_name
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import subprocess
        ns_flag = f"-n {ev.namespace}" if ev.namespace else ""
        cmd = f"kubectl get {ev.kind.lower()} {ev.name} {ns_flag} -o json --kubeconfig {kubeconfig}"
        result = subprocess.run(
            cmd.split(), capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            backup_path.write_text(result.stdout, encoding="utf-8")
            logger.info("Backup saved to %s", backup_path)
    except Exception as exc:
        logger.warning("Could not backup %s: %s", ev.name, exc)


def _apply_via_k8s_client(finding: Finding, kubeconfig: str) -> None:
    """Apply patch via the kubernetes Python client."""
    if not finding.evidence:
        raise ValueError("No evidence to determine target object.")

    ev = finding.evidence[0]

    try:
        from kubernetes import client, config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig)

        if ev.kind == "Deployment" and finding.remediation.patch_yaml:
            import yaml
            patch_body = yaml.safe_load(finding.remediation.patch_yaml)
            apps_v1 = client.AppsV1Api()
            apps_v1.patch_namespaced_deployment(
                name=ev.name,
                namespace=ev.namespace or "default",
                body=patch_body,
            )
            return
    except ImportError:
        pass  # Fall through to kubectl subprocess

    # Fallback: apply via kubectl
    import subprocess
    for cmd_str in finding.remediation.kubectl:
        if cmd_str.startswith("#"):
            continue
        logger.info("Running: %s", cmd_str)
        subprocess.run(cmd_str, shell=True, check=True, timeout=30)
