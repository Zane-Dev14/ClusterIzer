"""
Tests for cost deterministic checks in agents.py
"""

from kubesentinel.agents import _deterministic_cost_check
from kubesentinel.models import InfraState


def test_cost_check_single_replica_deployments():
    """Cost check should detect single replica deployments as inefficiency."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": []
        },
        "graph_summary": {
            "single_replica_deployments": ["api", "worker", "cache", "db", "queue"]  # 5 deployments
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
    
    findings = _deterministic_cost_check(state)
    
    # Should detect single replica inefficiency (>3 deployments)
    assert len(findings) > 0, "Expected at least one finding for single replica deployments"
    single_replica_finding = next((f for f in findings if "single replica" in f.get("analysis", "").lower()), None)
    assert single_replica_finding is not None, "Expected finding about single replica deployments"
    assert single_replica_finding["severity"] == "medium"


def test_cost_check_underutilized_nodes():
    """Cost check should detect nodes with <30% utilization."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [
                {"name": "node-1", "cpu": "4000m", "memory": "8Gi"},
                {"name": "node-2", "cpu": "4000m", "memory": "8Gi"}
            ],
            "pods": [
                {
                    "name": "pod-1",
                    "namespace": "default",
                    "node_name": "node-1",
                    "containers": [
                        {
                            "name": "app",
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"}  # Only 2.5% of 4000m
                            }
                        }
                    ]
                },
                {
                    "name": "pod-2",
                    "namespace": "default",
                    "node_name": "node-2",
                    "containers": [
                        {
                            "name": "app",
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "512Mi"}  # Only 12.5% of 4000m
                            }
                        }
                    ]
                }
            ],
            "deployments": []
        },
        "graph_summary": {
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
    
    findings = _deterministic_cost_check(state)
    
    # Should detect underutilized nodes
    underutil_finding = next((f for f in findings if "under 30%" in f.get("analysis", "").lower()), None)
    assert underutil_finding is not None, "Expected finding about underutilized nodes"
    assert underutil_finding["severity"] == "high"


def test_cost_check_hpa_candidates():
    """Cost check should detect fixed-replica deployments as HPA candidates."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": [
                {"name": f"app-{i}", "namespace": "default", "replicas": 3}
                for i in range(10)  # 10 deployments with 3 replicas each
            ]
        },
        "graph_summary": {
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
    
    findings = _deterministic_cost_check(state)
    
    # Should suggest HPA for fixed replica workloads
    hpa_finding = next((f for f in findings if "autoscaling" in f.get("analysis", "").lower()), None)
    assert hpa_finding is not None, "Expected finding about HPA candidates"
    assert hpa_finding["severity"] == "low"


def test_cost_check_over_requested_resources():
    """Cost check should detect over-requested resources via signals."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [],
            "pods": [],
            "deployments": []
        },
        "graph_summary": {
            "single_replica_deployments": []
        },
        "signals": [
            {
                "category": "cost",
                "severity": "medium",
                "resource": "pod/app-1",
                "message": "Container over-requested CPU: requested 2000m, usage 200m"
            },
            {
                "category": "cost",
                "severity": "medium",
                "resource": "pod/app-2",
                "message": "Container over-provisioned memory"
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
    
    findings = _deterministic_cost_check(state)
    
    # Should detect over-requested resources
    # Filter for resource-related findings (not just any finding with "over")
    over_req_finding = next((f for f in findings if "resource" in f.get("resource", "").lower() 
                            and ("over" in f.get("analysis", "").lower() or "exceed" in f.get("analysis", "").lower())), None)
    assert over_req_finding is not None, f"Expected finding about over-requested resources, got findings: {findings}"
    assert over_req_finding["severity"] == "medium"


def test_cost_check_no_issues():
    """Cost check should return empty findings when no issues detected."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [
                {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
            ],
            "pods": [
                {
                    "name": "pod-1",
                    "namespace": "default",
                    "node_name": "node-1",
                    "containers": [
                        {
                            "name": "app",
                            "resources": {
                                "requests": {"cpu": "2000m", "memory": "4Gi"}  # 50% utilization
                            }
                        }
                    ]
                }
            ],
            "deployments": [
                {"name": "app", "namespace": "default", "replicas": 3}  # Multi-replica
            ]
        },
        "graph_summary": {
            "single_replica_deployments": []  # No single replicas
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
    
    findings = _deterministic_cost_check(state)
    
    # Should return minimal or no findings for a healthy cluster
    # (May have HPA suggestion if many deployments, but no critical issues)
    critical_findings = [f for f in findings if f.get("severity") in ["critical", "high"]]
    assert len(critical_findings) == 0, f"Expected no critical/high findings, got {critical_findings}"


def test_cost_check_combined_issues():
    """Cost check should detect multiple cost issues in one cluster."""
    state: InfraState = {
        "user_query": "cost",
        "cluster_snapshot": {
            "nodes": [
                {"name": "node-1", "cpu": "4000m", "memory": "8Gi"}
            ],
            "pods": [
                {
                    "name": "pod-1",
                    "namespace": "default",
                    "node_name": "node-1",
                    "containers": [
                        {
                            "name": "app",
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"}  # Low utilization
                            }
                        }
                    ]
                }
            ],
            "deployments": [
                {"name": f"app-{i}", "namespace": "default", "replicas": 1}
                for i in range(5)  # Many single-replica deployments
            ]
        },
        "graph_summary": {
            "single_replica_deployments": ["app-0", "app-1", "app-2", "app-3", "app-4"]
        },
        "signals": [
            {
                "category": "cost",
                "severity": "medium",
                "resource": "pod/app-1",
                "message": "Container over-requested resources"
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
    
    findings = _deterministic_cost_check(state)
    
    # Should detect multiple issues
    assert len(findings) >= 2, f"Expected multiple findings, got {len(findings)}"
    
    # Check that different categories of issues are detected
    finding_types = {f.get("resource", "").split("/")[1] for f in findings}
    assert len(finding_types) > 1, "Expected findings from multiple cost categories"
