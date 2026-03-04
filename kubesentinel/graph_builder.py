"""Graph builder - constructs dependency graph and derived metrics."""
import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

from .models import InfraState

logger = logging.getLogger(__name__)

 
def build_graph(state: InfraState) -> InfraState:
    """Build dependency graph from cluster snapshot with ownership resolution."""
    logger.info("Building dependency graph...")
    snapshot = state["cluster_snapshot"]
    deployments = snapshot["deployments"]
    pods = snapshot["pods"]
    services = snapshot["services"]
    replicasets = snapshot.get("replicasets", [])
    
    # Build ownership index: pod -> replicaset -> deployment
    ownership_index = _build_ownership_index(pods, replicasets, deployments)
    
    # Build adjacency mappings with proper label selector evaluation
    service_to_deployment = _map_services_to_deployments_via_labels(services, pods, ownership_index)
    deployment_to_pods = _map_deployments_to_pods_via_ownership(pods, ownership_index)
    orphan_services = [svc["name"] for svc in services if not service_to_deployment.get(f"{svc['namespace']}/{svc['name']}")]
    single_replica_deployments = [dep["name"] for dep in deployments if dep["replicas"] == 1]
    node_fanout_count = defaultdict(int)
    for pod in pods:
        if pod["node_name"] != "unscheduled":
            node_fanout_count[pod["node_name"]] += 1
    graph_summary = {"service_to_deployment": service_to_deployment, "deployment_to_pods": deployment_to_pods, "pod_to_node": {f"{p['namespace']}/{p['name']}": p["node_name"] for p in pods}, "ownership_index": ownership_index, "orphan_services": orphan_services, "single_replica_deployments": single_replica_deployments, "node_fanout_count": dict(node_fanout_count)}
    
    logger.info(
        f"Graph built: {len(orphan_services)} orphan services, "
        f"{len(single_replica_deployments)} single-replica deployments, "
        f"{len(ownership_index)} ownership chains"
    )
    
    state["graph_summary"] = graph_summary
    return state


def _build_ownership_index(pods: List[Dict[str, Any]], replicasets: List[Dict[str, Any]], deployments: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """
    Build ownership chain index: pod_key -> {replicaset, deployment, top_controller}.
    
    Uses ownerReferences to resolve: Pod -> ReplicaSet -> Deployment.
    Falls back gracefully if UID/ownerReferences are missing (older clusters or test fixtures).
    """
    index = {}
    
    # Build UID lookup maps (skip resources without UIDs)
    rs_by_uid = {rs["uid"]: f"{rs['namespace']}/{rs['name']}" for rs in replicasets if "uid" in rs}
    dep_by_uid = {dep["uid"]: f"{dep['namespace']}/{dep['name']}" for dep in deployments if "uid" in dep}
    
    # Build ReplicaSet -> Deployment ownership
    rs_to_dep = {}
    for rs in replicasets:
        for owner in rs.get("owner_references", []):
            if owner.get("kind") == "Deployment":
                owner_uid = owner.get("uid")
                if owner_uid and owner_uid in dep_by_uid:
                    rs_to_dep[f"{rs['namespace']}/{rs['name']}"] = dep_by_uid[owner_uid]
                    break
    
    # Build Pod -> ReplicaSet -> Deployment chain
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        chain: Dict[str, Optional[str]] = {"replicaset": None, "deployment": None, "top_controller": None}
        
        for owner in pod.get("owner_references", []):
            owner_kind = owner.get("kind")
            owner_uid = owner.get("uid")
            
            if not owner_kind or not owner_uid:
                continue
            
            if owner_kind == "ReplicaSet" and owner_uid in rs_by_uid:
                rs_key = rs_by_uid[owner_uid]
                chain["replicaset"] = rs_key
                
                # Follow ReplicaSet -> Deployment
                if rs_key in rs_to_dep:
                    dep_key = rs_to_dep[rs_key]
                    chain["deployment"] = dep_key
                    chain["top_controller"] = dep_key
                else:
                    chain["top_controller"] = rs_key  # Orphaned RS
                break
            
            elif owner_kind == "Deployment" and owner_uid in dep_by_uid:
                # Direct Deployment ownership (rare but possible)
                dep_key = dep_by_uid[owner_uid]
                chain["deployment"] = dep_key
                chain["top_controller"] = dep_key
                break
            
            elif owner_kind in ["StatefulSet", "DaemonSet", "Job", "CronJob"]:
                # Other controllers
                chain["top_controller"] = f"{pod['namespace']}/{owner.get('name', 'unknown')}"
                break
        
        if chain["top_controller"]:
            index[pod_key] = chain
    
    return index


def _map_services_to_deployments_via_labels(services: List[Dict[str, Any]], pods: List[Dict[str, Any]], ownership_index: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    """
    Map services to deployments via label selector evaluation.
    
    Uses proper label matching (not name prefix heuristics) and ownership chains.
    """
    result = defaultdict(list)
    
    for svc in services:
        svc_key = f"{svc['namespace']}/{svc['name']}"
        selector = svc.get("selector", {})
        
        if not selector:
            continue
        
        # Find pods matching service selector
        matching_pods = []
        for pod in pods:
            if pod["namespace"] != svc["namespace"]:
                continue
            
            pod_labels = pod.get("labels", {})
            if _labels_match_selector(pod_labels, selector):
                matching_pods.append(pod)
        
        # Resolve pods to their top controllers
        controllers = set()
        for pod in matching_pods:
            pod_key = f"{pod['namespace']}/{pod['name']}"
            if pod_key in ownership_index:
                top_controller = ownership_index[pod_key].get("top_controller")
                if top_controller:
                    controllers.add(top_controller)
        
        result[svc_key] = sorted(list(controllers))
    
    return dict(result)


def _map_deployments_to_pods_via_ownership(pods: List[Dict[str, Any]], ownership_index: Dict[str, Dict[str, str]]) -> Dict[str, List[str]]:
    """
    Map deployments to their pods using ownership index.
    Falls back to name prefix matching if ownership data is unavailable.
    """
    result = defaultdict(list)
    
    # First pass: use ownership index (accurate)
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        if pod_key in ownership_index:
            deployment = ownership_index[pod_key].get("deployment")
            if deployment:
                result[deployment].append(pod_key)
    
    # Second pass: fallback to name prefix heuristic for pods without ownership data
    # This handles test fixtures and scenarios without ownerReferences
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        if pod_key not in ownership_index:
            # Try to match by name prefix heuristic
            # Pod names typically follow patterns:
            # - deployment-abc123 (direct, strip 1 segment)
            # - deployment-rs_hash-pod_hash (via ReplicaSet, strip 2 segments)
            # Simple approach: strip last segment only (works for most cases)
            pod_name = pod["name"]
            namespace = pod["namespace"]
            
            if "-" in pod_name:
                # Strip last segment (hash)
                potential_dep_name = pod_name.rsplit("-", 1)[0]
                dep_key = f"{namespace}/{potential_dep_name}"
                result[dep_key].append(pod_key)
            else:
                # No hyphens, unlikely to be managed by deployment
                pass
    
    return dict(result)


def _labels_match_selector(labels: Dict[str, str], selector: Dict[str, str]) -> bool:
    """
    Check if pod labels match service selector.
    
    Supports matchLabels semantics (all selector labels must match).
    """
    if not selector:
        return False
    
    for key, value in selector.items():
        if labels.get(key) != value:
            return False
    
    return True
