"""
Tests for planner routing logic in agents.py
"""

from kubesentinel.agents import planner_node
from kubesentinel.models import InfraState


def test_planner_cost_query():
    """Planner should route cost queries to cost_agent only."""
    state: InfraState = {
        "user_query": "How can we reduce costs?",
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
    assert "planner_decision" in result
    assert result["planner_decision"] == ["cost_agent"], (
        f"Expected ['cost_agent'], got {result['planner_decision']}"
    )


def test_planner_security_query():
    """Planner should route security queries to security_agent only."""
    state: InfraState = {
        "user_query": "What are the security vulnerabilities?",
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
    assert result["planner_decision"] == ["security_agent"], (
        f"Expected ['security_agent'], got {result['planner_decision']}"
    )


def test_planner_reliability_query():
    """Planner should route reliability queries to failure_agent only."""
    state: InfraState = {
        "user_query": "What are the reliability risks?",
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
    assert result["planner_decision"] == ["failure_agent"], (
        f"Expected ['failure_agent'], got {result['planner_decision']}"
    )


def test_planner_node_query():
    """Planner should route node queries to failure_agent."""
    state: InfraState = {
        "user_query": "Analyze node pressure and memory",
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
    assert result["planner_decision"] == ["failure_agent"], (
        f"Expected ['failure_agent'], got {result['planner_decision']}"
    )


def test_planner_architecture_query():
    """Planner should route architecture queries to all agents."""
    state: InfraState = {
        "user_query": "Full architecture analysis",
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
    expected = ["failure_agent", "cost_agent", "security_agent"]
    assert set(result["planner_decision"]) == set(expected), (
        f"Expected all agents, got {result['planner_decision']}"
    )


def test_planner_multi_category_query():
    """Planner should route queries matching multiple categories to all matching agents."""
    state: InfraState = {
        "user_query": "Analyze cost and security issues",
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
    assert "cost_agent" in result["planner_decision"], (
        f"Expected cost_agent in {result['planner_decision']}"
    )
    assert "security_agent" in result["planner_decision"], (
        f"Expected security_agent in {result['planner_decision']}"
    )
    assert "failure_agent" not in result["planner_decision"], (
        f"Did not expect failure_agent in {result['planner_decision']}"
    )


def test_planner_generic_query_defaults_to_failure():
    """Planner should default to failure_agent for generic queries."""
    state: InfraState = {
        "user_query": "Tell me about the cluster",
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
    assert result["planner_decision"] == ["failure_agent"], (
        f"Expected default ['failure_agent'], got {result['planner_decision']}"
    )


def test_planner_cli_override():
    """Planner should respect CLI override and not change planner_decision."""
    state: InfraState = {
        "user_query": "Full architecture analysis",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [],
        "risk_score": {},
        "planner_decision": ["cost_agent"],  # Pre-set by CLI
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = planner_node(state)
    assert result["planner_decision"] == ["cost_agent"], (
        f"Expected CLI override ['cost_agent'], got {result['planner_decision']}"
    )


def test_planner_deduplication():
    """Planner should deduplicate agent selections."""
    state: InfraState = {
        "user_query": "Node failure and reliability and outage risks",  # Multiple reliability keywords
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
    # Should only have failure_agent once, not duplicated
    assert result["planner_decision"].count("failure_agent") == 1, (
        f"Expected no duplicates: {result['planner_decision']}"
    )


def test_planner_top_risks_query_routes_all_agents():
    """Executive top-risks query should trigger multi-agent analysis."""
    state: InfraState = {
        "user_query": "If this cluster were production, what are the top 5 risks?",
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
    expected = {"failure_agent", "cost_agent", "security_agent"}
    assert set(result["planner_decision"]) == expected


def test_planner_fix_first_query_routes_all_agents():
    """Fix-priority query should not collapse to only failure_agent."""
    state: InfraState = {
        "user_query": "Why are pods pending and what should I fix first?",
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
    expected = {"failure_agent", "cost_agent", "security_agent"}
    assert set(result["planner_decision"]) == expected
