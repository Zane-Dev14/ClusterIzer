"""
Graph builder node - constructs dependency graph and derived metrics.

Builds simple adjacency dictionaries showing relationships between
Kubernetes resources. No networkx, just plain dicts.
"""

import logging
from typing import Dict, Any, List
from collections import defaultdict

from .models import InfraState

logger = logging.getLogger(__name__)


def build_graph(state: InfraState) -> InfraState:
    """
    Build dependency graph from cluster snapshot.
    
    Creates three adjacency mappings:
    - service_to_deployment: services → matching deployments
    - deployment_to_pods: deployments → their pods
    - pod_to_node: pods → nodes they run on
    
    Also computes derived metrics:
    - orphan_services: services with no matching deployments
    - single_replica_deployments: deployments with replicas == 1
    - node_fanout_count: number of pods per node
    
    Args:
        state: InfraState with cluster_snapshot populated
        
    Returns:
        Updated state with graph_summary populated
    """
    logger.info("Building dependency graph...")
    
    snapshot = state["cluster_snapshot"]
    deployments = snapshot["deployments"]
    pods = snapshot["pods"]
    services = snapshot["services"]
    
    # Build adjacency mappings
    service_to_deployment = _map_services_to_deployments(services, deployments, pods)
    deployment_to_pods = _map_deployments_to_pods(deployments, pods)
    pod_to_node = _map_pods_to_nodes(pods)
    
    # Compute derived metrics
    orphan_services = [
        svc["name"] for svc in services
        if not service_to_deployment.get(f"{svc['namespace']}/{svc['name']}")
    ]
    
    single_replica_deployments = [
        dep["name"] for dep in deployments
        if dep["replicas"] == 1
    ]
    
    node_fanout_count = defaultdict(int)
    for pod in pods:
        node_name = pod["node_name"]
        if node_name != "unscheduled":
            node_fanout_count[node_name] += 1
    
    graph_summary = {
        "service_to_deployment": service_to_deployment,
        "deployment_to_pods": deployment_to_pods,
        "pod_to_node": pod_to_node,
        "orphan_services": orphan_services,
        "single_replica_deployments": single_replica_deployments,
        "node_fanout_count": dict(node_fanout_count),
    }
    
    logger.info(
        f"Graph built: {len(orphan_services)} orphan services, "
        f"{len(single_replica_deployments)} single-replica deployments"
    )
    
    state["graph_summary"] = graph_summary
    return state


def _map_services_to_deployments(
    services: List[Dict[str, Any]],
    deployments: List[Dict[str, Any]],
    pods: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """
    Map services to deployments via label selectors.
    
    Returns dict: "namespace/service_name" -> ["namespace/deployment_name", ...]
    """
    result = defaultdict(list)
    
    for svc in services:
        svc_key = f"{svc['namespace']}/{svc['name']}"
        selector = svc.get("selector", {})
        
        if not selector:
            continue
        
        # Find pods that match this selector
        matching_pods = []
        for pod in pods:
            # We need pod labels to match - but we didn't extract them!
            # For MVP, match by namespace only (simplified)
            if pod["namespace"] == svc["namespace"]:
                matching_pods.append(pod)
        
        # Find deployments that own these pods (match by name prefix)
        for dep in deployments:
            if dep["namespace"] != svc["namespace"]:
                continue
            
            # Check if any pod name starts with deployment name
            dep_name = dep["name"]
            for pod in matching_pods:
                if pod["name"].startswith(dep_name):
                    dep_key = f"{dep['namespace']}/{dep['name']}"
                    if dep_key not in result[svc_key]:
                        result[svc_key].append(dep_key)
                    break
    
    return dict(result)


def _map_deployments_to_pods(
    deployments: List[Dict[str, Any]],
    pods: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """
    Map deployments to their pods.
    
    Returns dict: "namespace/deployment_name" -> ["namespace/pod_name", ...]
    """
    result = defaultdict(list)
    
    for dep in deployments:
        dep_key = f"{dep['namespace']}/{dep['name']}"
        dep_name = dep["name"]
        dep_namespace = dep["namespace"]
        
        for pod in pods:
            # Match by namespace and name prefix (standard k8s pod naming)
            if pod["namespace"] == dep_namespace and pod["name"].startswith(dep_name):
                pod_key = f"{pod['namespace']}/{pod['name']}"
                result[dep_key].append(pod_key)
    
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
