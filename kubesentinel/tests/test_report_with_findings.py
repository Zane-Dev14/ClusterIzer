"""
Tests for report generation with agent findings.

Tests verify that findings from all agents are included in the final report.
"""

from kubesentinel.reporting import build_report
from kubesentinel.models import InfraState
from pathlib import Path


def test_report_includes_reliability_findings():
    """Test that report includes reliability (failure_agent) findings."""
    state: InfraState = {
        "user_query": "test analysis",
        "cluster_snapshot": {
            "nodes": [{"name": "node-1"}],
            "pods": [],
            "deployments": [],
            "services": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 50, "grade": "C", "signal_count": 0},
        "planner_decision": [],
        "failure_findings": [
            {
                "resource": "default/deployment/myapp",
                "severity": "critical",
                "analysis": "Pod in CrashLoopBackOff",
                "recommendation": "Check pod logs immediately",
                "verified": True,
                "evidence": "OOMKilled in logs",
            }
        ],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "Test summary",
        "final_report": "",
    }
    
    report = build_report(state)
    
    # Should contain reliability findings section
    assert "Reliability Issues" in report or "🚨" in report
    assert "myapp" in report
    assert "CrashLoopBackOff" in report
    assert "Verified" in report  # Should show verification status


def test_report_includes_all_finding_categories():
    """Test that report includes findings from all categories."""
    state: InfraState = {
        "user_query": "comprehensive analysis",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": [],
            "services": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 75, "grade": "D", "signal_count": 5},
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
                "resource": "staging/deploy/unused",
                "severity": "medium",
                "analysis": "Idle deployment",
                "recommendation": "Remove or scale down",
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
        "strategic_summary": "Analysis complete",
        "final_report": "",
    }
    
    report = build_report(state)
    
    # Should contain sections for all three categories
    assert "Reliability Issues" in report or "🚨" in report
    assert "Cost Optimization" in report or "💰" in report
    assert "Security Audit" in report or "🔐" in report
    
    # Should contain actual findings
    assert "api" in report
    assert "unused" in report
    assert "exposed" in report


def test_report_header_contains_metadata():
    """Test that report header contains required metadata."""
    state: InfraState = {
        "user_query": "check cluster health",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": [],
            "services": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 0, "grade": "A"},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    report = build_report(state)
    
    # Should have title and query
    assert "KubeSentinel Infrastructure Intelligence Report" in report
    assert "check cluster health" in report
    assert "Report generated at" in report


def test_report_written_to_disk():
    """Test that report is written to report.md file."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": [],
            "services": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 0, "grade": "A"},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    # Clean up before test
    report_file = Path("report.md")
    if report_file.exists():
        report_file.unlink()
    
    build_report(state)
    
    # Check file was created
    assert report_file.exists()
    content = report_file.read_text()
    assert len(content) > 0
    assert "KubeSentinel" in content


def test_report_marks_verified_findings():
    """Test that verified findings are marked with checkmark in report."""
    state: InfraState = {
        "user_query": "test verification",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": [],
            "services": [],
        },
        "graph_summary": {},
        "signals": [],
        "risk_score": {"score": 50, "grade": "C"},
        "planner_decision": [],
        "failure_findings": [
            {
                "resource": "default/pod/test",
                "severity": "high",
                "analysis": "Test issue",
                "recommendation": "Fix it",
                "verified": True,
                "evidence": "Evidence found in logs",
            }
        ],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    report = build_report(state)
    
    # Should show verification status
    assert "✅" in report or "Verified" in report
    assert "Evidence found" in report
