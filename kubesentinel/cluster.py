"""Cluster snapshot node - deterministic Kubernetes state extraction."""
import logging
from typing import Dict, Any, List
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from .models import InfraState, MAX_PODS, MAX_DEPLOYMENTS, MAX_SERVICES, MAX_NODES

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
    logger.info(f"Scan complete: {len(nodes)} nodes, {len(deployments)} deployments, {len(pods)} pods, {len(services)} services")
    state["cluster_snapshot"] = {"nodes": nodes, "deployments": deployments, "pods": pods, "services": services}
    return state


def _extract_nodes(nodes: List[Any]) -> List[Dict[str, Any]]:
    """Extract slim node information."""
    result = []
    for node in nodes:
        allocatable = node.status.allocatable or {}
        result.append({"name": node.metadata.name, "allocatable_cpu": allocatable.get("cpu", "unknown"), "allocatable_memory": allocatable.get("memory", "unknown")})
    return result


def _extract_deployments(deployments: List[Any]) -> List[Dict[str, Any]]:
    """Extract slim deployment information."""
    result = []
    for dep in deployments:
        replicas = dep.spec.replicas if dep.spec.replicas is not None else 1
        containers = []
        for container in (dep.spec.template.spec.containers or []):
            privileged = container.security_context.privileged or False if container.security_context else False
            requests, limits = {}, {}
            if container.resources:
                requests = dict(container.resources.requests) if container.resources.requests else {}
                limits = dict(container.resources.limits) if container.resources.limits else {}
            containers.append({"name": container.name, "image": container.image, "privileged": privileged, "requests": requests, "limits": limits})
        result.append({"name": dep.metadata.name, "namespace": dep.metadata.namespace, "replicas": replicas, "containers": containers})
    return result


def _extract_pods(pods: List[Any]) -> List[Dict[str, Any]]:
    """Extract slim pod information with CrashLoopBackOff detection."""
    result = []
    for pod in pods:
        crash_loop, container_statuses = False, []
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                status_dict = {"name": cs.name, "ready": cs.ready, "restart_count": cs.restart_count}
                
                # Check for CrashLoopBackOff
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
        result.append({"name": pod.metadata.name, "namespace": pod.metadata.namespace, "phase": pod.status.phase, "node_name": pod.spec.node_name or "unscheduled", "crash_loop_backoff": crash_loop, "container_statuses": container_statuses})
    return result


def _extract_services(services: List[Any]) -> List[Dict[str, Any]]:
    """Extract slim service information."""
    return [{"name": svc.metadata.name, "namespace": svc.metadata.namespace, "type": svc.spec.type, "selector": dict(svc.spec.selector) if svc.spec.selector else {}} for svc in services]
