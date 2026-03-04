"""Cost modeling engine - compute cluster costs and overcommit detection."""
import logging
from typing import Dict, Any, List
from .models import InfraState

logger = logging.getLogger(__name__)

# Cloud provider pricing (USD per hour) - defaults approximating typical small instance costs
# Users should override these via configuration
DEFAULT_PRICE_MAP = {
    "aws": {
        "t3.medium": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.0416},
        "t3.large": {"vcpu": 2, "ram_gb": 8, "price_hour": 0.0832},
        "m5.large": {"vcpu": 2, "ram_gb": 8, "price_hour": 0.096},
        "m5.xlarge": {"vcpu": 4, "ram_gb": 16, "price_hour": 0.192},
        "default": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.05}
    },
    "gcp": {
        "n1-standard-2": {"vcpu": 2, "ram_gb": 7.5, "price_hour": 0.095},
        "n1-standard-4": {"vcpu": 4, "ram_gb": 15, "price_hour": 0.19},
        "e2-medium": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.0335},
        "default": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.05}
    },
    "azure": {
        "Standard_B2s": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.0416},
        "Standard_D2s_v3": {"vcpu": 2, "ram_gb": 8, "price_hour": 0.096},
        "Standard_D4s_v3": {"vcpu": 4, "ram_gb": 16, "price_hour": 0.192},
        "default": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.05}
    },
    "default": {"vcpu": 2, "ram_gb": 4, "price_hour": 0.05}
}


def compute_cluster_cost(state: InfraState) -> Dict[str, Any]:
    """
    Compute cluster cost estimates and overcommit analysis.
    
    Returns cost summary with:
    - total_estimated_cost_per_hour
    - per_pod_costs
    - overcommit_warnings
    - capacity_analysis
    """
    logger.info("Computing cluster costs...")
    
    snapshot = state["cluster_snapshot"]
    nodes = snapshot.get("nodes", [])
    pods = snapshot.get("pods", [])
    deployments = snapshot.get("deployments", [])
    
    # Build node cost map
    node_costs = {}
    total_node_capacity_cpu = 0
    total_node_capacity_mem = 0
    
    for node in nodes:
        instance_type = node.get("instance_type", "unknown")
        cpu_millicores = node.get("allocatable_cpu_millicores", 0)
        mem_mib = node.get("allocatable_memory_mib", 0)
        
        # Detect provider from instance type naming convention
        provider = _detect_provider(instance_type)
        price_entry = _get_price_entry(provider, instance_type)
        
        node_costs[node["name"]] = {
            "instance_type": instance_type,
            "cpu_millicores": cpu_millicores,
            "memory_mib": mem_mib,
            "price_per_hour": price_entry["price_hour"],
            "vcpu": price_entry["vcpu"],
            "ram_gb": price_entry["ram_gb"]
        }
        
        total_node_capacity_cpu += cpu_millicores
        total_node_capacity_mem += mem_mib
    
    # Build deployment resource map
    deployment_resources = {}
    for dep in deployments:
        total_cpu = 0
        total_mem = 0
        for container in dep.get("containers", []):
            total_cpu += container.get("requests_cpu_millicores", 0)
            total_mem += container.get("requests_memory_mib", 0)
        deployment_resources[f"{dep['namespace']}/{dep['name']}"] = {
            "cpu_millicores": total_cpu,
            "memory_mib": total_mem,
            "replicas": dep.get("replicas", 1)
        }
    
    # Compute per-pod costs
    pod_costs = []
    total_requested_cpu = 0
    total_requested_mem = 0
    
    for pod in pods:
        node_name = pod.get("node_name", "unscheduled")
        if node_name == "unscheduled" or node_name not in node_costs:
            continue
        
        # Find deployment for this pod (simple heuristic: name prefix match)
        pod_cpu = 0
        pod_mem = 0
        for dep_key, resources in deployment_resources.items():
            dep_name = dep_key.split("/")[1]
            if pod["name"].startswith(dep_name):
                # Distribute deployment resources across replicas
                replicas = resources["replicas"] or 1
                pod_cpu = resources["cpu_millicores"] / replicas
                pod_mem = resources["memory_mib"] / replicas
                break
        
        total_requested_cpu += pod_cpu
        total_requested_mem += pod_mem
        
        # Compute fractional cost based on resource allocation
        node = node_costs[node_name]
        node_cpu = node["cpu_millicores"]
        node_mem = node["memory_mib"]
        
        cpu_fraction = pod_cpu / node_cpu if node_cpu > 0 else 0
        mem_fraction = pod_mem / node_mem if node_mem > 0 else 0
        fraction = max(cpu_fraction, mem_fraction)  # Conservative estimate
        
        pod_cost = fraction * node["price_per_hour"]
        
        pod_costs.append({
            "pod": f"{pod['namespace']}/{pod['name']}",
            "node": node_name,
            "cpu_millicores": pod_cpu,
            "memory_mib": pod_mem,
            "estimated_cost_per_hour": round(pod_cost, 4)
        })
    
    # Overcommit detection
    overcommit_warnings = []
    if total_requested_cpu > total_node_capacity_cpu:
        overcommit_warnings.append({
            "type": "cpu_overcommit",
            "severity": "high",
            "message": f"CPU overcommit: {total_requested_cpu}m requested > {total_node_capacity_cpu}m available"
        })
    
    if total_requested_mem > total_node_capacity_mem:
        overcommit_warnings.append({
            "type": "memory_overcommit",
            "severity": "high",
            "message": f"Memory overcommit: {total_requested_mem}MiB requested > {total_node_capacity_mem}MiB available"
        })
    
    # CPU/Memory utilization ratios
    cpu_utilization = (total_requested_cpu / total_node_capacity_cpu * 100) if total_node_capacity_cpu > 0 else 0
    mem_utilization = (total_requested_mem / total_node_capacity_mem * 100) if total_node_capacity_mem > 0 else 0
    
    # Warn on very low utilization (potential waste)
    if cpu_utilization < 20 and len(nodes) > 0:
        overcommit_warnings.append({
            "type": "cpu_underutilization",
            "severity": "low",
            "message": f"Low CPU utilization: {cpu_utilization:.1f}% - cluster may be over-provisioned"
        })
    
    if mem_utilization < 20 and len(nodes) > 0:
        overcommit_warnings.append({
            "type": "memory_underutilization",
            "severity": "low",
            "message": f"Low memory utilization: {mem_utilization:.1f}% - cluster may be over-provisioned"
        })
    
    # Total cluster cost
    total_cluster_cost = sum(node["price_per_hour"] for node in node_costs.values())
    
    cost_summary = {
        "total_estimated_cost_per_hour": round(total_cluster_cost, 4),
        "total_estimated_cost_per_month": round(total_cluster_cost * 730, 2),  # 730 = avg hours/month
        "node_count": len(nodes),
        "pod_count": len([p for p in pods if p.get("node_name") != "unscheduled"]),
        "capacity": {
            "total_cpu_millicores": total_node_capacity_cpu,
            "total_memory_mib": total_node_capacity_mem,
            "requested_cpu_millicores": int(total_requested_cpu),
            "requested_memory_mib": int(total_requested_mem),
            "cpu_utilization_percent": round(cpu_utilization, 1),
            "memory_utilization_percent": round(mem_utilization, 1)
        },
        "overcommit_warnings": overcommit_warnings,
        "top_expensive_pods": sorted(pod_costs, key=lambda x: x["estimated_cost_per_hour"], reverse=True)[:10]
    }
    
    logger.info(f"Cost analysis: ${cost_summary['total_estimated_cost_per_hour']:.4f}/hr, "
                f"CPU util: {cpu_utilization:.1f}%, Mem util: {mem_utilization:.1f}%")
    
    return cost_summary


def _detect_provider(instance_type: str) -> str:
    """Detect cloud provider from instance type naming convention."""
    if not instance_type or instance_type == "unknown":
        return "default"
    
    instance_lower = instance_type.lower()
    
    # AWS: t3.medium, m5.large, etc.
    if any(prefix in instance_lower for prefix in ["t2.", "t3.", "m5.", "c5.", "r5."]):
        return "aws"
    
    # GCP: n1-standard-2, e2-medium, etc.
    if any(prefix in instance_lower for prefix in ["n1-", "n2-", "e2-", "c2-", "m1-"]):
        return "gcp"
    
    # Azure: Standard_B2s, Standard_D2s_v3, etc.
    if "standard_" in instance_lower or instance_lower.startswith("basic_"):
        return "azure"
    
    return "default"


def _get_price_entry(provider: str, instance_type: str) -> Dict[str, Any]:
    """Get price entry for instance type."""
    price_map = DEFAULT_PRICE_MAP.get(provider, DEFAULT_PRICE_MAP["default"])
    
    if isinstance(price_map, dict):
        # Try exact match
        if instance_type in price_map:
            return price_map[instance_type]
        # Fallback to default for provider
        if "default" in price_map:
            return price_map["default"]
    
    # Ultimate fallback
    return DEFAULT_PRICE_MAP["default"]
