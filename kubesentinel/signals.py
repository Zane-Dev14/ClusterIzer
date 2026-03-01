"""
Signal engine node - generates deterministic signals from cluster state.

Analyzes cluster snapshot and graph summary to produce structured signals
across reliability, security, and cost categories. Pure function, no LLM.
"""

import logging
from typing import Dict, Any, List, Set, Tuple

from .models import InfraState, MAX_SIGNALS

logger = logging.getLogger(__name__)


def generate_signals(state: InfraState) -> InfraState:
    """
    Generate deterministic signals from cluster snapshot and graph.
    
    Signals are categorized as:
    - Reliability: CrashLoopBackOff, single replica, pod not ready
    - Security: privileged containers, :latest tags, missing limits
    - Cost: high replica count, missing limits, request/limit mismatch
    
    Deduplicates by (category, resource, message) tuple and caps at MAX_SIGNALS.
    
    Args:
        state: InfraState with cluster_snapshot and graph_summary
        
    Returns:
        Updated state with signals populated
    """
    logger.info("Generating signals...")
    
    snapshot = state["cluster_snapshot"]
    graph = state["graph_summary"]
    
    signals: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str]] = set()
    
    # Reliability signals
    signals.extend(_generate_reliability_signals(snapshot, graph, seen))
    
    # Security signals
    signals.extend(_generate_security_signals(snapshot, seen))
    
    # Cost signals
    signals.extend(_generate_cost_signals(snapshot, seen))
    
    # Cap at maximum
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
    message: str
) -> None:
    """Add signal if not already seen (deduplication)."""
    key = (category, resource, message)
    if key not in seen:
        seen.add(key)
        signals.append({
            "category": category,
            "severity": severity,
            "resource": resource,
            "message": message,
        })


def _generate_reliability_signals(
    snapshot: Dict[str, Any],
    graph: Dict[str, Any],
    seen: Set[Tuple[str, str, str]]
) -> List[Dict[str, Any]]:
    """Generate reliability-related signals."""
    signals = []
    pods = snapshot["pods"]
    deployments = snapshot["deployments"]
    
    # CrashLoopBackOff - critical
    for pod in pods:
        if pod.get("crash_loop_backoff"):
            resource = f"pod/{pod['namespace']}/{pod['name']}"
            _add_signal(
                signals, seen, "reliability", "critical", resource,
                "Pod in CrashLoopBackOff state"
            )
    
    # Pods not ready - high
    for pod in pods:
        for cs in pod.get("container_statuses", []):
            if not cs.get("ready") and cs.get("state") != "Running":
                resource = f"pod/{pod['namespace']}/{pod['name']}"
                _add_signal(
                    signals, seen, "reliability", "high", resource,
                    f"Container {cs['name']} not ready (state: {cs.get('state', 'unknown')})"
                )
    
    # Single replica deployments - medium
    single_replica = graph.get("single_replica_deployments", [])
    for dep_name in single_replica:
        # Find namespace
        for dep in deployments:
            if dep["name"] == dep_name:
                resource = f"deployment/{dep['namespace']}/{dep_name}"
                _add_signal(
                    signals, seen, "reliability", "medium", resource,
                    "Deployment has only 1 replica (no redundancy)"
                )
                break
    
    # Orphan services - medium
    orphan_services = graph.get("orphan_services", [])
    for svc_name in orphan_services:
        # Find namespace
        for svc in snapshot["services"]:
            if svc["name"] == svc_name:
                resource = f"service/{svc['namespace']}/{svc_name}"
                _add_signal(
                    signals, seen, "reliability", "medium", resource,
                    "Service has no matching deployments"
                )
                break
    
    return signals


def _generate_security_signals(
    snapshot: Dict[str, Any],
    seen: Set[Tuple[str, str, str]]
) -> List[Dict[str, Any]]:
    """Generate security-related signals."""
    signals = []
    deployments = snapshot["deployments"]
    
    for dep in deployments:
        resource = f"deployment/{dep['namespace']}/{dep['name']}"
        
        for container in dep.get("containers", []):
            # Privileged containers - critical
            if container.get("privileged"):
                _add_signal(
                    signals, seen, "security", "critical", resource,
                    f"Container {container['name']} runs in privileged mode"
                )
            
            # :latest tag - high
            image = container.get("image", "")
            if image.endswith(":latest") or ":" not in image:
                _add_signal(
                    signals, seen, "security", "high", resource,
                    f"Container {container['name']} uses :latest or untagged image"
                )
            
            # Missing resource limits - medium
            limits = container.get("limits", {})
            if not limits:
                _add_signal(
                    signals, seen, "security", "medium", resource,
                    f"Container {container['name']} has no resource limits (security risk)"
                )
    
    return signals


def _generate_cost_signals(
    snapshot: Dict[str, Any],
    seen: Set[Tuple[str, str, str]]
) -> List[Dict[str, Any]]:
    """Generate cost-related signals."""
    signals = []
    deployments = snapshot["deployments"]
    
    for dep in deployments:
        resource = f"deployment/{dep['namespace']}/{dep['name']}"
        replicas = dep.get("replicas", 1)
        
        # High replica count - low
        if replicas > 3:
            _add_signal(
                signals, seen, "cost", "low", resource,
                f"Deployment has {replicas} replicas (may be over-provisioned)"
            )
        
        for container in dep.get("containers", []):
            # Missing limits - medium
            limits = container.get("limits", {})
            if not limits:
                _add_signal(
                    signals, seen, "cost", "medium", resource,
                    f"Container {container['name']} has no resource limits (cost risk)"
                )
            
            # Request/limit mismatch - low
            requests = container.get("requests", {})
            if requests and limits:
                # Check CPU mismatch
                try:
                    cpu_req = requests.get("cpu", "")
                    cpu_lim = limits.get("cpu", "")
                    if cpu_req and cpu_lim:
                        # Simple string comparison (proper parsing would be complex)
                        if cpu_req != cpu_lim:
                            _add_signal(
                                signals, seen, "cost", "low", resource,
                                f"Container {container['name']} has CPU request/limit mismatch"
                            )
                except Exception:
                    pass
    
    return signals
