"""Signal engine - generates deterministic signals from cluster state."""
import logging
from typing import Dict, Any, List, Set, Tuple, Optional

from .models import InfraState, MAX_SIGNALS

logger = logging.getLogger(__name__)


# CIS Kubernetes Benchmark mappings (v1.7.0 controls)
CIS_MAPPINGS = {
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
    "default_namespace": "5.7.3",
    "no_liveness_probe": "5.1.1",
    "no_readiness_probe": "5.1.2",
}


def generate_signals(state: InfraState) -> InfraState:
    """Generate deterministic signals from cluster snapshot and graph."""
    logger.info("Generating signals...")
    snapshot, graph = state["cluster_snapshot"], state["graph_summary"]
    signals, seen = [], set()
    _generate_pod_signals(snapshot, seen, signals)
    _generate_deployment_signals(snapshot, graph, seen, signals)
    _generate_container_signals(snapshot, seen, signals)
    _generate_service_signals(snapshot, graph, seen, signals)
    signals = signals[:MAX_SIGNALS]
    logger.info(f"Generated {len(signals)} signals")
    state["signals"] = signals
    return state


def _add_signal(signals: List[Dict[str, Any]], seen: Set[Tuple[str, str, str]], 
                category: str, severity: str, resource: str, message: str,
                cis_control: Optional[str] = None, signal_id: Optional[str] = None) -> None:
    """Add signal if not already seen."""
    key = (category, resource, message)
    if key not in seen:
        seen.add(key)
        signal = {"category": category, "severity": severity, "resource": resource, "message": message}
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
        if pod['namespace'] == "default":
            _add_signal(signals, seen, "security", "low", resource,
                       "Pod running in default namespace",
                       cis_control=CIS_MAPPINGS["default_namespace"],
                       signal_id="default_namespace")
        
        if pod.get("crash_loop_backoff"):
            _add_signal(signals, seen, "reliability", "critical", resource, "Pod in CrashLoopBackOff state")
        for cs in pod.get("container_statuses", []):
            if not cs.get("ready") and cs.get("state") != "Running":
                _add_signal(signals, seen, "reliability", "high", resource, f"Container {cs['name']} not ready (state: {cs.get('state', 'unknown')})")

def _generate_deployment_signals(snapshot: Dict[str, Any], graph: Dict[str, Any], seen: Set, signals: List) -> None:
    """Generate deployment-related signals."""
    dep_map = {d["name"]: d for d in snapshot["deployments"]}
    
    # Single replica deployments
    for dep_name in graph.get("single_replica_deployments", []):
        if dep := dep_map.get(dep_name):
            _add_signal(signals, seen, "reliability", "medium",
                       f"deployment/{dep['namespace']}/{dep_name}",
                       "Deployment has only 1 replica (no redundancy)")
    
    # High replica count
    for dep in snapshot["deployments"]:
        if dep.get("replicas", 1) > 3:
            _add_signal(signals, seen, "cost", "low",
                       f"deployment/{dep['namespace']}/{dep['name']}",
                       f"Deployment has {dep['replicas']} replicas (may be over-provisioned)")


def _generate_container_signals(snapshot: Dict[str, Any], seen: Set, signals: List) -> None:
    for dep in snapshot["deployments"]:
        resource = f"deployment/{dep['namespace']}/{dep['name']}"
        for container in dep.get("containers", []):
            # Security signals with CIS mappings
            if container.get("privileged"):
                _add_signal(signals, seen, "security", "critical", resource,
                           f"Container {container['name']} runs in privileged mode",
                           cis_control=CIS_MAPPINGS["privileged_container"],
                           signal_id="privileged_container")
            
            image = container.get("image", "")
            if image.endswith(":latest") or ":" not in image:
                _add_signal(signals, seen, "security", "high", resource,
                           f"Container {container['name']} uses :latest or untagged image",
                           cis_control=CIS_MAPPINGS["latest_image_tag"],
                           signal_id="latest_image_tag")
            
            # Cost/Security signals for missing limits
            if not container.get("limits"):
                _add_signal(signals, seen, "security", "medium", resource,
                           f"Container {container['name']} has no resource limits (security risk)",
                           cis_control=CIS_MAPPINGS["no_resource_limits"],
                           signal_id="no_resource_limits")
                _add_signal(signals, seen, "cost", "medium", resource,
                           f"Container {container['name']} has no resource limits (cost risk)")



def _generate_service_signals(snapshot: Dict[str, Any], graph: Dict[str, Any],
                              seen: Set, signals: List) -> None:
    """Generate service-related signals."""
    svc_map = {s["name"]: s for s in snapshot["services"]}
    for svc_name in graph.get("orphan_services", []):
        if svc := svc_map.get(svc_name):
            _add_signal(signals, seen, "reliability", "medium",
                       f"service/{svc['namespace']}/{svc_name}",
                       "Service has no matching deployments")
