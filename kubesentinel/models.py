"""
State contract for KubeSentinel graph execution.

This defines the single execution contract that flows through all nodes.
All lists are hard-capped to prevent unbounded growth.
"""

from typing import TypedDict, List, Dict, Any

# Hard caps to prevent unbounded state growth
MAX_PODS = 1000
MAX_DEPLOYMENTS = 200
MAX_SERVICES = 200
MAX_NODES = 100
MAX_SIGNALS = 200
MAX_FINDINGS = 50


class InfraState(TypedDict):
    """
    Execution state for the KubeSentinel graph.
    
    This TypedDict defines the complete state schema that flows through
    all nodes in the LangGraph execution. No nested arbitrary growth is
    allowed - all lists must be capped.
    """
    
    # User input
    user_query: str
    
    # Deterministic layer outputs
    cluster_snapshot: Dict[str, Any]  # {nodes, deployments, pods, services}
    graph_summary: Dict[str, Any]     # {adjacency dicts, derived metrics}
    signals: List[Dict[str, Any]]     # [{category, severity, resource, message}]
    risk_score: Dict[str, Any]        # {score, grade, signal_count}
    
    # Planner output
    planner_decision: List[str]       # ["failure_agent", "cost_agent", ...]
    
    # Agent outputs
    failure_findings: List[Dict[str, Any]]   # [{resource, severity, analysis, recommendation}]
    cost_findings: List[Dict[str, Any]]      # [{resource, severity, analysis, recommendation}]
    security_findings: List[Dict[str, Any]]  # [{resource, severity, analysis, recommendation}]
    
    # Synthesis outputs
    strategic_summary: str            # Executive summary from synthesizer
    final_report: str                 # Full markdown report
