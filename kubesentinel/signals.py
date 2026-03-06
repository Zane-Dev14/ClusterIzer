import logging
from typing import Dict, Any, List, Set, Tuple, Optional

from .models import InfraState, MAX_SIGNALS

logger = logging.getLogger(__name__)

# CIS Kubernetes Benchmark mappings (v1.7.0 controls)
CIS_MAPPINGS = {
    # Container-level (5.2.x)
    "privileged_container": "5.2.1",
    "host_pid": "5.2.2",
    "host_ipc": "5.2.3",
    "host_network": "5.2.4",
    "allow_privilege_escalation": "5.2.5",
    "run_as_non_root": "5.2.6",
    "image_pull_policy": "5.2.7",
    "immutable_root_filesystem": "5.2.9",
    "no_resource_limits": "5.2.12",
    "latest_image_tag": "5.4.1",
    # Namespace-level
    "default_namespace": "5.7.3",
    "psa_enforcement_absent": "5.7.2",
    "networkpolicy_absent": "5.4.5",
    # Pod-level
    "no_liveness_probe": "5.1.1",
    "no_readiness_probe": "5.1.2",
    # RBAC
    "rbac_permissive": "5.1.3",
    "service_account_exposed": "5.1.4",
}


def generate_signals(state: InfraState) -> InfraState:
    """Generate deterministic signals from cluster snapshot and graph."""
    logger.info("Generating signals...")
    snapshot, graph = state["cluster_snapshot"], state["graph_summary"]
    signals: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()

    # Pod and container signals
    _generate_pod_signals(snapshot, seen, signals)
    _generate_container_signals(snapshot, seen, signals)

    # Workload signals
    _generate_deployment_signals(snapshot, graph, seen, signals)
    _generate_statefulset_signals(snapshot, seen, signals)
    _generate_daemonset_signals(snapshot, seen, signals)

    # Service and network signals
    _generate_service_signals(snapshot, graph, seen, signals)

    # Namespace-level signals
    _generate_namespace_signals(snapshot, seen, signals)

    signals = signals[:MAX_SIGNALS]
    logger.info(f"Generated {len(signals)} signals")
    state["signals"] = signals
    return state


def _add_signal(
    signals: List[Dict[str, Any]],
    seen: Set[Tuple[str, str, str]],
    category: str,
    severity: str,
    resource: str,
    message: str,
    cis_control: Optional[str] = None,
    signal_id: Optional[str] = None,
) -> None:
    key = (category, resource, message)
    if key not in seen:
        seen.add(key)
        signal = {
            "category": category,
            "severity": severity,
            "resource": resource,
            "message": message,
        }
        if cis_control:
            signal["cis_control"] = cis_control
        if signal_id:
            signal["signal_id"] = signal_id
        signals.append(signal)


def _generate_pod_signals(snapshot: Dict[str, Any], seen: Set, signals: List) -> None:
    """Generate pod-related reliability signals."""
    for pod in snapshot["pods"]:
        resource = f"pod/{pod['namespace']}/{pod['name']}"

        # Default namespace check
        if pod["namespace"] == "default":
            _add_signal(
                signals,
                seen,
                "security",
                "low",
                resource,
                "Pod running in default namespace",
                cis_control=CIS_MAPPINGS["default_namespace"],
                signal_id="default_namespace",
            )

        if pod.get("crash_loop_backoff"):
            _add_signal(
                signals,
                seen,
                "reliability",
                "critical",
                resource,
                "Pod in CrashLoopBackOff state",
            )
        for cs in pod.get("container_statuses", []):
            if not cs.get("ready") and cs.get("state") != "Running":
                _add_signal(
                    signals,
                    seen,
                    "reliability",
                    "high",
                    resource,
                    f"Container {cs['name']} not ready (state: {cs.get('state', 'unknown')})",
                )


def _generate_deployment_signals(
    snapshot: Dict[str, Any], graph: Dict[str, Any], seen: Set, signals: List
) -> None:
    """Generate deployment-related signals."""
    dep_map = {d["name"]: d for d in snapshot["deployments"]}

    # Single replica deployments
    for dep_name in graph.get("single_replica_deployments", []):
        if dep := dep_map.get(dep_name):
            _add_signal(
                signals,
                seen,
                "reliability",
                "medium",
                f"deployment/{dep['namespace']}/{dep_name}",
                "Deployment has only 1 replica (no redundancy)",
            )

    # High replica count
    for dep in snapshot["deployments"]:
        if dep.get("replicas", 1) > 3:
            _add_signal(
                signals,
                seen,
                "cost",
                "low",
                f"deployment/{dep['namespace']}/{dep['name']}",
                f"Deployment has {dep['replicas']} replicas (may be over-provisioned)",
            )


def _generate_container_signals(
    snapshot: Dict[str, Any], seen: Set, signals: List
) -> None:
    for dep in snapshot["deployments"]:
        resource = f"deployment/{dep['namespace']}/{dep['name']}"
        for container in dep.get("containers", []):
            # Security signals with CIS mappings
            if container.get("privileged"):
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "critical",
                    resource,
                    f"Container {container['name']} runs in privileged mode",
                    cis_control=CIS_MAPPINGS["privileged_container"],
                    signal_id="privileged_container",
                )

            image = container.get("image", "")
            if image.endswith(":latest") or ":" not in image:
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "high",
                    resource,
                    f"Container {container['name']} uses :latest or untagged image",
                    cis_control=CIS_MAPPINGS["latest_image_tag"],
                    signal_id="latest_image_tag",
                )

            # Cost/Security signals for missing limits
            if not container.get("limits"):
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "medium",
                    resource,
                    f"Container {container['name']} has no resource limits (security risk)",
                    cis_control=CIS_MAPPINGS["no_resource_limits"],
                    signal_id="no_resource_limits",
                )
                _add_signal(
                    signals,
                    seen,
                    "cost",
                    "medium",
                    resource,
                    f"Container {container['name']} has no resource limits (cost risk)",
                )


def _generate_service_signals(
    snapshot: Dict[str, Any], graph: Dict[str, Any], seen: Set, signals: List
) -> None:
    svc_map = {s["name"]: s for s in snapshot["services"]}
    for svc_name in graph.get("orphan_services", []):
        if svc := svc_map.get(svc_name):
            _add_signal(
                signals,
                seen,
                "reliability",
                "medium",
                f"service/{svc['namespace']}/{svc_name}",
                "Service has no matching deployments",
            )


def _generate_statefulset_signals(
    snapshot: Dict[str, Any], seen: Set, signals: List
) -> None:
    """Generate StatefulSet-specific signals."""
    for sts in snapshot.get("statefulsets", []):
        resource = f"statefulset/{sts['namespace']}/{sts['name']}"

        # StatefulSets without headless service
        if not sts.get("service_name"):
            _add_signal(
                signals,
                seen,
                "reliability",
                "high",
                resource,
                "StatefulSet missing headless service (required for stable pod identity)",
                cis_control="5.1.1",
            )

        # Container security analysis (reuse deployment logic)
        for container in sts.get("containers", []):
            if container.get("privileged"):
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "critical",
                    resource,
                    f"Container {container['name']} runs in privileged mode",
                    cis_control=CIS_MAPPINGS["privileged_container"],
                    signal_id="privileged_container",
                )

            if not container.get("limits"):
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "medium",
                    resource,
                    f"Container {container['name']} has no resource limits",
                    cis_control=CIS_MAPPINGS["no_resource_limits"],
                    signal_id="no_resource_limits",
                )


def _generate_daemonset_signals(
    snapshot: Dict[str, Any], seen: Set, signals: List
) -> None:
    """Generate DaemonSet-specific signals."""
    for ds in snapshot.get("daemonsets", []):
        resource = f"daemonset/{ds['namespace']}/{ds['name']}"

        # DaemonSets with auto-update strategy
        update_strategy = ds.get("update_strategy", "RollingUpdate")
        if update_strategy != "RollingUpdate":
            _add_signal(
                signals,
                seen,
                "reliability",
                "medium",
                resource,
                f"DaemonSet uses {update_strategy} update strategy (may cause uneven rollouts)",
            )

        # Container security analysis
        for container in ds.get("containers", []):
            if container.get("privileged"):
                _add_signal(
                    signals,
                    seen,
                    "security",
                    "critical",
                    resource,
                    f"Container {container['name']} runs in privileged mode",
                    cis_control=CIS_MAPPINGS["privileged_container"],
                    signal_id="privileged_container",
                )


def _generate_namespace_signals(
    snapshot: Dict[str, Any], seen: Set, signals: List
) -> None:
    """Generate namespace-level signals."""
    # Extract unique namespaces
    namespaces = set()
    for pod in snapshot.get("pods", []):
        ns = pod.get("namespace", "default")
        if ns != "kube-system" and ns != "kube-node-lease" and ns != "kube-public":
            namespaces.add(ns)

    for ns in namespaces:
        resource = f"namespace/{ns}"

        # Default namespace check (already done at pod level, but track namespace-wide)
        if ns == "default":
            _add_signal(
                signals,
                seen,
                "security",
                "medium",
                resource,
                "Namespace is 'default' - all workloads exposed to same namespace",
                cis_control=CIS_MAPPINGS["default_namespace"],
                signal_id="default_namespace",
            )

        # Check if namespace has any workloads (pod count)
        ns_pods = [p for p in snapshot.get("pods", []) if p.get("namespace") == ns]
        if len(ns_pods) == 0:
            _add_signal(
                signals,
                seen,
                "cost",
                "low",
                resource,
                "Empty namespace (no active pods)",
                signal_id="empty_namespace",
            )


def _generate_node_signals(snapshot: Dict[str, Any], seen: Set, signals: List) -> None:
    """Generate node pressure and readiness signals for SRE monitoring."""
    nodes = snapshot.get("nodes", [])

    for node in nodes:
        node_name = node.get("name", "unknown")
        resource = f"node/{node_name}"
        conditions = node.get("conditions", {})

        # Critical: Node not ready
        if conditions.get("Ready") is False:
            _add_signal(
                signals,
                seen,
                "reliability",
                "critical",
                resource,
                f"Node {node_name} is NotReady - workloads cannot schedule",
                signal_id="node_not_ready",
            )

        # High: Memory pressure detected
        if conditions.get("MemoryPressure") is True:
            _add_signal(
                signals,
                seen,
                "reliability",
                "high",
                resource,
                f"Node {node_name} experiencing MemoryPressure - evictions may occur",
                signal_id="memory_pressure",
            )

        # High: Disk pressure detected
        if conditions.get("DiskPressure") is True:
            _add_signal(
                signals,
                seen,
                "reliability",
                "high",
                resource,
                f"Node {node_name} experiencing DiskPressure - pods may be evicted",
                signal_id="disk_pressure",
            )

        # Medium: PID pressure detected
        if conditions.get("PIDPressure") is True:
            _add_signal(
                signals,
                seen,
                "reliability",
                "medium",
                resource,
                f"Node {node_name} experiencing PIDPressure - process limit reached",
                signal_id="pid_pressure",
            )

        # High: Network unavailable
        if conditions.get("NetworkUnavailable") is True:
            _add_signal(
                signals,
                seen,
                "reliability",
                "high",
                resource,
                f"Node {node_name} has NetworkUnavailable condition - connectivity issues",
                signal_id="network_unavailable",
            )


def _generate_orphan_workload_signals(
    snapshot: Dict[str, Any], graph_summary: Dict[str, Any], seen: Set, signals: List
) -> None:
    """Detect orphaned workloads and broken ownership chains."""
    ownership_index = graph_summary.get("ownership_index", {})
    broken_refs = graph_summary.get("broken_ownership_refs", [])

    # Signal for pods with broken ownership references
    for broken_ref in broken_refs:
        resource_type = broken_ref.get("resource_type")
        resource_name = broken_ref.get("resource_name")
        missing_owner = broken_ref.get("missing_owner_kind")
        missing_uid = broken_ref.get("missing_owner_uid")

        resource = f"{resource_type}/{resource_name}"
        _add_signal(
            signals,
            seen,
            "reliability",
            "high",
            resource,
            f"Broken ownership: references {missing_owner} with UID {missing_uid[:8]}... which doesn't exist",
            signal_id="broken_ownership_chain",
        )

    # Detect orphaned pods (no owner reference at all)
    pods = snapshot.get("pods", [])
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        owner_refs = pod.get("owner_references", [])

        # Skip system namespaces
        if pod.get("namespace") in ["kube-system", "kube-node-lease", "kube-public"]:
            continue

        # Orphan detection: no owner references AND not in ownership index
        if not owner_refs and pod_key not in ownership_index:
            resource = f"pod/{pod['namespace']}/{pod['name']}"
            _add_signal(
                signals,
                seen,
                "reliability",
                "medium",
                resource,
                "Orphaned pod with no controller - will not be recreated if deleted",
                signal_id="orphaned_pod",
            )
