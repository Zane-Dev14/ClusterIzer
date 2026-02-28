"""Connector Agent — snapshots Kubernetes cluster state via the official API.

Usage (standalone test)::

    python -m app.tools.k8s_connector --kubeconfig ~/.kube/config

Produces ``snapshots/latest.json`` with keys:
    cluster_name, timestamp, namespaces, deployments, pods, nodes, events,
    hpa, services, ingresses, rbac_roles, rbac_bindings, networkpolicies.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from app import config
from app.models import ClusterMeta
from app.tools.utils import write_json, utcnow_iso

logger = logging.getLogger("clustergpt.connector")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialise(api_client: Any, obj: Any) -> Any:
    """Convert a K8s API object to a plain dict via the client's serialiser."""
    return api_client.sanitize_for_serialization(obj)


def _kubectl_fallback(resource: str, namespace: str | None = None) -> list[dict]:
    """Shell-out fallback when the Python client cannot reach the API."""
    cmd = ["kubectl", "get", resource, "-o", "json"]
    if namespace:
        cmd += ["-n", namespace]
    else:
        cmd += ["--all-namespaces"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        return data.get("items", [])
    except Exception as exc:
        logger.warning("kubectl fallback for %s failed: %s", resource, exc)
        return []


# ---------------------------------------------------------------------------
# Main snapshot function
# ---------------------------------------------------------------------------

def snapshot_cluster(
    kubeconfig_path: str,
    namespaces: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Connect to a cluster and return a full state snapshot as a plain dict.

    Parameters
    ----------
    kubeconfig_path:
        Path to a kubeconfig file.
    namespaces:
        If provided, restrict collection to these namespaces.
        ``None`` means all namespaces.

    Returns
    -------
    dict
        Snapshot with all collected resource lists.
    """
    try:
        from kubernetes import client, config as k8s_config

        k8s_config.load_kube_config(config_file=kubeconfig_path)
        api_client = client.ApiClient()
        v1 = client.CoreV1Api(api_client)
        apps_v1 = client.AppsV1Api(api_client)
        autoscaling_v1 = client.AutoscalingV1Api(api_client)
        networking_v1 = client.NetworkingV1Api(api_client)
        rbac_v1 = client.RbacAuthorizationV1Api(api_client)
    except Exception as exc:
        logger.error("Failed to load kubeconfig at %s: %s", kubeconfig_path, exc)
        logger.info("Falling back to kubectl subprocess calls.")
        return _snapshot_via_kubectl()

    snap: dict[str, Any] = {
        "cluster_name": _detect_cluster_name(kubeconfig_path),
        "timestamp": utcnow_iso(),
    }

    # --- Namespaces ---------------------------------------------------------
    snap["namespaces"] = _safe_list(
        lambda: _serialise(api_client, v1.list_namespace()),
        "namespaces",
    ).get("items", [])

    ns_names: list[str] = namespaces or [
        ns["metadata"]["name"] for ns in snap["namespaces"]
    ]

    # --- Deployments --------------------------------------------------------
    snap["deployments"] = _collect_namespaced(
        lambda ns: _serialise(api_client, apps_v1.list_namespaced_deployment(ns)),
        ns_names, "deployments",
    )

    # --- Pods ---------------------------------------------------------------
    snap["pods"] = _collect_namespaced(
        lambda ns: _serialise(api_client, v1.list_namespaced_pod(ns)),
        ns_names, "pods",
    )

    # --- Nodes (cluster-scoped) ---------------------------------------------
    snap["nodes"] = _safe_list(
        lambda: _serialise(api_client, v1.list_node()),
        "nodes",
    ).get("items", [])

    # --- Events (last 24 h) -------------------------------------------------
    snap["events"] = _collect_namespaced(
        lambda ns: _serialise(api_client, v1.list_namespaced_event(ns)),
        ns_names, "events",
    )
    snap["events"] = _filter_recent_events(snap["events"])

    # --- HPA ----------------------------------------------------------------
    snap["hpa"] = _collect_namespaced(
        lambda ns: _serialise(api_client, autoscaling_v1.list_namespaced_horizontal_pod_autoscaler(ns)),
        ns_names, "hpa",
    )

    # --- Services -----------------------------------------------------------
    snap["services"] = _collect_namespaced(
        lambda ns: _serialise(api_client, v1.list_namespaced_service(ns)),
        ns_names, "services",
    )

    # --- Ingresses ----------------------------------------------------------
    snap["ingresses"] = _collect_namespaced(
        lambda ns: _serialise(api_client, networking_v1.list_namespaced_ingress(ns)),
        ns_names, "ingresses",
    )

    # --- RBAC (cluster-scoped) ----------------------------------------------
    snap["rbac_roles"] = _safe_list(
        lambda: _serialise(api_client, rbac_v1.list_cluster_role()),
        "rbac_roles",
    ).get("items", [])

    snap["rbac_bindings"] = _safe_list(
        lambda: _serialise(api_client, rbac_v1.list_cluster_role_binding()),
        "rbac_bindings",
    ).get("items", [])

    # --- NetworkPolicies ----------------------------------------------------
    snap["networkpolicies"] = _collect_namespaced(
        lambda ns: _serialise(api_client, networking_v1.list_namespaced_network_policy(ns)),
        ns_names, "networkpolicies",
    )

    return snap


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_cluster_name(kubeconfig_path: str) -> str:
    """Best-effort extraction of cluster name from kubeconfig context."""
    try:
        from kubernetes import config as k8s_config
        _, active_context = k8s_config.list_kube_config_contexts(config_file=kubeconfig_path)
        return active_context.get("context", {}).get("cluster", active_context.get("name", "unknown"))
    except Exception:
        return "unknown"


def _safe_list(fn: Any, label: str) -> dict:
    """Call *fn* and return the result dict, or an empty ``{"items": []}``."""
    try:
        return fn()
    except Exception as exc:
        logger.warning("Could not list %s: %s", label, exc)
        return {"items": []}


def _collect_namespaced(
    fn: Any,
    ns_names: list[str],
    label: str,
) -> list[dict]:
    """Call *fn(ns)* for each namespace and merge all items."""
    items: list[dict] = []
    for ns in ns_names:
        try:
            result = fn(ns)
            items.extend(result.get("items", []))
        except Exception as exc:
            logger.warning("Could not list %s in namespace %s: %s", label, ns, exc)
    return items


def _filter_recent_events(
    events: list[dict],
    window_seconds: int = config.EVENT_WINDOW_SECONDS,
) -> list[dict]:
    """Keep only events from the last *window_seconds*."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    recent: list[dict] = []
    for ev in events:
        last_ts = ev.get("lastTimestamp") or ev.get("metadata", {}).get("creationTimestamp", "")
        if not last_ts:
            recent.append(ev)  # keep if we can't determine age
            continue
        try:
            ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if ts >= cutoff:
                recent.append(ev)
        except (ValueError, TypeError):
            recent.append(ev)
    return recent


# ---------------------------------------------------------------------------
# kubectl-only fallback (full snapshot)
# ---------------------------------------------------------------------------

def _snapshot_via_kubectl() -> dict[str, Any]:
    """Build a snapshot entirely from kubectl subprocess calls."""
    logger.info("Building snapshot via kubectl fallback…")
    return {
        "cluster_name": "unknown (kubectl fallback)",
        "timestamp": utcnow_iso(),
        "namespaces": _kubectl_fallback("namespaces"),
        "deployments": _kubectl_fallback("deployments"),
        "pods": _kubectl_fallback("pods"),
        "nodes": _kubectl_fallback("nodes"),
        "events": _kubectl_fallback("events"),
        "hpa": _kubectl_fallback("hpa"),
        "services": _kubectl_fallback("services"),
        "ingresses": _kubectl_fallback("ingresses"),
        "rbac_roles": _kubectl_fallback("clusterroles"),
        "rbac_bindings": _kubectl_fallback("clusterrolebindings"),
        "networkpolicies": _kubectl_fallback("networkpolicies"),
    }


# ---------------------------------------------------------------------------
# Build ClusterMeta from a snapshot
# ---------------------------------------------------------------------------

def build_cluster_meta(snapshot: dict[str, Any]) -> ClusterMeta:
    """Extract lightweight metadata from a snapshot dict."""
    return ClusterMeta(
        cluster_name=snapshot.get("cluster_name", "unknown"),
        timestamp=snapshot.get("timestamp", utcnow_iso()),
        node_count=len(snapshot.get("nodes", [])),
        namespace_count=len(snapshot.get("namespaces", [])),
        pod_count=len(snapshot.get("pods", [])),
    )


# ---------------------------------------------------------------------------
# Save / load snapshot JSON
# ---------------------------------------------------------------------------

def save_snapshot(snapshot: dict[str, Any], directory: str = config.SNAPSHOT_DIR) -> Path:
    """Write snapshot to ``<directory>/latest.json`` and return the path."""
    return write_json(snapshot, Path(directory) / "latest.json")


# ---------------------------------------------------------------------------
# CLI entrypoint for standalone testing
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Snapshot a Kubernetes cluster.")
    parser.add_argument("--kubeconfig", default=config.DEFAULT_KUBECONFIG)
    parser.add_argument("--namespace", action="append", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    snap = snapshot_cluster(args.kubeconfig, args.namespace)
    path = save_snapshot(snap)
    print(f"Snapshot written to {path}  ({len(snap.get('pods', []))} pods)")


if __name__ == "__main__":
    _cli()
