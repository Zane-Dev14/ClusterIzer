"""
Tool layer - deterministic tools for agent use.

Factory functions create closure-based tools that give agents bounded
access to deterministic cluster data. No cluster calls, no side effects.
"""

import json
import logging
from typing import List, Optional
from langchain_core.tools import tool

from .models import InfraState

logger = logging.getLogger(__name__)


def make_tools(state: InfraState) -> List:
    """
    Create tools that capture state in closures.
    
    This factory pattern avoids global mutable state while giving
    agents access to deterministic data extracted during cluster scan.
    
    Args:
        state: Current InfraState with deterministic layer populated
        
    Returns:
        List of LangChain tools
    """
    
    @tool
    def get_cluster_summary() -> str:
        """
        Get high-level cluster summary (counts, node names, namespaces).
        Does NOT return full raw cluster snapshot.
        """
        snapshot = state["cluster_snapshot"]
        
        nodes = snapshot.get("nodes", [])
        deployments = snapshot.get("deployments", [])
        pods = snapshot.get("pods", [])
        services = snapshot.get("services", [])
        
        # Extract unique namespaces
        namespaces = set()
        for dep in deployments:
            namespaces.add(dep["namespace"])
        for pod in pods:
            namespaces.add(pod["namespace"])
        for svc in services:
            namespaces.add(svc["namespace"])
        
        summary = {
            "node_count": len(nodes),
            "node_names": [n["name"] for n in nodes],
            "deployment_count": len(deployments),
            "pod_count": len(pods),
            "service_count": len(services),
            "namespaces": sorted(namespaces),
        }
        
        return json.dumps(summary, indent=2)
    
    @tool
    def get_graph_summary() -> str:
        """
        Get dependency graph summary with derived metrics.
        Includes orphan services, single-replica deployments, node fanout.
        """
        graph = state["graph_summary"]
        
        # Return relevant parts (not full adjacency)
        summary = {
            "orphan_services": graph.get("orphan_services", []),
            "single_replica_deployments": graph.get("single_replica_deployments", []),
            "node_fanout_count": graph.get("node_fanout_count", {}),
            "service_count": len(graph.get("service_to_deployment", {})),
            "deployment_with_pods_count": len(graph.get("deployment_to_pods", {})),
        }
        
        return json.dumps(summary, indent=2)
    
    @tool
    def get_signals(category: Optional[str] = None) -> str:
        """
        Get signals, optionally filtered by category.
        
        Args:
            category: Optional filter ("reliability", "security", "cost")
        
        Returns:
            JSON array of signals
        """
        signals = state["signals"]
        
        if category:
            signals = [s for s in signals if s.get("category") == category]
        
        # Limit to 100 for tool output
        return json.dumps(signals[:100], indent=2)
    
    @tool
    def get_risk_score() -> str:
        """
        Get computed risk score and grade.
        """
        risk = state["risk_score"]
        return json.dumps(risk, indent=2)
    
    return [
        get_cluster_summary,
        get_graph_summary,
        get_signals,
        get_risk_score,
    ]
