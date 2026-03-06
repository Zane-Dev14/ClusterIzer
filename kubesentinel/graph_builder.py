import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

from .models import InfraState

logger = logging.getLogger(__name__)

def build_graph(state: InfraState) -> InfraState:
    """Build dependency graph from cluster snapshot with ownership resolution."""
    logger.info("Building dependency graph...")
    snapshot = state.get("cluster_snapshot", {})
    deployments = snapshot.get("deployments", [])
    pods = snapshot.get("pods", [])
    services = snapshot.get("services", [])
    replicasets = snapshot.get("replicasets", [])
    statefulsets = snapshot.get("statefulsets", [])
    crds = snapshot.get("crds", {})
    
    # Build ownership index: pod -> replicaset -> deployment (or other controllers)
    ownership_index, broken_refs = _build_ownership_index(pods, replicasets, deployments, statefulsets)
    
    # Validate ownership index schema
    schema_errors = _validate_ownership_index_schema(ownership_index)
    if schema_errors:
        logger.warning(f"Ownership index schema issues: {schema_errors}")
    
    # Build CRD ownership chains
    crd_ownership = _build_crd_ownership_chains(crds)
    
    # Build adjacency mappings with proper label selector evaluation
    service_to_deployment = _map_services_to_deployments_via_labels(services, pods, ownership_index)
    deployment_to_pods = _map_deployments_to_pods_via_ownership(pods, ownership_index)
    orphan_services = [svc["name"] for svc in services if not service_to_deployment.get(f"{svc['namespace']}/{svc['name']}")]
    single_replica_deployments = [dep["name"] for dep in deployments if dep["replicas"] == 1]
    node_fanout_count = defaultdict(int)
    for pod in pods:
        if pod.get("node_name") != "unscheduled":
            node_fanout_count[pod["node_name"]] += 1
    
    graph_summary = {
        "service_to_deployment": service_to_deployment,
        "deployment_to_pods": deployment_to_pods,
        "pod_to_node": {f"{p['namespace']}/{p['name']}": p.get("node_name", "unscheduled") for p in pods},
        "ownership_index": ownership_index,
        "crd_ownership": crd_ownership,
        "orphan_services": orphan_services,
        "single_replica_deployments": single_replica_deployments,
        "node_fanout_count": dict(node_fanout_count),
        "broken_ownership_refs": broken_refs,
        "schema_validation_errors": schema_errors
    }
    
    logger.info(
        f"Graph built: {len(orphan_services)} orphan services, "
        f"{len(single_replica_deployments)} single-replica deployments, "
        f"{len(ownership_index)} ownership chains, "
        f"{len(broken_refs)} broken references, "
        f"{len(crd_ownership)} CRD ownership chains"
    )
    
    state["graph_summary"] = graph_summary
    return state

def _build_ownership_index(pods: List[Dict[str, Any]], replicasets: List[Dict[str, Any]], deployments: List[Dict[str, Any]], statefulsets: List[Dict[str, Any]] = None) -> tuple[Dict[str, Dict[str, Optional[str]]], List[Dict[str, Any]]]:
    """Build ownership index and detect broken references with full controller support.
    
    Returns:
        Tuple of (ownership_index, broken_references)
    """
    if statefulsets is None:
        statefulsets = []
    
    index = {}
    broken_refs = []
    
    # Build UID lookup maps with validation (skip resources with None/empty UIDs)
    rs_by_uid = {}
    for rs in replicasets:
        uid = rs.get("uid")
        if uid and isinstance(uid, str) and uid.strip():  # Validate UID exists and is non-empty
            rs_key = f"{rs['namespace']}/{rs['name']}"
            rs_by_uid[uid] = rs_key
    
    dep_by_uid = {}
    for dep in deployments:
        uid = dep.get("uid")
        if uid and isinstance(uid, str) and uid.strip():  # Validate UID exists and is non-empty
            dep_key = f"{dep['namespace']}/{dep['name']}"
            dep_by_uid[uid] = dep_key
    
    sts_by_uid = {}
    for sts in statefulsets:
        uid = sts.get("uid")
        if uid and isinstance(uid, str) and uid.strip():  # Validate UID exists and is non-empty
            sts_key = f"{sts['namespace']}/{sts['name']}"
            sts_by_uid[uid] = sts_key
    
    # Build ReplicaSet -> Deployment ownership
    rs_to_dep = {}
    for rs in replicasets:
        for owner in rs.get("owner_references", []):
            if owner.get("kind") == "Deployment":
                owner_uid = owner.get("uid")
                if owner_uid and rs.get("uid"):  # Validate both UIDs are present
                    if owner_uid in dep_by_uid:
                        rs_to_dep[f"{rs['namespace']}/{rs['name']}"] = dep_by_uid[owner_uid]
                    else:
                        # Broken reference: ReplicaSet references non-existent Deployment
                        broken_refs.append({
                            "resource_type": "replicaset",
                            "resource_name": f"{rs['namespace']}/{rs['name']}",
                            "missing_owner_kind": "Deployment",
                            "missing_owner_uid": owner_uid
                        })
                    break
    
    # Build Pod -> ReplicaSet/Deployment/StatefulSet ownership chain
    for pod in pods:
        pod_key = f"{pod['namespace']}/{pod['name']}"
        chain: Dict[str, Optional[str]] = {
            "replicaset": None,
            "deployment": None,
            "statefulset": None,
            "top_controller": None
        }
        
        for owner in pod.get("owner_references", []):
            owner_kind = owner.get("kind")
            owner_uid = owner.get("uid")
            
            # Validate owner reference has required fields
            if not owner_kind or not owner_uid:
                continue
            
            # Handle ReplicaSet ownership (usually from Deployments)
            if owner_kind == "ReplicaSet":
                if owner_uid in rs_by_uid:
                    rs_key = rs_by_uid[owner_uid]
                    chain["replicaset"] = rs_key
                    
                    # Follow ReplicaSet -> Deployment
                    if rs_key in rs_to_dep:
                        dep_key = rs_to_dep[rs_key]
                        chain["deployment"] = dep_key
                        chain["top_controller"] = dep_key
                    else:
                        # Orphaned ReplicaSet (no deployment)
                        chain["top_controller"] = rs_key
                else:
                    # Broken reference: Pod references non-existent ReplicaSet
                    broken_refs.append({
                        "resource_type": "pod",
                        "resource_name": pod_key,
                        "missing_owner_kind": "ReplicaSet",
                        "missing_owner_uid": owner_uid
                    })
                break
            
            # Handle Deployment ownership (rare but possible)
            elif owner_kind == "Deployment":
                if owner_uid in dep_by_uid:
                    dep_key = dep_by_uid[owner_uid]
                    chain["deployment"] = dep_key
                    chain["top_controller"] = dep_key
                else:
                    # Broken reference: Pod references non-existent Deployment
                    broken_refs.append({
                        "resource_type": "pod",
                        "resource_name": pod_key,
                        "missing_owner_kind": "Deployment",
                        "missing_owner_uid": owner_uid
                    })
                break
            
            # Handle StatefulSet ownership
            elif owner_kind == "StatefulSet":
                if owner_uid in sts_by_uid:
                    sts_key = sts_by_uid[owner_uid]
                    chain["statefulset"] = sts_key
                    chain["top_controller"] = sts_key
                else:
                    # Broken reference: Pod references non-existent StatefulSet
                    broken_refs.append({
                        "resource_type": "pod",
                        "resource_name": pod_key,
                        "missing_owner_kind": "StatefulSet",
                        "missing_owner_uid": owner_uid
                    })
                break
            
            # Handle other controllers (DaemonSet, Job, CronJob)
            elif owner_kind in ["DaemonSet", "Job", "CronJob"]:
                owner_name = owner.get("name", "unknown")
                chain["top_controller"] = f"{pod['namespace']}/{owner_name}"
                break
        
        # Only add to index if we have identified a top controller
        if chain["top_controller"]:
            index[pod_key] = chain
    
    return index, broken_refs

def _validate_ownership_index_schema(ownership_index: Dict[str, Dict[str, Optional[str]]]) -> List[str]:
    """
    Validate that ownership_index entries have proper schema.
    
    Args:
        ownership_index: The ownership index to validate
    
    Returns:
        List of schema validation errors (empty if valid)
    """
    errors = []
    
    for pod_key, chain in ownership_index.items():
        # Check required fields exist
        required_fields = ["replicaset", "deployment", "statefulset", "top_controller"]
        for field in required_fields:
            if field not in chain:
                errors.append(f"Pod {pod_key}: missing field '{field}'")
        
        # Check types
        if not isinstance(chain, dict):
            errors.append(f"Pod {pod_key}: chain is not a dict")
            continue
        
        # Check top_controller is not empty
        if not chain.get("top_controller"):
            errors.append(f"Pod {pod_key}: top_controller is empty")
        
        # Check all fields are either None or strings
        for field, value in chain.items():
            if value is not None and not isinstance(value, str):
                errors.append(f"Pod {pod_key}: field '{field}' is {type(value).__name__}, expected None or str")
    
    return errors


def _build_crd_ownership_chains(crds: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """
    Build ownership chains for Custom Resources (CRDs).
    
    Args:
        crds: Dictionary of CRD resources from discovery
    
    Returns:
        Dictionary mapping CRD resource keys to their ownership information
    """
    crd_ownership = {}
    
    for crd_group, crd_resources in crds.items():
        for resource in crd_resources:
            resource_key = f"{crd_group}/{resource['namespace']}/{resource['name']}"
            
            # Extract ownership information
            owner_refs = resource.get("owner_references", [])
            top_owner = None
            
            if owner_refs:
                # Get first owner (usually the controller)
                first_owner = owner_refs[0]
                top_owner = f"{resource['namespace']}/{first_owner.get('name', 'unknown')}"
            
            crd_ownership[resource_key] = {
                "kind": resource.get("kind"),
                "namespace": resource.get("namespace"),
                "name": resource.get("name"),
                "owner_references": owner_refs,
                "top_owner": top_owner,
                "metadata": {
                    "creation_timestamp": resource.get("creation_timestamp"),
                    "deletion_timestamp": resource.get("deletion_timestamp"),
                    "uid": resource.get("uid")
                }
            }
    
    return crd_ownership


def _map_services_to_deployments_via_labels(services: List[Dict[str, Any]], pods: List[Dict[str, Any]], ownership_index: Dict[str, Dict[str, Optional[str]]]) -> Dict[str, List[str]]:
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

def _map_deployments_to_pods_via_ownership(pods: List[Dict[str, Any]], ownership_index: Dict[str, Dict[str, Optional[str]]]) -> Dict[str, List[str]]:
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
    if not selector:
        return False
    
    for key, value in selector.items():
        if labels.get(key) != value:
            return False
    
    return True
