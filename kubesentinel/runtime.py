"""
LangGraph runtime - orchestrates the full execution graph.

Builds StateGraph with all nodes, defines execution flow,
compiles with checkpointer for state persistence.
"""

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
    """
    Build the complete LangGraph execution graph.
    
    Graph flow:
    1. scan_cluster: Connect to k8s and extract state
    2. build_graph: Build dependency graph
    3. generate_signals: Generate deterministic signals
    4. compute_risk: Calculate risk score
    5. planner: Decide which agents to run
    6. failure_agent: Analyze reliability (if selected)
    7. cost_agent: Analyze costs (if selected)
    8. security_agent: Analyze security (if selected)
    9. synthesizer: Produce strategic summary
    10. END
    
    Returns:
        Compiled graph ready for invocation
    """
    logger.info("Building runtime graph...")
    
    # Create StateGraph with InfraState schema
    builder = StateGraph(InfraState)
    
    # Add all nodes
    builder.add_node("scan_cluster", scan_cluster)
    builder.add_node("build_graph", build_graph)
    builder.add_node("generate_signals", generate_signals)
    builder.add_node("compute_risk", compute_risk)
    builder.add_node("planner", planner_node)
    builder.add_node("failure_agent", failure_agent_node)
    builder.add_node("cost_agent", cost_agent_node)
    builder.add_node("security_agent", security_agent_node)
    builder.add_node("synthesizer", synthesizer_node)
    
    # Define linear execution flow for deterministic layer
    builder.add_edge("scan_cluster", "build_graph")
    builder.add_edge("build_graph", "generate_signals")
    builder.add_edge("generate_signals", "compute_risk")
    builder.add_edge("compute_risk", "planner")
    
    # Sequential agent execution (all agents check planner_decision internally)
    builder.add_edge("planner", "failure_agent")
    builder.add_edge("failure_agent", "cost_agent")
    builder.add_edge("cost_agent", "security_agent")
    builder.add_edge("security_agent", "synthesizer")
    
    # Synthesizer goes to END
    builder.add_edge("synthesizer", END)
    
    # Set entry point
    builder.set_entry_point("scan_cluster")
    
    # Compile with memory checkpointer
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)
    
    logger.info("Runtime graph compiled successfully")
    return graph


# Global graph instance
_graph = None


def get_graph():
    """Get or create the runtime graph (singleton)."""
    global _graph
    if _graph is None:
        _graph = build_runtime_graph()
    return _graph


def run_engine(user_query: str) -> InfraState:
    """
    Run the complete KubeSentinel analysis engine.
    
    Executes the full graph from cluster scan through synthesis.
    
    Args:
        user_query: User's analysis request (e.g., "Full cluster analysis")
        
    Returns:
        Final InfraState with all analysis complete
        
    Raises:
        RuntimeError: If cluster connection or execution fails
    """
    logger.info(f"Starting KubeSentinel engine with query: {user_query}")
    
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
    
    # Get graph
    graph = get_graph()
    
    # Invoke with thread config for checkpointing
    config = {"configurable": {"thread_id": "main"}}
    
    try:
        result = graph.invoke(initial_state, config)
        logger.info("Engine execution complete")
        return result
    except Exception as e:
        logger.error(f"Engine execution failed: {e}", exc_info=True)
        raise RuntimeError(f"KubeSentinel execution failed: {e}")
