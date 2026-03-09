"""
Tests for synthesizer_node() - deterministic strategic summary generation.

Tests verify that synthesizer produces structured output without placeholders.
"""

from kubesentinel.agents import _synthesize_strategic_summary, synthesizer_node
from kubesentinel.models import InfraState


def test_synthesize_strategic_summary_basic():
    """Test basic summary generation from empty findings."""
    state: InfraState = {
        "user_query": "test analysis",
        "cluster_snapshot": {
            "nodes": [{"name": "node-1"}, {"name": "node-2"}],
            "pods": [{"name": "pod-1"}, {"name": "pod-2"}],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 45, "grade": "C", "signal_count": 5},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    summary = _synthesize_strategic_summary(state)
    
    # Should contain key sections
    assert "# Strategic Summary" in summary
    assert "Risk Assessment" in summary
    assert "Cluster Size" in summary
    assert "45/100" in summary
    assert "2 nodes" in summary


def test_synthesize_strategic_summary_with_critical_findings():
    """Test summary generation with critical findings."""
    state: InfraState = {
        "user_query": "test analysis",
        "cluster_snapshot": {
            "nodes": [{"name": "node-1"}],
            "pods": [{"name": "pod-1"}],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 85, "grade": "F", "signal_count": 10},
        "planner_decision": [],
        "failure_findings": [
            {
                "resource": "default/deployment/myapp",
                "severity": "critical",
                "analysis": "Pod in CrashLoopBackOff",
                "recommendation": "Check pod logs immediately",
                "verified": True,
                "evidence": "OOMKilled error in logs",
            }
        ],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    summary = _synthesize_strategic_summary(state)
    
    # Should highlight critical issues
    assert "Critical Issues (1 found)" in summary
    assert "default/deployment/myapp" in summary
    assert "CrashLoopBackOff" in summary
    assert "OOMKilled error" in summary


def test_synthesize_strategic_summary_with_multiple_findings():
    """Test summary with findings across all categories."""
    state: InfraState = {
        "user_query": "comprehensive analysis",
        "cluster_snapshot": {
            "nodes": [{"name": "node-1"}, {"name": "node-2"}],
            "pods": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 70, "grade": "D", "signal_count": 8},
        "planner_decision": [],
        "failure_findings": [
            {
                "resource": "prod/svc/api",
                "severity": "high",
                "analysis": "Single replica",
                "recommendation": "Scale to 3 replicas",
                "verified": False,
                "evidence": "",
            }
        ],
        "cost_findings": [
            {
                "resource": "staging/deployment/unused",
                "severity": "medium",
                "analysis": "Idle deployment",
                "recommendation": "Scale down or remove",
                "verified": False,
                "evidence": "",
            }
        ],
        "security_findings": [
            {
                "resource": "default/pod/exposed",
                "severity": "high",
                "analysis": "Privileged container",
                "recommendation": "Remove privileged flag",
                "verified": False,
                "evidence": "",
            }
        ],
        "strategic_summary": "",
        "final_report": "",
    }
    
    summary = _synthesize_strategic_summary(state)
    
    # Should contain findings from all categories
    assert "Reliability" in summary and "1 findings" in summary
    assert "Cost" in summary and "1 findings" in summary
    assert "Security" in summary and "1 findings" in summary
    assert "Findings by Category" in summary


def test_synthesize_strategic_summary_no_placeholders():
    """Test that deterministic summary never has placeholders."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 50, "grade": "C", "signal_count": 3},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    summary = _synthesize_strategic_summary(state)
    
    # Should not contain angle bracket placeholders
    assert "<" not in summary or "http" in summary  # Allow URLs but not <placeholder>
    # Check for common placeholder patterns
    import re
    placeholders = re.findall(r"<[a-z_\-]+>", summary, re.IGNORECASE)
    assert len(placeholders) == 0, f"Found placeholders: {placeholders}"


def test_synthesizer_node_completes():
    """Test that synthesizer_node completes successfully."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [{"name": "node-1"}],
            "pods": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 50, "grade": "C", "signal_count": 0},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    result = synthesizer_node(state)
    
    # Should have populated strategic_summary
    assert "strategic_summary" in result
    assert len(result["strategic_summary"]) > 0
