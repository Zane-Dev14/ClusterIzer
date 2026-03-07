"""
Tests for risk.py - risk score computation.

Tests use mocked signals, no real cluster required.
"""

from kubesentinel.risk import compute_risk
from kubesentinel.models import InfraState


def test_empty_signals_score_zero():
    """Test that empty signals result in score 0, grade A."""
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

    result = compute_risk(state)
    assert "risk_score" in result
    risk = result["risk_score"]

    assert risk["score"] == 0
    assert risk["grade"] == "A"
    assert risk["signal_count"] == 0


def test_single_critical_signal():
    """Test that one critical signal gives score 30 (15*2.0 security multiplier), grade A."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {
                "category": "security",
                "severity": "critical",
                "resource": "deployment/default/test",
                "message": "Privileged container",
            }
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = compute_risk(state)
    assert "risk_score" in result
    risk = result["risk_score"]

    assert risk["score"] == 30  # critical (15) * security multiplier (2.0)
    assert risk["grade"] == "A"  # 30 is A grade threshold (0-34)
    assert risk["signal_count"] == 1


def test_score_capped_at_100():
    """Test that score is properly normalized with distribution-aware formula."""
    # Create many critical signals
    signals = []
    for i in range(20):
        signals.append(
            {
                "category": "reliability",
                "severity": "critical",
                "resource": f"pod/default/pod-{i}",
                "message": "CrashLoopBackOff",
            }
        )

    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": signals,
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = compute_risk(state)
    assert "risk_score" in result
    risk = result["risk_score"]

    # 20 critical signals with 100% severity ratio: score should be high but differentiated
    # severity_component = (20*15*1.8/20)*3 = 81, volume = 10 → 91
    assert risk["score"] == 91
    assert risk["grade"] == "F"
    assert risk["signal_count"] == 20


def test_grade_boundary_A_B():
    """Test grade boundary between A (0-34) and B (35-54) with contextual weighting."""
    # A grade: 1 reliability critical = 15 * 1.8 = 27
    state_a: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {
                "category": "reliability",
                "severity": "critical",
                "resource": "test1",
                "message": "test",
            },
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_a = compute_risk(state_a)
    assert "risk_score" in result_a
    assert result_a["risk_score"]["score"] == 27
    assert result_a["risk_score"]["grade"] == "A"

    # B grade: 1 security critical (30) + 1 cost critical (15*0.5=7.5) = 37 (int)
    state_b: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {
                "category": "security",
                "severity": "critical",
                "resource": "test1",
                "message": "test",
            },
            {
                "category": "cost",
                "severity": "critical",
                "resource": "test2",
                "message": "test",
            },
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_b = compute_risk(state_b)
    assert "risk_score" in result_b
    assert result_b["risk_score"]["score"] == 37
    assert result_b["risk_score"]["grade"] == "B"


def test_grade_boundary_C_D():
    """Test grade boundary between C (55-74) and D (75-89) with contextual weighting."""
    # C grade: 2 security critical = 2 * 15 * 2.0 = 60
    signals_c = [
        {
            "category": "security",
            "severity": "critical",
            "resource": "t1",
            "message": "t",
        },
        {
            "category": "security",
            "severity": "critical",
            "resource": "t2",
            "message": "t",
        },
    ]

    state_c: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": signals_c,
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_c = compute_risk(state_c)
    assert "risk_score" in result_c
    assert result_c["risk_score"]["score"] == 60
    assert result_c["risk_score"]["grade"] == "C"

    # D grade: 2 security critical (60) + 1 reliability high (14.4) + 1 cost high (4) = 78
    signals_d = signals_c + [
        {
            "category": "reliability",
            "severity": "high",
            "resource": "t3",
            "message": "t",
        },
        {"category": "cost", "severity": "high", "resource": "t4", "message": "t"},
    ]

    state_d: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": signals_d,
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_d = compute_risk(state_d)
    assert "risk_score" in result_d
    assert result_d["risk_score"]["score"] == 78
    assert result_d["risk_score"]["grade"] == "D"


def test_grade_D_F_boundary():
    """Test grade boundary between D (70-89) and F (90-100) with contextual weighting."""
    # D grade: 3 reliability critical = 3 * 15 * 1.8 = 81
    signals_d = [
        {
            "category": "reliability",
            "severity": "critical",
            "resource": f"t{i}",
            "message": "t",
        }
        for i in range(3)
    ]

    state_d: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": signals_d,
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_d = compute_risk(state_d)
    assert "risk_score" in result_d
    assert result_d["risk_score"]["score"] == 81
    assert result_d["risk_score"]["grade"] == "D"

    # F grade: 3 security critical = 3 * 15 * 2.0 = 90
    signals_f = [
        {
            "category": "security",
            "severity": "critical",
            "resource": f"t{i}",
            "message": "t",
        }
        for i in range(3)
    ]

    state_f: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": signals_f,
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result_f = compute_risk(state_f)
    assert "risk_score" in result_f
    assert result_f["risk_score"]["score"] == 90
    assert result_f["risk_score"]["grade"] == "F"


def test_risk_includes_top_risks_structure():
    """Risk output should include deterministic top risk ranking."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {
                "signal_id": "pending_pod_unscheduled",
                "category": "reliability",
                "severity": "high",
                "resource": "pod/social-network/a",
                "message": "Pod is Pending and unschedulable",
            },
            {
                "signal_id": "pending_pod_unscheduled",
                "category": "reliability",
                "severity": "high",
                "resource": "pod/social-network/b",
                "message": "Pod is Pending and unschedulable",
            },
            {
                "signal_id": "crashloop_pod",
                "category": "reliability",
                "severity": "critical",
                "resource": "pod/social-network/c",
                "message": "Pod in CrashLoopBackOff state",
            },
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = compute_risk(state)
    top_risks = result["risk_score"].get("top_risks", [])

    assert isinstance(top_risks, list)
    assert len(top_risks) > 0
    first = top_risks[0]
    assert "id" in first
    assert "title" in first
    assert "affected_count" in first
    assert "first_fix" in first
