"""
Tests for _verify_findings_with_evidence() - ReAct verification loop.

Tests verify findings enhancement with cluster evidence.
"""

import pytest
from unittest.mock import patch, MagicMock
from kubesentinel.agents import _verify_findings_with_evidence
from kubesentinel.models import InfraState


def test_verify_findings_empty():
    """Test verification with empty findings list."""
    state: InfraState = {
        "user_query": "test",
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
    
    verified = _verify_findings_with_evidence([], state)
    assert verified == []


def test_verify_findings_invalid_resource_format():
    """Test verification with malformed resource names."""
    findings = [
        {
            "resource": "invalid",
            "severity": "high",
            "analysis": "Test issue",
            "recommendation": "Fix it",
        }
    ]
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {"pods": []},
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
    
    verified = _verify_findings_with_evidence(findings, state)
    assert len(verified) == 1
    assert verified[0]["verified"] == False
    assert "Invalid resource format" in verified[0]["evidence"]


def test_verify_findings_pod_not_found():
    """Test verification when pod not found in snapshot."""
    findings = [
        {
            "resource": "default/nonexistent-pod",
            "severity": "critical",
            "analysis": "Pod missing",
            "recommendation": "Investigate",
        }
    ]
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "pods": [
                {
                    "name": "existing-pod",
                    "namespace": "default",
                    "phase": "Running",
                }
            ]
        },
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
    
    verified = _verify_findings_with_evidence(findings, state)
    assert len(verified) == 1
    assert verified[0]["verified"] == False
    assert "Not found" in verified[0]["evidence"]


def test_verify_findings_pod_found():
    """Test verification when pod found in snapshot."""
    findings = [
        {
            "resource": "default/existing-pod",
            "severity": "medium",
            "analysis": "Pod exists",
            "recommendation": "Monitor it",
        }
    ]
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "pods": [
                {
                    "name": "existing-pod",
                    "namespace": "default",
                    "phase": "Running",
                    "crash_loop_backoff": False,
                }
            ],
            "deployments": [],
            "services": [],
            "configmaps": [],
            "statefulsets": [],
            "daemonsets": [],
        },
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
    
    verified = _verify_findings_with_evidence(findings, state, max_verifications=5)
    assert len(verified) == 1
    assert "verified" in verified[0]
    assert "evidence" in verified[0]


@patch('subprocess.run')
def test_verify_findings_crash_loop_with_logs(mock_run):
    """Test verification of crashloop pod tries to fetch logs."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="OOMKilled: Container ran out of memory"
    )
    
    findings = [
        {
            "resource": "default/crash-pod",
            "severity": "critical",
            "analysis": "Pod crashing",
            "recommendation": "Increase memory limits",
        }
    ]
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "pods": [
                {
                    "name": "crash-pod",
                    "namespace": "default",
                    "phase": "Running",
                    "crash_loop_backoff": True,
                }
            ],
            "deployments": [],
            "services": [],
            "configmaps": [],
            "statefulsets": [],
            "daemonsets": [],
        },
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
    
    verified = _verify_findings_with_evidence(findings, state, max_verifications=5)
    assert len(verified) == 1
    assert "evidence" in verified[0]
    # Should have tried to fetch logs
    assert mock_run.called


def test_verify_findings_respects_max_verifications():
    """Test that verification respects max_verifications limit."""
    findings = [
        {
            "resource": f"default/pod{i}",
            "severity": "low",
            "analysis": f"Finding {i}",
            "recommendation": "Fix",
        }
        for i in range(5)
    ]
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "pods": [],
            "deployments": [],
            "services": [],
            "configmaps": [],
            "statefulsets": [],
            "daemonsets": [],
        },
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
    
    verified = _verify_findings_with_evidence(findings, state, max_verifications=2)
    assert len(verified) == 5
    # First 2 should have actual verification attempt
    assert verified[0]["evidence"] != "Verification skipped (timeout constraint)"
    assert verified[1]["evidence"] != "Verification skipped (timeout constraint)"
    # Last 3 should be marked as skipped
    assert verified[2]["evidence"] == "Verification skipped (timeout constraint)"
    assert verified[3]["evidence"] == "Verification skipped (timeout constraint)"
    assert verified[4]["evidence"] == "Verification skipped (timeout constraint)"
