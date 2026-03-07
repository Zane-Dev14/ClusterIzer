from typing import TypedDict, List, Dict, Any, Optional

# Hard caps to prevent unbounded state growth
MAX_PODS = 1000
MAX_DEPLOYMENTS = 200
MAX_SERVICES = 200
MAX_NODES = 100
MAX_SIGNALS = 200
MAX_FINDINGS = 50


class InfraState(TypedDict, total=False):
    # User input (required)
    user_query: str

    # Deterministic layer outputs (required)
    cluster_snapshot: Dict[str, Any]  # {nodes, deployments, pods, services}
    graph_summary: Dict[str, Any]  # {adjacency dicts, derived metrics}
    signals: List[Dict[str, Any]]  # [{category, severity, resource, message}]
    risk_score: Dict[str, Any]  # {score, grade, signal_count}

    # Planner output
    planner_decision: List[str]  # ["failure_agent", "cost_agent", ...]

    # Agent outputs
    failure_findings: List[
        Dict[str, Any]
    ]  # [{resource, severity, analysis, recommendation}]
    cost_findings: List[
        Dict[str, Any]
    ]  # [{resource, severity, analysis, recommendation}]
    security_findings: List[
        Dict[str, Any]
    ]  # [{resource, severity, analysis, recommendation}]

    # Synthesis outputs
    strategic_summary: str  # Executive summary from synthesizer
    final_report: str  # Full markdown report

    # Runtime configuration
    target_namespace: Optional[
        str
    ]  # Kubernetes namespace to scope scan (None = all namespaces)
    git_repo: Optional[str]  # Desired-state repo URL or local path
    _desired_state_snapshot: Optional[Dict[str, List[Dict[str, Any]]]]

    # Persistence/Drift tracking (optional, added during execution)
    _drift_analysis: Optional[Dict[str, Any]]  # Drift detection results
    _snapshot_timestamp: Optional[str]  # When snapshot was persisted
    _snapshot_persisted_at: Optional[str]  # Persistence timestamp
