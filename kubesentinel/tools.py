"""Tool layer - deterministic tools for agent use."""
import json
import logging
from typing import List, Optional
from langchain_core.tools import tool

from .models import InfraState

logger = logging.getLogger(__name__)


def make_tools(state: InfraState) -> List:
    """Create tools that capture state in closures."""
    @tool
    def get_cluster_summary() -> str:
        """Get high-level cluster summary."""
        snapshot = state.get("cluster_snapshot", {})
        nodes, deployments, pods, services = snapshot.get("nodes", []), snapshot.get("deployments", []), snapshot.get("pods", []), snapshot.get("services", [])
        namespaces = set()
        for dep in deployments:
            namespaces.add(dep["namespace"])
        for pod in pods:
            namespaces.add(pod["namespace"])
        for svc in services:
            namespaces.add(svc["namespace"])
        return json.dumps({"node_count": len(nodes), "node_names": [n["name"] for n in nodes], "deployment_count": len(deployments), "pod_count": len(pods), "service_count": len(services), "namespaces": sorted(namespaces)}, indent=2)
    
    @tool
    def get_graph_summary() -> str:
        """Get dependency graph summary."""
        graph = state.get("graph_summary", {})
        return json.dumps({"orphan_services": graph.get("orphan_services", []), "single_replica_deployments": graph.get("single_replica_deployments", []), "node_fanout_count": graph.get("node_fanout_count", {}), "service_count": len(graph.get("service_to_deployment", {})), "deployment_with_pods_count": len(graph.get("deployment_to_pods", {}))}, indent=2)
    
    @tool
    def get_signals(category: Optional[str] = None) -> str:
        """Get signals, optionally filtered by category."""
        signals = state.get("signals", [])
        if category:
            signals = [s for s in signals if s.get("category") == category]
        return json.dumps(signals[:100], indent=2)
    
    @tool
    def get_risk_score() -> str:
        """Get computed risk score and grade."""
        return json.dumps(state.get("risk_score", {}), indent=2)
    return [get_cluster_summary, get_graph_summary, get_signals, get_risk_score]
