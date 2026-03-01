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
        "final_report": ""
    }
    
    result = compute_risk(state)
    risk = result["risk_score"]
    
    assert risk["score"] == 0
    assert risk["grade"] == "A"
    assert risk["signal_count"] == 0


def test_single_critical_signal():
    """Test that one critical signal gives score 15, grade A."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {
                "category": "security",
                "severity": "critical",
                "resource": "deployment/default/test",
                "message": "Privileged container"
            }
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = compute_risk(state)
    risk = result["risk_score"]
    
    assert risk["score"] == 15
    assert risk["grade"] == "A"
    assert risk["signal_count"] == 1


def test_score_capped_at_100():
    """Test that score is capped at 100 even with many signals."""
    # Create many critical signals
    signals = []
    for i in range(20):
        signals.append({
            "category": "reliability",
            "severity": "critical",
            "resource": f"pod/default/pod-{i}",
            "message": "CrashLoopBackOff"
        })
    
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
        "final_report": ""
    }
    
    result = compute_risk(state)
    risk = result["risk_score"]
    
    # 20 critical signals = 20 * 15 = 300, but capped at 100
    assert risk["score"] == 100
    assert risk["grade"] == "F"
    assert risk["signal_count"] == 20


def test_grade_boundary_A_B():
    """Test grade boundary between A (0-29) and B (30-49)."""
    # 29 points: 1 critical (15) + 1 high (8) + 2 medium (6) = 29
    state_a: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {"category": "security", "severity": "critical", "resource": "test1", "message": "test"},
            {"category": "security", "severity": "high", "resource": "test2", "message": "test"},
            {"category": "security", "severity": "medium", "resource": "test3", "message": "test"},
            {"category": "security", "severity": "medium", "resource": "test4", "message": "test"},
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result_a = compute_risk(state_a)
    assert result_a["risk_score"]["score"] == 29
    assert result_a["risk_score"]["grade"] == "A"
    
    # 30 points: 2 critical (30) - should be B
    state_b: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {},
        "graph_summary": {},
        "signals": [
            {"category": "security", "severity": "critical", "resource": "test1", "message": "test"},
            {"category": "security", "severity": "critical", "resource": "test2", "message": "test"},
        ],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result_b = compute_risk(state_b)
    assert result_b["risk_score"]["score"] == 30
    assert result_b["risk_score"]["grade"] == "B"


def test_grade_boundary_C_D():
    """Test grade boundary between C (50-69) and D (70-89)."""
    # 69 points: 4 critical (60) + 1 high (8) + 1 low (1) = 69
    signals_c = [
        {"category": "security", "severity": "critical", "resource": "t1", "message": "t"},
        {"category": "security", "severity": "critical", "resource": "t2", "message": "t"},
        {"category": "security", "severity": "critical", "resource": "t3", "message": "t"},
        {"category": "security", "severity": "critical", "resource": "t4", "message": "t"},
        {"category": "security", "severity": "high", "resource": "t5", "message": "t"},
        {"category": "security", "severity": "low", "resource": "t6", "message": "t"},
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
        "final_report": ""
    }
    
    result_c = compute_risk(state_c)
    assert result_c["risk_score"]["score"] == 69
    assert result_c["risk_score"]["grade"] == "C"
    
    # 70 points: 4 critical (60) + 1 high (8) + 2 low (2) = 70
    signals_d = signals_c + [{"category": "cost", "severity": "low", "resource": "t7", "message": "t"}]
    
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
        "final_report": ""
    }
    
    result_d = compute_risk(state_d)
    assert result_d["risk_score"]["score"] == 70
    assert result_d["risk_score"]["grade"] == "D"


def test_grade_D_F_boundary():
    """Test grade boundary between D (70-89) and F (90-100)."""
    # 89 points
    signals = []
    for i in range(5):
        signals.append({"category": "security", "severity": "critical", "resource": f"t{i}", "message": "t"})
    # 5 * 15 = 75, need 14 more
    signals.append({"category": "security", "severity": "high", "resource": "t5", "message": "t"})  # +8 = 83
    signals.append({"category": "security", "severity": "high", "resource": "t6", "message": "t"})  # +8 = 91
    # That's actually 91, let me adjust
    signals = []
    for i in range(5):
        signals.append({"category": "security", "severity": "critical", "resource": f"t{i}", "message": "t"})
    # 5 * 15 = 75, need 14 more for 89
    signals.append({"category": "security", "severity": "high", "resource": "t5", "message": "t"})  # +8 = 83
    for i in range(6):
        signals.append({"category": "cost", "severity": "low", "resource": f"t{6+i}", "message": "t"})  # +6 = 89
    
    state_d: InfraState = {
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
        "final_report": ""
    }
    
    result_d = compute_risk(state_d)
    assert result_d["risk_score"]["score"] == 89
    assert result_d["risk_score"]["grade"] == "D"
    
    # 90 points: 6 critical = 90
    signals_f = []
    for i in range(6):
        signals_f.append({"category": "security", "severity": "critical", "resource": f"t{i}", "message": "t"})
    
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
        "final_report": ""
    }
    
    result_f = compute_risk(state_f)
    assert result_f["risk_score"]["score"] == 90
    assert result_f["risk_score"]["grade"] == "F"
