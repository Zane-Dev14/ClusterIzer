"""Runtime execution tracer for KubeSentinel.

Automatically records and visualizes the execution flow through the engine.
Generates Mermaid diagrams showing actual runtime execution paths.
"""

import logging
import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ExecutionTracer:
    """Trace runtime execution and generate execution graphs."""

    def __init__(self, trace_dir: Optional[Path] = None):
        self.trace_dir = trace_dir or Path("runtime_traces")
        self.trace_dir.mkdir(exist_ok=True)
        self.events: List[Dict[str, Any]] = []
        self.start_time = datetime.now()
        self.node_timings: Dict[str, float] = {}
        self.current_node: Optional[str] = None

    def enter_node(self, node_name: str) -> None:
        """Record entering a graph node."""
        self.current_node = node_name
        self.node_timings[node_name] = datetime.now().timestamp()
        self.events.append(
            {
                "timestamp": datetime.now().isoformat(),
                "event": "node_enter",
                "node": node_name,
            }
        )
        logger.info(f"[TRACE] → {node_name}")

    def exit_node(self, node_name: str, state_summary: Optional[Dict] = None) -> None:
        """Record exiting a graph node."""
        elapsed = (
            (datetime.now().timestamp() - self.node_timings.get(node_name, 0))
            if node_name in self.node_timings
            else 0
        )

        event_data = {
            "timestamp": datetime.now().isoformat(),
            "event": "node_exit",
            "node": node_name,
            "elapsed_seconds": round(elapsed, 2),
        }

        if state_summary:
            event_data["state_summary"] = state_summary

        self.events.append(event_data)
        logger.info(f"[TRACE] ← {node_name} ({elapsed:.2f}s)")
        self.current_node = None

    def log_state_change(self, key: str, value: Any) -> None:
        """Record a state change."""
        self.events.append(
            {
                "timestamp": datetime.now().isoformat(),
                "event": "state_change",
                "key": key,
                "value_type": type(value).__name__,
            }
        )

    def generate_mermaid_graph(self) -> str:
        """Generate a Mermaid diagram of the execution path taken."""
        # Build path from events
        nodes_visited = []
        for event in self.events:
            if event["event"] == "node_enter":
                node = event["node"]
                if node not in nodes_visited:
                    nodes_visited.append(node)

        if not nodes_visited:
            return "graph TD\n  A[No nodes executed]"

        # Create Mermaid diagram
        diagram = ["graph TD"]

        # Add nodes with icons
        icons = {
            "scan_cluster": "🔍",
            "load_desired_state": "📋",
            "build_graph": "🕸️",
            "generate_signals": "⚠️",
            "persist_snapshot": "💾",
            "compute_risk": "📊",
            "planner": "🤖",
            "run_agents_parallel": "⚙️",
            "synthesizer": "📝",
            "build_report": "📄",
        }

        # Create node definitions with timing
        for i, node in enumerate(nodes_visited):
            icon = icons.get(node, "◆")
            # Find elapsed time for this node
            elapsed = 0
            for event in self.events:
                if event["event"] == "node_exit" and event["node"] == node:
                    elapsed = event.get("elapsed_seconds", 0)

            node_id = f"N{i}"
            display_name = node.replace("_", " ").title()
            diagram.append(f'  {node_id}["{icon} {display_name}<br/>({elapsed}s)"]')

        # Add edges
        for i in range(len(nodes_visited) - 1):
            diagram.append(f"  N{i} --> N{i + 1}")

        return "\n".join(diagram)

    def save_trace(self, filename: Optional[str] = None) -> Path:
        """Save execution trace to file."""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"runtime_trace_{timestamp}.json"

        filepath = self.trace_dir / filename
        with open(filepath, "w") as f:
            json.dump(
                {
                    "start_time": self.start_time.isoformat(),
                    "end_time": datetime.now().isoformat(),
                    "events": self.events,
                },
                f,
                indent=2,
            )

        logger.info(f"Runtime trace saved to {filepath}")
        return filepath

    def save_graph(self, filename: Optional[str] = None) -> Path:
        """Save Mermaid graph to file."""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"runtime_graph_{timestamp}.mmd"

        filepath = self.trace_dir / filename
        mermaid_content = self.generate_mermaid_graph()
        with open(filepath, "w") as f:
            f.write(mermaid_content)

        logger.info(f"Runtime graph saved to {filepath}")
        return filepath


# Global tracer instance
_tracer: Optional[ExecutionTracer] = None


def get_tracer() -> ExecutionTracer:
    """Get or create the global execution tracer."""
    global _tracer
    if _tracer is None:
        _tracer = ExecutionTracer()
    return _tracer


def reset_tracer() -> None:
    """Reset the global tracer."""
    global _tracer
    _tracer = None
