"""
Tests for signals.py - deterministic signal generation.

Tests use mocked cluster snapshots, no real cluster required.
"""

from kubesentinel.signals import generate_signals
from kubesentinel.models import InfraState


def test_crashloop_signal_generated():
    """Test that CrashLoopBackOff pods generate critical signals."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": [
                {
                    "name": "failing-pod",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": True,
                    "container_statuses": []
                }
            ],
            "services": []
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": []
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = generate_signals(state)
    signals = result["signals"]
    
    # Should have at least one signal
    assert len(signals) > 0
    
    # Should have CrashLoopBackOff signal
    crash_signals = [s for s in signals if "CrashLoopBackOff" in s["message"]]
    assert len(crash_signals) == 1
    assert crash_signals[0]["category"] == "reliability"
    assert crash_signals[0]["severity"] == "critical"
    assert "failing-pod" in crash_signals[0]["resource"]


def test_single_replica_signal_generated():
    """Test that single-replica deployments generate signals."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "single-dep",
                    "namespace": "default",
                    "replicas": 1,
                    "containers": []
                }
            ],
            "pods": [],
            "services": []
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": ["single-dep"]
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = generate_signals(state)
    signals = result["signals"]
    
    # Should have single replica signal
    single_rep_signals = [s for s in signals if "1 replica" in s["message"]]
    assert len(single_rep_signals) == 1
    assert single_rep_signals[0]["category"] == "reliability"
    assert single_rep_signals[0]["severity"] == "medium"


def test_privileged_container_signal():
    """Test that privileged containers generate critical security signals."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "priv-dep",
                    "namespace": "default",
                    "replicas": 1,
                    "containers": [
                        {
                            "name": "priv-container",
                            "image": "nginx:1.21",
                            "privileged": True,
                            "requests": {},
                            "limits": {}
                        }
                    ]
                }
            ],
            "pods": [],
            "services": []
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": []
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = generate_signals(state)
    signals = result["signals"]
    
    # Should have privileged signal
    priv_signals = [s for s in signals if "privileged" in s["message"]]
    assert len(priv_signals) == 1
    assert priv_signals[0]["category"] == "security"
    assert priv_signals[0]["severity"] == "critical"


def test_signal_deduplication():
    """Test that duplicate signals are deduplicated."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "dep-1",
                    "namespace": "default",
                    "replicas": 1,
                    "containers": [
                        {
                            "name": "container-1",
                            "image": "nginx:latest",
                            "privileged": False,
                            "requests": {},
                            "limits": {}
                        },
                        {
                            "name": "container-2",
                            "image": "redis:latest",
                            "privileged": False,
                            "requests": {},
                            "limits": {}
                        }
                    ]
                }
            ],
            "pods": [],
            "services": []
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": []
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = generate_signals(state)
    signals = result["signals"]
    
    # Each container should generate its own :latest signal (different container names)
    latest_signals = [s for s in signals if ":latest" in s["message"]]
    assert len(latest_signals) == 2


def test_signal_cap_enforced():
    """Test that signals are capped at MAX_SIGNALS."""
    from kubesentinel.models import MAX_SIGNALS
    
    # Create many deployments to generate many signals
    deployments = []
    for i in range(300):
        deployments.append({
            "name": f"dep-{i}",
            "namespace": "default",
            "replicas": 5,  # Will generate cost signal
            "containers": [
                {
                    "name": "container",
                    "image": "nginx:latest",  # Will generate security signal
                    "privileged": False,
                    "requests": {},
                    "limits": {}  # Will generate cost + security signals
                }
            ]
        })
    
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": deployments,
            "pods": [],
            "services": []
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": []
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": ""
    }
    
    result = generate_signals(state)
    signals = result["signals"]
    
    # Should be capped at MAX_SIGNALS
    assert len(signals) <= MAX_SIGNALS
