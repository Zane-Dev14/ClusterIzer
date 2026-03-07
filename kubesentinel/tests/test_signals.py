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
                    "container_statuses": [],
                }
            ],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    assert "signals" in result
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
                    "containers": [],
                }
            ],
            "pods": [],
            "services": [],
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": ["single-dep"],
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    assert "signals" in result
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
                            "limits": {},
                        }
                    ],
                }
            ],
            "pods": [],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    assert "signals" in result
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
                            "limits": {},
                        },
                        {
                            "name": "container-2",
                            "image": "redis:latest",
                            "privileged": False,
                            "requests": {},
                            "limits": {},
                        },
                    ],
                }
            ],
            "pods": [],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    assert "signals" in result
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
        deployments.append(
            {
                "name": f"dep-{i}",
                "namespace": "default",
                "replicas": 5,  # Will generate cost signal
                "containers": [
                    {
                        "name": "container",
                        "image": "nginx:latest",  # Will generate security signal
                        "privileged": False,
                        "requests": {},
                        "limits": {},  # Will generate cost + security signals
                    }
                ],
            }
        )

    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": deployments,
            "pods": [],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    assert "signals" in result
    signals = result["signals"]

    # Should be capped at MAX_SIGNALS
    assert len(signals) <= MAX_SIGNALS


def test_pending_pods_generate_signals():
    """Pending pods should generate unschedulable and namespace aggregate signals."""
    pods = []
    for i in range(6):
        pods.append(
            {
                "name": f"pending-{i}",
                "namespace": "social-network",
                "phase": "Pending",
                "node_name": "unscheduled",
                "crash_loop_backoff": False,
                "container_statuses": [],
                "owner_references": [],
            }
        )

    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": pods,
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    signals = result["signals"]

    pending = [s for s in signals if s.get("signal_id") == "pending_pod_unscheduled"]
    namespace_rollup = [
        s for s in signals if s.get("signal_id") == "pending_pods_namespace"
    ]
    assert len(pending) == 6
    assert len(namespace_rollup) == 1


def test_node_pressure_signals_are_generated():
    """Node condition signals should be emitted when pressure conditions are true."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [
                {
                    "name": "node-1",
                    "conditions": {"MemoryPressure": True, "Ready": False},
                    "allocatable_cpu_millicores": 2000,
                }
            ],
            "deployments": [],
            "pods": [],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    signals = result["signals"]
    ids = {s.get("signal_id") for s in signals}

    assert "memory_pressure" in ids
    assert "node_not_ready" in ids


def test_replica_imbalance_signal_generated():
    """Replica mismatch should be detected from desired vs running pods."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "api",
                    "namespace": "social-network",
                    "replicas": 3,
                    "containers": [],
                }
            ],
            "pods": [
                {
                    "name": "api-1",
                    "namespace": "social-network",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": [],
                    "owner_references": [],
                }
            ],
            "services": [],
        },
        "graph_summary": {
            "orphan_services": [],
            "single_replica_deployments": [],
            "ownership_index": {
                "social-network/api-1": {
                    "replicaset": "social-network/api-rs",
                    "deployment": "social-network/api",
                    "statefulset": None,
                    "top_controller": "social-network/api",
                }
            },
        },
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    signals = result["signals"]
    imbalance = [s for s in signals if s.get("signal_id") == "replica_imbalance"]

    assert len(imbalance) == 1
    assert "desired 3, running 1" in imbalance[0]["message"]


def test_crashloop_with_diagnosis():
    """Test that crashloop pods with crash_logs get automatic diagnosis attached."""
    crash_log_with_lua_error = """
2026/03/07 06:41:41 [error] 1#1: failed to initialize Lua VM in /usr/local/openresty/nginx/conf/nginx.conf:123
nginx: [error] failed to initialize Lua VM in /usr/local/openresty/nginx/conf/nginx.conf:123
    """

    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": [
                {
                    "name": "media-frontend-abc",
                    "namespace": "social-network",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": True,
                    "container_statuses": [
                        {
                            "name": "nginx",
                            "ready": False,
                            "restart_count": 5,
                            "state": "CrashLoopBackOff",
                        }
                    ],
                    "crash_logs": {
                        "nginx": crash_log_with_lua_error,
                    },
                }
            ],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    signals = result["signals"]

    # Should have CrashLoopBackOff signal
    crash_signals = [s for s in signals if s.get("signal_id") == "crashloop_pod"]
    assert len(crash_signals) == 1

    crash_signal = crash_signals[0]

    # Should have diagnosis attached
    assert "diagnosis" in crash_signal, "Crashloop signal should have diagnosis field"
    diagnosis = crash_signal["diagnosis"]

    # Verify diagnosis structure
    assert diagnosis["type"] == "nginx_lua_init_fail"
    assert diagnosis["confidence"] >= 0.85
    assert "Lua" in diagnosis["root_cause"]
    assert (
        "nginx" in diagnosis["evidence"].lower()
        or "lua" in diagnosis["evidence"].lower()
    )
    assert len(diagnosis["fix_plan"]) > 0
    assert "container" in diagnosis
    assert diagnosis["container"] == "nginx"

    # Verify fix plan structure
    first_step = diagnosis["fix_plan"][0]
    assert "step_number" in first_step
    assert "description" in first_step
    assert first_step["step_number"] == 1

    # At least one step should have a command
    commands_present = any(step.get("command") for step in diagnosis["fix_plan"])
    assert commands_present, "At least one fix step should have a command"


def test_crashloop_without_diagnosis():
    """Test that crashloop pods without crash_logs don't have diagnosis."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": [
                {
                    "name": "crashing-pod",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": True,
                    "container_statuses": [
                        {
                            "name": "app",
                            "ready": False,
                            "restart_count": 3,
                            "state": "CrashLoopBackOff",
                        }
                    ],
                    # No crash_logs field
                }
            ],
            "services": [],
        },
        "graph_summary": {"orphan_services": [], "single_replica_deployments": []},
        "signals": [],
        "risk_score": {},
        "planner_decision": [],
        "failure_findings": [],
        "cost_findings": [],
        "security_findings": [],
        "strategic_summary": "",
        "final_report": "",
    }

    result = generate_signals(state)
    signals = result["signals"]

    # Should have CrashLoopBackOff signal
    crash_signals = [s for s in signals if s.get("signal_id") == "crashloop_pod"]
    assert len(crash_signals) == 1

    crash_signal = crash_signals[0]

    # Should NOT have diagnosis (no crash_logs available)
    assert "diagnosis" not in crash_signal or crash_signal["diagnosis"] is None
