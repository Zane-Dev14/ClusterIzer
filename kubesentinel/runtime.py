import logging
import copy
from datetime import datetime
from typing import Any, MutableMapping, cast
from concurrent.futures import ThreadPoolExecutor, as_completed

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
)
from .synthesizer import synthesizer_node
from .persistence import PersistenceManager, drift_to_signals
from .git_loader import load_git_desired_state
from .runtime_tracer import get_tracer, reset_tracer

logger = logging.getLogger(__name__)

# Global persistence manager
_persistence_manager: PersistenceManager | None = None


def get_persistence_manager() -> PersistenceManager:
    """Get or create persistence manager."""
    global _persistence_manager
    if _persistence_manager is None:
        _persistence_manager = PersistenceManager()
    return _persistence_manager


def persist_snapshot(state: InfraState) -> InfraState:
    """Persist current snapshot and detect drift."""
    logger.info("Persisting snapshot...")
    pm = get_persistence_manager()

    # Save snapshot - convert TypedDict to dict for persistence layer
    state_dict = dict(state)
    timestamp = pm.save_snapshot(state_dict)
    state["_snapshot_persisted_at"] = timestamp
    state["_snapshot_timestamp"] = datetime.utcnow().isoformat()

    # Detect drift against previous snapshot
    drift_analysis = pm.analyze_drift(state_dict)

    # Convert critical drifts to signals
    if (
        drift_analysis.get("summary", {}).get("critical_lost_count", 0) > 0
        or drift_analysis.get("summary", {}).get("critical_risky_count", 0) > 0
    ):
        old_signals = state.get("signals", [])
        state["signals"] = drift_to_signals(drift_analysis, old_signals or [])

    state["_drift_analysis"] = drift_analysis
    summary = drift_analysis.get("summary", {})
    logger.info(f"Drift detected: {summary.get('total_changes', 0)} changes")

    return state


def load_desired_state(state: InfraState) -> InfraState:
    """Optionally load desired state from Git repository or local manifest path."""
    git_repo = state.get("git_repo")
    if not git_repo:
        return state

    logger.info(f"Loading desired state from: {git_repo}")
    desired = load_git_desired_state(repo_url=git_repo, local_path=None, branch="main")
    state["_desired_state_snapshot"] = desired
    desired_count = sum(len(items) for items in desired.values())
    logger.info(f"Loaded desired state resources: {desired_count}")
    return state


def run_agents_parallel(state: InfraState) -> InfraState:
    """Run selected agents concurrently and merge findings.

    Uses deep copy to prevent race conditions when agents read/modify state.
    """
    selected = set(state.get("planner_decision", []))
    agent_map = {
        "failure_agent": (failure_agent_node, "failure_findings"),
        "cost_agent": (cost_agent_node, "cost_findings"),
        "security_agent": (security_agent_node, "security_findings"),
    }

    # Use a mutable mapping view to perform dynamic key access on the TypedDict
    mutable_state: MutableMapping[str, Any] = cast(MutableMapping[str, Any], state)
    for _, findings_key in agent_map.values():
        mutable_state.setdefault(findings_key, [])

    run_targets = {name: cfg for name, cfg in agent_map.items() if name in selected}
    if not run_targets:
        return state

    # Phase N logging: Log which agents are actually running (consistency with planner)
    logger.info(f"[executor] running_agents={sorted(run_targets.keys())}")
    logger.info("Running selected agents concurrently...")
    max_workers = min(len(run_targets), 3)  # Limit to 3 parallel workers
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(func, copy.deepcopy(state)): (name, findings_key)  # type: ignore
            for name, (func, findings_key) in run_targets.items()
        }
        for future in as_completed(futures):
            name, findings_key = futures[future]
            try:
                result_state = future.result()
                mutable_state[findings_key] = result_state.get(findings_key, [])
                logger.debug(
                    f"Agent {name} completed: {len(mutable_state[findings_key])} findings"
                )
            except Exception as exc:
                logger.error(
                    f"{name} failed in parallel execution: {exc}", exc_info=True
                )
                mutable_state[findings_key] = []

    return state


def build_runtime_graph() -> Any:
    """Build the complete LangGraph execution graph."""
    logger.info("Building runtime graph...")
    builder = StateGraph(InfraState)

    # Wrap nodes with tracing
    def traced_node(node_func, node_name):
        """Wrap a node function with tracing."""

        def wrapper(state: InfraState) -> InfraState:
            tracer = get_tracer()
            tracer.enter_node(node_name)
            try:
                result = node_func(state)
                # Log state summary
                summary = {
                    "findings_count": (
                        len(result.get("failure_findings", []))
                        + len(result.get("cost_findings", []))
                        + len(result.get("security_findings", []))
                    ),
                    "signals_count": len(result.get("signals", [])),
                }
                tracer.exit_node(node_name, summary)
                return result
            except Exception as e:
                logger.error(f"[{node_name}] Error: {e}")
                tracer.exit_node(node_name)
                raise

        return wrapper

    nodes = [
        ("scan_cluster", scan_cluster),
        ("load_desired_state", load_desired_state),
        ("build_graph", build_graph),
        ("generate_signals", generate_signals),
        ("persist_snapshot", persist_snapshot),
        ("compute_risk", compute_risk),
        ("planner", planner_node),
        ("run_agents_parallel", run_agents_parallel),
        ("synthesizer", synthesizer_node),
    ]

    for name, func in nodes:
        builder.add_node(name, traced_node(func, name))

    for src, dst in [
        ("scan_cluster", "load_desired_state"),
        ("load_desired_state", "build_graph"),
        ("build_graph", "generate_signals"),
        ("generate_signals", "persist_snapshot"),
        ("persist_snapshot", "compute_risk"),
        ("compute_risk", "planner"),
        ("planner", "run_agents_parallel"),
        ("run_agents_parallel", "synthesizer"),
        ("synthesizer", END),
    ]:
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


def run_engine(
    user_query: str,
    namespace: str | None = None,
    agents: list[str] | None = None,
    git_repo: str | None = None,
) -> InfraState:
    """Run the complete KubeSentinel analysis engine.

    Args:
        user_query: The analysis query
        namespace: Optional Kubernetes namespace to scan
        agents: Optional list of agent names to override planner decision

    Returns:
        InfraState with analysis results

    Side Effects:
        - Saves runtime trace to runtime_traces/runtime_trace_*.json
        - Saves execution graph to runtime_traces/runtime_graph_*.mmd
    """
    # Reset tracer for new execution
    reset_tracer()
    tracer = get_tracer()

    logger.info(f"Starting engine: {user_query}")
    if namespace:
        logger.info(f"Namespace: {namespace}")
    if agents:
        logger.info(f"Agent override: {agents}")
    if git_repo:
        logger.info(f"Desired state source: {git_repo}")

    # Initialize state
    initial_state: InfraState = {
        "user_query": user_query,
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [],
        "risk_score": {},
        "planner_decision": agents if agents else [],  # Override if provided
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    # Pass namespace through context
    if namespace:
        initial_state["target_namespace"] = namespace
    if git_repo:
        initial_state["git_repo"] = git_repo
    try:
        result = get_graph().invoke(
            initial_state, {"configurable": {"thread_id": "main"}}
        )
        logger.info("Engine execution complete")

        # Save traces automatically
        trace_file = tracer.save_trace()
        graph_file = tracer.save_graph()
        logger.info(f"Runtime trace saved: {trace_file}")
        logger.info(f"Runtime graph saved: {graph_file}")

        # Log execution summary
        logger.info("=" * 80)
        logger.info("EXECUTION SUMMARY")
        logger.info("=" * 80)
        mermaid_graph = tracer.generate_mermaid_graph()
        logger.info(f"\n{mermaid_graph}")
        logger.info("=" * 80)

        return result
    except Exception as e:
        logger.error(f"Engine failed: {e}", exc_info=True)
        # Still save trace even on failure
        try:
            tracer.save_trace()
            tracer.save_graph()
        except Exception as save_error:
            logger.error(f"Failed to save trace after error: {save_error}")
        raise RuntimeError(f"Execution failed: {e}")
