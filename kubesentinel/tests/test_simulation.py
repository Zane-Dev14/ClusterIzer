"""
Tests for node failure simulation in simulation.py
"""

from kubesentinel.simulation import simulate_node_failure


def test_simulate_node_failure_single_replica():
    """Simulation should detect critical outage for single-replica deployments."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"},
            {"name": "node-2", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "api-abc123",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [
            {
                "name": "api",
                "namespace": "default",
                "replicas": 1
            }
        ],
        "statefulsets": [],
        "services": [
            {
                "name": "api",
                "namespace": "default"
            }
        ]
    }
    
    graph_summary = {
        "ownership_index": {
            "default/api-abc123": {
                "owner_kind": "Deployment",
                "owner_name": "api"
            }
        },
        "service_to_deployment": {
            "default/api": "default/api"
        },
        "deployment_to_pods": {
            "default/api": ["default/api-abc123"]
        }
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    assert result["node"] == "node-1"
    assert result["impact_severity"] == "critical", f"Expected critical impact, got {result['impact_severity']}"
    assert len(result["affected_pods"]) == 1
    assert len(result["affected_workloads"]) == 1
    
    # Check workload impact
    workload = result["affected_workloads"][0]
    assert workload["name"] == "api"
    assert workload["impact"] == "critical"
    assert "Single replica" in workload["reason"] or "outage" in workload["reason"].lower()


def test_simulate_node_failure_multi_replica():
    """Simulation should detect degradation for multi-replica deployments."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"},
            {"name": "node-2", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "api-pod-1",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [
            {
                "name": "api",
                "namespace": "default",
                "replicas": 5
            }
        ],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {
            "default/api-pod-1": {
                "owner_kind": "Deployment",
                "owner_name": "api"
            }
        },
        "service_to_deployment": {},
        "deployment_to_pods": {
            "default/api": ["default/api-pod-1"]
        }
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert result["impact_severity"] in ["medium", "low"], f"Expected medium/low impact for 5 replicas, got {result['impact_severity']}"
    assert len(result["affected_workloads"]) == 1
    
    workload = result["affected_workloads"][0]
    assert workload["replicas"] == 5
    assert workload["impact"] in ["medium", "low"]


def test_simulate_node_failure_no_pods():
    """Simulation should handle nodes with no pods gracefully."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [],
        "deployments": [],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {},
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert result["impact_severity"] == "none"
    assert len(result["affected_pods"]) == 0
    assert "no scheduled pods" in result["summary"].lower()


def test_simulate_node_failure_nonexistent_node():
    """Simulation should report error for non-existent nodes."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [],
        "deployments": [],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {},
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-999")
    
    assert "error" in result
    assert "not found" in result["error"].lower()
    assert "available_nodes" in result
    assert "node-1" in result["available_nodes"]


def test_simulate_node_failure_orphan_pod():
    """Simulation should detect orphan pods as critical."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "orphan-pod",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {},  # No owner for the pod
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert result["impact_severity"] == "critical"
    assert len(result["affected_workloads"]) == 1
    
    workload = result["affected_workloads"][0]
    assert workload["type"] == "pod"
    assert workload["impact"] == "critical"
    assert "orphan" in workload["reason"].lower() or "no controller" in workload["reason"].lower()


def test_simulate_node_failure_statefulset():
    """Simulation should handle StatefulSet workloads."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "db-0",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [],
        "statefulsets": [
            {
                "name": "db",
                "namespace": "default",
                "replicas": 1
            }
        ],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {
            "default/db-0": {
                "owner_kind": "StatefulSet",
                "owner_name": "db"
            }
        },
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert result["impact_severity"] == "critical"
    assert len(result["affected_workloads"]) == 1
    
    workload = result["affected_workloads"][0]
    assert workload["type"] == "StatefulSet"
    assert workload["name"] == "db"
    assert workload["impact"] == "critical"


def test_simulate_node_failure_multiple_workloads():
    """Simulation should handle multiple workloads on same node."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "api-pod",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            },
            {
                "name": "worker-pod",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [
            {"name": "api", "namespace": "default", "replicas": 1},
            {"name": "worker", "namespace": "default", "replicas": 3}
        ],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {
            "default/api-pod": {
                "owner_kind": "Deployment",
                "owner_name": "api"
            },
            "default/worker-pod": {
                "owner_kind": "Deployment",
                "owner_name": "worker"
            }
        },
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert len(result["affected_pods"]) == 2
    assert len(result["affected_workloads"]) == 2
    
    # Should have different impact levels
    impacts = {w["impact"] for w in result["affected_workloads"]}
    assert "critical" in impacts  # api with 1 replica
    assert len(impacts) > 1  # worker with 3 replicas should have different impact


def test_simulate_node_failure_recommendations():
    """Simulation should generate actionable recommendations."""
    cluster_snapshot = {
        "nodes": [
            {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
        ],
        "pods": [
            {
                "name": "api-pod",
                "namespace": "default",
                "node_name": "node-1",
                "containers": []
            }
        ],
        "deployments": [
            {"name": "api", "namespace": "default", "replicas": 1}
        ],
        "statefulsets": [],
        "services": []
    }
    
    graph_summary = {
        "ownership_index": {
            "default/api-pod": {
                "owner_kind": "Deployment",
                "owner_name": "api"
            }
        },
        "service_to_deployment": {},
        "deployment_to_pods": {}
    }
    
    result = simulate_node_failure(cluster_snapshot, graph_summary, "node-1")
    
    assert "recommendations" in result
    assert len(result["recommendations"]) > 0
    
    # Should recommend increasing replicas for critical workloads
    has_replica_recommendation = any(
        "replica" in rec.lower() for rec in result["recommendations"]
    )
    assert has_replica_recommendation, "Expected recommendation about replica count"
