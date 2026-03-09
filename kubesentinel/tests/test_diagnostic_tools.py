"""
Tests for agent diagnostic tools - kubectl-based evidence gathering.

These tests verify tool validation logic, not actual kubectl execution.
"""

import pytest
from unittest.mock import patch, MagicMock
from kubesentinel.agents import make_tools, KUBECTL_SAFE_VERBS, KUBECTL_WRITE_VERBS
from kubesentinel.models import InfraState


def test_kubectl_safe_verbs_defined():
    """Verify KUBECTL_SAFE_VERBS and KUBECTL_WRITE_VERBS are non-empty."""
    assert len(KUBECTL_SAFE_VERBS) > 0
    assert "get" in KUBECTL_SAFE_VERBS
    assert "describe" in KUBECTL_SAFE_VERBS
    assert "logs" in KUBECTL_SAFE_VERBS
    
    assert len(KUBECTL_WRITE_VERBS) > 0
    assert "delete" in KUBECTL_WRITE_VERBS
    assert "apply" in KUBECTL_WRITE_VERBS


def test_make_tools_includes_diagnostic_tools():
    """Verify make_tools() returns diagnostic tools."""
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
    
    tools = make_tools(state)
    tool_names = [t.name for t in tools]
    
    # Should have original 4 tools + 3 new diagnostic tools
    assert len(tools) == 7
    assert "get_cluster_summary" in tool_names
    assert "get_graph_summary" in tool_names
    assert "get_signals" in tool_names
    assert "get_risk_score" in tool_names
    assert "get_pod_logs" in tool_names
    assert "get_resource_yaml" in tool_names
    assert "kubectl_safe" in tool_names


@patch('subprocess.run')
def test_get_pod_logs_success(mock_run):
    """Test get_pod_logs() with successful log retrieval."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Error: OOMKilled\\nContainer terminated")
    
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
    
    tools = make_tools(state)
    get_pod_logs = next(t for t in tools if t.name == "get_pod_logs")
    
    result = get_pod_logs.invoke({"pod_name": "myapp-123", "namespace": "default", "tail_lines": 10})
    assert "OOMKilled" in result


@patch('subprocess.run')
def test_get_resource_yaml_success(mock_run):
    """Test get_resource_yaml() with successful YAML retrieval."""
    yaml_output = "apiVersion: apps/v1\\nkind: Deployment\\nmetadata:\\n  name: myapp"
    mock_run.return_value = MagicMock(returncode=0, stdout=yaml_output)
    
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
    
    tools = make_tools(state)
    get_resource_yaml = next(t for t in tools if t.name == "get_resource_yaml")
    
    result = get_resource_yaml.invoke({"resource_type": "deployment", "name": "myapp", "namespace": "default"})
    assert "apiVersion" in result
    assert "Deployment" in result


def test_kubectl_safe_rejects_unsafe_verbs():
    """Test kubectl_safe() rejects unsafe write verbs."""
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
    
    tools = make_tools(state)
    kubectl_safe = next(t for t in tools if t.name == "kubectl_safe")
    
    # Try unsafe verb
    result = kubectl_safe.invoke({"command_args": "delete pod myapp-123 -n default"})
    assert "Error" in result
    assert "not allowed" in result or "Verb" in result


def test_kubectl_safe_rejects_shell_metacharacters():
    """Test kubectl_safe() rejects shell metacharacters."""
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
    
    tools = make_tools(state)
    kubectl_safe = next(t for t in tools if t.name == "kubectl_safe")
    
    # Try command with pipe
    result = kubectl_safe.invoke({"command_args": "get pods | grep myapp"})
    assert "Error" in result
    assert "metacharacters" in result
    
    # Try command with semicolon
    result = kubectl_safe.invoke({"command_args": "get pods; echo hacked"})
    assert "Error" in result
    assert "metacharacters" in result


@patch('subprocess.run')
def test_kubectl_safe_allows_safe_verbs(mock_run):
    """Test kubectl_safe() allows safe read-only verbs."""
    mock_run.return_value = MagicMock(returncode=0, stdout="NAME    READY   STATUS\\nmyapp   1/1     Running")
    
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
    
    tools = make_tools(state)
    kubectl_safe = next(t for t in tools if t.name == "kubectl_safe")
    
    # Try safe verb
    result = kubectl_safe.invoke({"command_args": "get pods -n default"})
    assert "Running" in result or "NAME" in result
