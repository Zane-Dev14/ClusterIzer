"""Graph builder - constructs dependency graph and derived metrics."""
import logging
from typing import Dict, Any, List
from collections import defaultdict

from .models import InfraState

logger = logging.getLogger(__name__)

 
def build_graph(state: InfraState) -> InfraState:
    """Build dependency graph from cluster snapshot."""
    logger.info("Building dependency graph...")
    snapshot = state["cluster_snapshot"]
    deployments, pods, services = snapshot["deployments"], snapshot["pods"], snapshot["services"]
    
    # Build adjacency mappings
    service_to_deployment = _map_services_to_deployments(services, deployments, pods)
    deployment_to_pods = _map_deployments_to_pods(deployments, pods)
    orphan_services = [svc["name"] for svc in services if not service_to_deployment.get(f"{svc['namespace']}/{svc['name']}")]
    single_replica_deployments = [dep["name"] for dep in deployments if dep["replicas"] == 1]
    node_fanout_count = defaultdict(int)
    for pod in pods:
        if pod["node_name"] != "unscheduled":
            node_fanout_count[pod["node_name"]] += 1
    graph_summary = {"service_to_deployment": service_to_deployment, "deployment_to_pods": deployment_to_pods, "pod_to_node": {f"{p['namespace']}/{p['name']}": p["node_name"] for p in pods}, "orphan_services": orphan_services, "single_replica_deployments": single_replica_deployments, "node_fanout_count": dict(node_fanout_count)}
    
    logger.info(
        f"Graph built: {len(orphan_services)} orphan services, "
        f"{len(single_replica_deployments)} single-replica deployments"
    )
    
    state["graph_summary"] = graph_summary
    return state


def _map_services_to_deployments(services: List[Dict[str, Any]], deployments: List[Dict[str, Any]], pods: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map services to deployments via label selectors."""
    result = defaultdict(list)
    for svc in services:
        svc_key, selector = f"{svc['namespace']}/{svc['name']}", svc.get("selector", {})
        if not selector:
            continue
        matching_pods = [pod for pod in pods if pod["namespace"] == svc["namespace"]]
        for dep in deployments:
            if dep["namespace"] != svc["namespace"]:
                continue
            for pod in matching_pods:
                if pod["name"].startswith(dep["name"]):
                    dep_key = f"{dep['namespace']}/{dep['name']}"
                    if dep_key not in result[svc_key]:
                        result[svc_key].append(dep_key)
                    break
    return dict(result)


def _map_deployments_to_pods(deployments: List[Dict[str, Any]], pods: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map deployments to their pods."""
    result = defaultdict(list)
    for dep in deployments:
        dep_key = f"{dep['namespace']}/{dep['name']}"
        for pod in pods:
            if pod["namespace"] == dep["namespace"] and pod["name"].startswith(dep["name"]):
                result[dep_key].append(f"{pod['namespace']}/{pod['name']}")
    return dict(result)


def _map_pods_to_nodes(pods: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    Map pods to nodes.
    
    Returns dict: "namespace/pod_name" -> "node_name"
    """
    result = {}
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        result[pod_key] = pod["node_name"]
    return result
