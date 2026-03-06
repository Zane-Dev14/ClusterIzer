import logging
from typing import Dict, Any, List
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .models import InfraState, MAX_PODS, MAX_DEPLOYMENTS, MAX_SERVICES, MAX_NODES
from .crd_discovery import discover_crds

logger = logging.getLogger(__name__)

def scan_cluster(state: InfraState) -> InfraState:
    """Scan Kubernetes cluster and extract bounded state."""
    logger.info("Starting cluster scan...")
    target_namespace = state.get("target_namespace", None)
    try:
        config.load_kube_config()
        logger.info("Loaded kubeconfig")
    except Exception as e1:
        logger.warning(f"kubeconfig failed: {e1}")
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster config")
        except Exception as e2:
            raise RuntimeError(f"Unable to connect to cluster. kubeconfig: {e1}, in-cluster: {e2}")
    core_v1, apps_v1 = client.CoreV1Api(), client.AppsV1Api()
    
    # Fetch resources
    try:
        nodes_raw = core_v1.list_node(limit=MAX_NODES)
        
        # Filter by namespace if specified
        if target_namespace:
            pods_raw = core_v1.list_namespaced_pod(namespace=target_namespace, limit=MAX_PODS)
            deployments_raw = apps_v1.list_namespaced_deployment(namespace=target_namespace, limit=MAX_DEPLOYMENTS)
            services_raw = core_v1.list_namespaced_service(namespace=target_namespace, limit=MAX_SERVICES)
        else:
            pods_raw = core_v1.list_pod_for_all_namespaces(limit=MAX_PODS)
            deployments_raw = apps_v1.list_deployment_for_all_namespaces(limit=MAX_DEPLOYMENTS)
            services_raw = core_v1.list_service_for_all_namespaces(limit=MAX_SERVICES)
    except ApiException as e:
        raise RuntimeError(f"Failed to fetch cluster resources: {e}")
    nodes = _extract_nodes(nodes_raw.items[:MAX_NODES])
    deployments = _extract_deployments(deployments_raw.items[:MAX_DEPLOYMENTS])
    pods = _extract_pods(pods_raw.items[:MAX_PODS])
    services = _extract_services(services_raw.items[:MAX_SERVICES])
    
    # Fetch ReplicaSets for ownership resolution
    replicasets = []
    try:
        if target_namespace:
            replicasets_raw = apps_v1.list_namespaced_replica_set(namespace=target_namespace, limit=MAX_DEPLOYMENTS * 2)
        else:
            replicasets_raw = apps_v1.list_replica_set_for_all_namespaces(limit=MAX_DEPLOYMENTS * 2)
        replicasets = _extract_replicasets(replicasets_raw.items[:MAX_DEPLOYMENTS * 2])
    except ApiException as e:
        logger.warning(f"Failed to fetch ReplicaSets: {e}")
    
    # Fetch StatefulSets
    statefulsets = []
    try:
        if target_namespace:
            statefulsets_raw = apps_v1.list_namespaced_stateful_set(namespace=target_namespace, limit=MAX_DEPLOYMENTS)
        else:
            statefulsets_raw = apps_v1.list_stateful_set_for_all_namespaces(limit=MAX_DEPLOYMENTS)
        statefulsets = _extract_statefulsets(statefulsets_raw.items[:MAX_DEPLOYMENTS])
    except ApiException as e:
        logger.warning(f"Failed to fetch StatefulSets: {e}")
    
    # Fetch DaemonSets
    daemonsets = []
    try:
        if target_namespace:
            daemonsets_raw = apps_v1.list_namespaced_daemon_set(namespace=target_namespace, limit=MAX_DEPLOYMENTS)
        else:
            daemonsets_raw = apps_v1.list_daemon_set_for_all_namespaces(limit=MAX_DEPLOYMENTS)
        daemonsets = _extract_daemonsets(daemonsets_raw.items[:MAX_DEPLOYMENTS])
    except ApiException as e:
        logger.warning(f"Failed to fetch DaemonSets: {e}")
    
    # Discover Custom Resources (CRDs)
    crds = {}
    crd_errors = []
    try:
        crds, crd_errors = discover_crds(target_namespace)
        if crd_errors:
            for error in crd_errors:
                logger.debug(f"CRD discovery warning: {error}")
    except Exception as e:
        logger.warning(f"CRD discovery failed: {e}")
    
    logger.info(f"Scan complete: {len(nodes)} nodes, {len(deployments)} deps, {len(statefulsets)} sts, {len(daemonsets)} ds, {len(pods)} pods, {len(services)} svcs, {len(replicasets)} rs, {len(crds)} CRD groups")
    state["cluster_snapshot"] = {
        "nodes": nodes,
        "deployments": deployments,
        "statefulsets": statefulsets,
        "daemonsets": daemonsets,
        "pods": pods,
        "services": services,
        "replicasets": replicasets,
        "crds": crds
    }
    return state

def _extract_nodes(nodes: List[Any]) -> List[Dict[str, Any]]:
    """Extract node information with allocatable resources and instance metadata."""
    result = []
    for node in nodes:
        allocatable = node.status.allocatable or {}
        labels = dict(node.metadata.labels) if node.metadata.labels else {}
        # Normalize allocatable resources
        cpu_millicores = _parse_cpu_to_millicores(allocatable.get("cpu", "0"))
        memory_mib = _parse_memory_to_mib(allocatable.get("memory", "0"))
        
        # Extract node conditions for pressure detection
        conditions = {}
        if node.status.conditions:
            for condition in node.status.conditions:
                condition_type = condition.type
                condition_status = condition.status  # "True" or "False"
                conditions[condition_type] = (condition_status == "True")
        
        result.append({
            "name": node.metadata.name,
            "allocatable_cpu": allocatable.get("cpu", "unknown"),
            "allocatable_memory": allocatable.get("memory", "unknown"),
            "allocatable_cpu_millicores": cpu_millicores,
            "allocatable_memory_mib": memory_mib,
            "instance_type": labels.get("node.kubernetes.io/instance-type", labels.get("beta.kubernetes.io/instance-type", "unknown")),
            "labels": labels,
            "conditions": conditions
        })
    return result

def _extract_deployments(deployments: List[Any]) -> List[Dict[str, Any]]:
    """Extract deployment information with labels and normalized resources."""
    result = []
    for dep in deployments:
        replicas = dep.spec.replicas if dep.spec.replicas is not None else 1
        labels = dict(dep.metadata.labels) if dep.metadata.labels else {}
        pod_labels = dict(dep.spec.template.metadata.labels) if dep.spec.template.metadata.labels else {}
        selector = dep.spec.selector.match_labels if dep.spec.selector and dep.spec.selector.match_labels else {}
        containers = []
        for container in (dep.spec.template.spec.containers or []):
            privileged = container.security_context.privileged or False if container.security_context else False
            requests, limits = {}, {}
            if container.resources:
                requests = dict(container.resources.requests) if container.resources.requests else {}
                limits = dict(container.resources.limits) if container.resources.limits else {}
            # Normalize resources
            cpu_req_millicores = _parse_cpu_to_millicores(requests.get("cpu", "0"))
            mem_req_mib = _parse_memory_to_mib(requests.get("memory", "0"))
            cpu_lim_millicores = _parse_cpu_to_millicores(limits.get("cpu", "0"))
            mem_lim_mib = _parse_memory_to_mib(limits.get("memory", "0"))
            containers.append({
                "name": container.name,
                "image": container.image,
                "privileged": privileged,
                "requests": requests,
                "limits": limits,
                "requests_cpu_millicores": cpu_req_millicores,
                "requests_memory_mib": mem_req_mib,
                "limits_cpu_millicores": cpu_lim_millicores,
                "limits_memory_mib": mem_lim_mib
            })
        result.append({
            "name": dep.metadata.name,
            "namespace": dep.metadata.namespace,
            "replicas": replicas,
            "labels": labels,
            "pod_labels": pod_labels,
            "selector": selector,
            "containers": containers
        })
    return result

def _extract_pods(pods: List[Any]) -> List[Dict[str, Any]]:
    """Extract pod information with labels, ownerReferences, and status."""
    result = []
    for pod in pods:
        crash_loop, container_statuses = False, []
        labels = dict(pod.metadata.labels) if pod.metadata.labels else {}
        owner_refs = []
        if pod.metadata.owner_references:
            for owner in pod.metadata.owner_references:
                owner_refs.append({
                    "kind": owner.kind,
                    "name": owner.name,
                    "uid": owner.uid,
                    "controller": owner.controller if hasattr(owner, 'controller') else False
                })
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                status_dict = {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count}
                if cs.state and cs.state.waiting:
                    reason = cs.state.waiting.reason or ""
                    status_dict["state"] = reason
                    if reason == "CrashLoopBackOff":
                        crash_loop = True
                elif cs.state and cs.state.running:
                    status_dict["state"] = "Running"
                elif cs.state and cs.state.terminated:
                    status_dict["state"] = cs.state.terminated.reason or "Terminated"
                else:
                    status_dict["state"] = "Unknown"
                container_statuses.append(status_dict)
        result.append({
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "node_name": pod.spec.node_name or "unscheduled",
            "labels": labels,
            "owner_references": owner_refs,
            "crash_loop_backoff": crash_loop,
            "container_statuses": container_statuses
        })
    return result

def _extract_services(services: List[Any]) -> List[Dict[str, Any]]:
    """Extract service information with selector details."""
    return [{"name": svc.metadata.name, "namespace": svc.metadata.namespace, "type": svc.spec.type, "selector": dict(svc.spec.selector) if svc.spec.selector else {}} for svc in services]

def _extract_replicasets(replicasets: List[Any]) -> List[Dict[str, Any]]:
    """Extract ReplicaSet information with ownerReferences."""
    result = []
    for rs in replicasets:
        owner_refs = []
        if rs.metadata.owner_references:
            for owner in rs.metadata.owner_references:
                owner_refs.append({
                    "kind": owner.kind,
                    "name": owner.name,
                    "uid": owner.uid,
                    "controller": owner.controller if hasattr(owner, 'controller') else False
                })
        result.append({
            "name": rs.metadata.name,
            "namespace": rs.metadata.namespace,
            "uid": rs.metadata.uid,
            "owner_references": owner_refs
        })
    return result

def _extract_statefulsets(statefulsets: List[Any]) -> List[Dict[str, Any]]:
    """Extract StatefulSet information (ordered, persistent identity)."""
    result = []
    for sts in statefulsets:
        replicas = sts.spec.replicas if sts.spec.replicas is not None else 1
        labels = dict(sts.metadata.labels) if sts.metadata.labels else {}
        pod_labels = dict(sts.spec.template.metadata.labels) if sts.spec.template.metadata.labels else {}
        selector = sts.spec.selector.match_labels if sts.spec.selector and sts.spec.selector.match_labels else {}
        containers = []
        for container in (sts.spec.template.spec.containers or []):
            privileged = container.security_context.privileged or False if container.security_context else False
            requests, limits = {}, {}
            if container.resources:
                requests = dict(container.resources.requests) if container.resources.requests else {}
                limits = dict(container.resources.limits) if container.resources.limits else {}
            cpu_req_millicores = _parse_cpu_to_millicores(requests.get("cpu", "0"))
            mem_req_mib = _parse_memory_to_mib(requests.get("memory", "0"))
            cpu_lim_millicores = _parse_cpu_to_millicores(limits.get("cpu", "0"))
            mem_lim_mib = _parse_memory_to_mib(limits.get("memory", "0"))
            containers.append({
                "name": container.name,
                "image": container.image,
                "privileged": privileged,
                "requests": requests,
                "limits": limits,
                "requests_cpu_millicores": cpu_req_millicores,
                "requests_memory_mib": mem_req_mib,
                "limits_cpu_millicores": cpu_lim_millicores,
                "limits_memory_mib": mem_lim_mib
            })
        result.append({
            "name": sts.metadata.name,
            "namespace": sts.metadata.namespace,
            "uid": sts.metadata.uid,
            "replicas": replicas,
            "labels": labels,
            "pod_labels": pod_labels,
            "selector": selector,
            "containers": containers,
            "service_name": sts.spec.service_name or None,
            "controller_type": "StatefulSet"
        })
    return result

def _extract_daemonsets(daemonsets: List[Any]) -> List[Dict[str, Any]]:
    """Extract DaemonSet information (one pod per node)."""
    result = []
    for ds in daemonsets:
        labels = dict(ds.metadata.labels) if ds.metadata.labels else {}
        pod_labels = dict(ds.spec.template.metadata.labels) if ds.spec.template.metadata.labels else {}
        selector = ds.spec.selector.match_labels if ds.spec.selector and ds.spec.selector.match_labels else {}
        containers = []
        for container in (ds.spec.template.spec.containers or []):
            privileged = container.security_context.privileged or False if container.security_context else False
            requests, limits = {}, {}
            if container.resources:
                requests = dict(container.resources.requests) if container.resources.requests else {}
                limits = dict(container.resources.limits) if container.resources.limits else {}
            cpu_req_millicores = _parse_cpu_to_millicores(requests.get("cpu", "0"))
            mem_req_mib = _parse_memory_to_mib(requests.get("memory", "0"))
            cpu_lim_millicores = _parse_cpu_to_millicores(limits.get("cpu", "0"))
            mem_lim_mib = _parse_memory_to_mib(limits.get("memory", "0"))
            containers.append({
                "name": container.name,
                "image": container.image,
                "privileged": privileged,
                "requests": requests,
                "limits": limits,
                "requests_cpu_millicores": cpu_req_millicores,
                "requests_memory_mib": mem_req_mib,
                "limits_cpu_millicores": cpu_lim_millicores,
                "limits_memory_mib": mem_lim_mib
            })
        result.append({
            "name": ds.metadata.name,
            "namespace": ds.metadata.namespace,
            "uid": ds.metadata.uid,
            "labels": labels,
            "pod_labels": pod_labels,
            "selector": selector,
            "containers": containers,
            "update_strategy": ds.spec.update_strategy.type if ds.spec.update_strategy else None,
            "controller_type": "DaemonSet"
        })
    return result

def _parse_cpu_to_millicores(cpu_str: str) -> int:
    """Parse Kubernetes CPU string to millicores."""
    if not cpu_str or cpu_str == "0" or cpu_str == "unknown":
        return 0
    cpu_str = str(cpu_str)
    if cpu_str.endswith("m"):
        return int(cpu_str[:-1])
    try:
        return int(float(cpu_str) * 1000)
    except (ValueError, TypeError):
        return 0

def _parse_memory_to_mib(mem_str: str) -> int:
    """Parse Kubernetes memory string to MiB."""
    if not mem_str or mem_str == "0" or mem_str == "unknown":
        return 0
    mem_str = str(mem_str)
    # Handle different units
    units = {"Ki": 1/1024, "Mi": 1, "Gi": 1024, "Ti": 1024*1024,
             "K": 1/1024/1.024, "M": 1/1.024, "G": 1024/1.024, "T": 1024*1024/1.024}
    for unit, multiplier in units.items():
        if mem_str.endswith(unit):
            try:
                return int(float(mem_str[:-len(unit)]) * multiplier)
            except (ValueError, TypeError):
                return 0
    try:
        return int(float(mem_str) / (1024 * 1024))
    except (ValueError, TypeError):
        return 0
