"""LangGraph runtime - orchestrates the full execution graph."""
import logging
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .models import InfraState
from .cluster import scan_cluster
from .graph_builder import build_graph
from .signals import generate_signals
from .risk import compute_risk
from .agents import (
    planner_node,
    failure_agent_node,
    cost_agent_node,
    security_agent_node,
    synthesizer_node,
)

logger = logging.getLogger(__name__)


def build_runtime_graph() -> Any:
    """Build the complete LangGraph execution graph."""
    logger.info("Building runtime graph...")
    builder = StateGraph(InfraState)
    for name, func in [("scan_cluster", scan_cluster), ("build_graph", build_graph), ("generate_signals", generate_signals), ("compute_risk", compute_risk), ("planner", planner_node), ("failure_agent", failure_agent_node), ("cost_agent", cost_agent_node), ("security_agent", security_agent_node), ("synthesizer", synthesizer_node)]:
        builder.add_node(name, func)
    for src, dst in [("scan_cluster", "build_graph"), ("build_graph", "generate_signals"), ("generate_signals", "compute_risk"), ("compute_risk", "planner"), ("planner", "failure_agent"), ("failure_agent", "cost_agent"), ("cost_agent", "security_agent"), ("security_agent", "synthesizer"), ("synthesizer", END)]:
        builder.add_edge(src, dst)
    builder.set_entry_point("scan_cluster")
    graph = builder.compile(checkpointer=MemorySaver())
    logger.info("Runtime graph compiled")
    return graph

_graph = None

def get_graph():
    """Get or create the runtime graph."""
    global _graph
    if _graph is None:
        _graph = build_runtime_graph()
    return _graph


def run_engine(user_query: str, namespace: str | None = None) -> InfraState:
    """Run the complete KubeSentinel analysis engine."""
    logger.info(f"Starting engine: {user_query}")
    if namespace:
        logger.info(f"Namespace: {namespace}")
    
    # Initialize state
    initial_state: InfraState = {
        "user_query": user_query,
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    # Pass namespace through context
    if namespace:
        initial_state["target_namespace"] = namespace
    try:
        result = get_graph().invoke(initial_state, {"configurable": {"thread_id": "main"}})
        logger.info("Engine execution complete")
        return result
    except Exception as e:
        logger.error(f"Engine failed: {e}", exc_info=True)
        raise RuntimeError(f"Execution failed: {e}")
