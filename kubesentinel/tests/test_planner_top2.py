"""
Tests for planner_node() - deterministic agent selection (top-2 agents).

Tests verify that planner selects agents based on query keywords.
"""

from kubesentinel.agents import planner_node
from kubesentinel.models import InfraState


def test_planner_architecture_query_selects_all_agents():
    """Test that 'architecture' queries select all 3 agents."""
    state: InfraState = {
        "user_query": "provide a comprehensive architecture analysis",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Architecture query should run all 3 agents
    assert len(agents) == 3
    assert "failure_agent" in agents
    assert "cost_agent" in agents
    assert "security_agent" in agents


def test_planner_cost_query_selects_cost_agent():
    """Test that cost queries select cost_agent."""
    state: InfraState = {
        "user_query": "what are the biggest cost optimization opportunities",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Should select top 2, cost_agent must be one of them
    assert len(agents) <= 2
    assert "cost_agent" in agents


def test_planner_security_query_selects_security_agent():
    """Test that security queries select security_agent."""
    state: InfraState = {
        "user_query": "audit security vulnerabilities and cis compliance",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Should select top 2, security_agent must be one of them
    assert len(agents) <= 2
    assert "security_agent" in agents


def test_planner_reliability_query_selects_failure_agent():
    """Test that reliability queries select failure_agent."""
    state: InfraState = {
        "user_query": "identify reliability risks and outage prevention",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Should select top 2, failure_agent must be one of them
    assert len(agents) <= 2
    assert "failure_agent" in agents


def test_planner_selects_top_2_max():
    """Test that planner never selects more than 2 agents for non-architecture queries."""
    state: InfraState = {
        "user_query": "cost optimization, security audit, and reliability risks",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Should select at most 2 agents
    assert len(agents) <= 2
    # But should have selected something
    assert len(agents) > 0


def test_planner_defaults_to_failure_agent():
    """Test that generic queries default to failure_agent."""
    state: InfraState = {
        "user_query": "analyze this cluster",
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
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Generic query should default to failure_agent
    assert len(agents) >= 1
    assert "failure_agent" in agents


def test_planner_respects_cli_override():
    """Test that planner respects CLI override."""
    state: InfraState = {
        "user_query": "cost analysis",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [],
        "risk_score": {},
        "planner_decision": ["custom_agent"],  # Pre-set by CLI
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }
    
    result = planner_node(state)
    agents = result["planner_decision"]
    
    # Should keep the pre-set value
    assert agents == ["custom_agent"]
