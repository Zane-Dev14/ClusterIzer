"""
Tests for graph_builder.py - dependency graph construction.

Tests use mocked cluster snapshots, no real cluster required.
"""

from kubesentinel.graph_builder import build_graph
from kubesentinel.models import InfraState


def test_deployment_to_pods_mapping():
    """Test that deployments are correctly mapped to their pods."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "nginx-deployment",
                    "namespace": "default",
                    "replicas": 3,
                    "containers": []
                }
            ],
            "pods": [
                {
                    "name": "nginx-deployment-abc123",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "nginx-deployment-def456",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "other-pod",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-2",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                }
            ],
            "services": []
        },
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
    
    result = build_graph(state)
    graph = result["graph_summary"]
    
    dep_to_pods = graph["deployment_to_pods"]
    assert "default/nginx-deployment" in dep_to_pods
    assert len(dep_to_pods["default/nginx-deployment"]) == 2
    assert "default/nginx-deployment-abc123" in dep_to_pods["default/nginx-deployment"]
    assert "default/nginx-deployment-def456" in dep_to_pods["default/nginx-deployment"]


def test_orphan_service_detection():
    """Test that services with no matching deployments are detected as orphans."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [
                {
                    "name": "existing-deployment",
                    "namespace": "default",
                    "replicas": 1,
                    "containers": []
                }
            ],
            "pods": [],
            "services": [
                {
                    "name": "orphan-service",
                    "namespace": "default",
                    "type": "ClusterIP",
                    "selector": {"app": "nonexistent"}
                },
                {
                    "name": "connected-service",
                    "namespace": "default",
                    "type": "ClusterIP",
                    "selector": {"app": "exists"}
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
        "final_report": ""
    }
    
    result = build_graph(state)
    graph = result["graph_summary"]
    
    # Both services are orphans because there are no pods
    orphans = graph["orphan_services"]
    assert len(orphans) == 2
    assert "orphan-service" in orphans or "connected-service" in orphans


def test_single_replica_detection():
    """Test that single-replica deployments are correctly identified."""
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
                },
                {
                    "name": "multi-dep",
                    "namespace": "default",
                    "replicas": 3,
                    "containers": []
                },
                {
                    "name": "another-single",
                    "namespace": "kube-system",
                    "replicas": 1,
                    "containers": []
                }
            ],
            "pods": [],
            "services": []
        },
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
    
    result = build_graph(state)
    graph = result["graph_summary"]
    
    single_replica = graph["single_replica_deployments"]
    assert len(single_replica) == 2
    assert "single-dep" in single_replica
    assert "another-single" in single_replica
    assert "multi-dep" not in single_replica


def test_node_fanout_count():
    """Test that pods per node are correctly counted."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [
                {
                    "name": "node-1",
                    "allocatable_cpu": "4",
                    "allocatable_memory": "8Gi"
                },
                {
                    "name": "node-2",
                    "allocatable_cpu": "4",
                    "allocatable_memory": "8Gi"
                }
            ],
            "deployments": [],
            "pods": [
                {
                    "name": "pod-1",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "pod-2",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "pod-3",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "pod-4",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-2",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "pod-5",
                    "namespace": "default",
                    "phase": "Pending",
                    "node_name": "unscheduled",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                }
            ],
            "services": []
        },
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
    
    result = build_graph(state)
    graph = result["graph_summary"]
    
    fanout = graph["node_fanout_count"]
    assert fanout["node-1"] == 3
    assert fanout["node-2"] == 1
    # Unscheduled pods should not be counted
    assert "unscheduled" not in fanout


def test_pod_to_node_mapping():
    """Test that pods are correctly mapped to their nodes."""
    state: InfraState = {
        "user_query": "test",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": [
                {
                    "name": "pod-a",
                    "namespace": "default",
                    "phase": "Running",
                    "node_name": "node-1",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                },
                {
                    "name": "pod-b",
                    "namespace": "kube-system",
                    "phase": "Running",
                    "node_name": "node-2",
                    "crash_loop_backoff": False,
                    "container_statuses": []
                }
            ],
            "services": []
        },
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
    
    result = build_graph(state)
    graph = result["graph_summary"]
    
    pod_to_node = graph["pod_to_node"]
    assert pod_to_node["default/pod-a"] == "node-1"
    assert pod_to_node["kube-system/pod-b"] == "node-2"
