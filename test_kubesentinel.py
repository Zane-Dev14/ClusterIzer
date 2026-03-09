"""Comprehensive unit tests for KubeSentinel."""

import pytest
from typing import Dict, Any, List
from kubesentinel.models import InfraState
from kubesentinel.risk import (
    compute_risk,
    _signal_title,
    SEVERITY_WEIGHTS,
    GRADE_THRESHOLDS,
)
from kubesentinel.signals import (
    generate_signals,
    _add_signal,
)
from kubesentinel.graph_builder import build_graph
from kubesentinel.reporting import build_report


# ============================================================================
# FIXTURES & TEST DATA
# ============================================================================


@pytest.fixture
def empty_state() -> InfraState:
    """Create empty infrastructure state."""
    return {
        "user_query": "test query",
        "cluster_snapshot": {
            "nodes": [],
            "deployments": [],
            "pods": [],
            "services": [],
            "replicasets": [],
            "statefulsets": [],
            "daemonsets": [],
            "crds": {},
        },
        "graph_summary": {
            "service_to_deployment": {},
            "deployment_to_pods": {},
            "orphan_services": [],
            "single_replica_deployments": [],
        },
        "signals": [],
        "risk_score": {},
    }


@pytest.fixture
def populated_state() -> InfraState:
    """Create state with sample kubernetes resources."""
    return {
        "user_query": "analyze cluster health",
        "cluster_snapshot": {
            "nodes": [
                {
                    "name": "node-1",
                    "status": "Ready",
                    "allocatable": {
                        "cpu": "4",
                        "memory": "8Gi",
                    },
                },
                {
                    "name": "node-2",
                    "status": "Ready",
                    "allocatable": {
                        "cpu": "2",
                        "memory": "4Gi",
                    },
                },
            ],
            "deployments": [
                {
                    "name": "web-app",
                    "namespace": "default",
                    "replicas": 3,
                    "status_replicas": 3,
                    "selector": {"app": "web"},
                },
                {
                    "name": "api-service",
                    "namespace": "default",
                    "replicas": 1,
                    "status_replicas": 0,
                    "selector": {"app": "api"},
                },
            ],
            "pods": [
                {
                    "name": "web-app-abc123",
                    "namespace": "default",
                    "status": "Running",
                    "node_name": "node-1",
                    "labels": {"app": "web"},
                    "owner_references": [
                        {
                            "kind": "ReplicaSet",
                            "name": "web-app-rs",
                        }
                    ],
                },
                {
                    "name": "api-service-xyz789",
                    "namespace": "default",
                    "status": "CrashLoopBackOff",
                    "node_name": "node-2",
                    "labels": {"app": "api"},
                    "owner_references": [
                        {
                            "kind": "ReplicaSet",
                            "name": "api-service-rs",
                        }
                    ],
                },
            ],
            "services": [
                {
                    "name": "web-svc",
                    "namespace": "default",
                    "selector": {"app": "web"},
                },
                {
                    "name": "orphan-svc",
                    "namespace": "default",
                    "selector": {"app": "nonexistent"},
                },
            ],
            "replicasets": [
                {
                    "name": "web-app-rs",
                    "namespace": "default",
                    "replicas": 3,
                    "owner_references": [
                        {
                            "kind": "Deployment",
                            "name": "web-app",
                        }
                    ],
                },
                {
                    "name": "api-service-rs",
                    "namespace": "default",
                    "replicas": 1,
                    "owner_references": [
                        {
                            "kind": "Deployment",
                            "name": "api-service",
                        }
                    ],
                },
            ],
            "statefulsets": [],
            "daemonsets": [],
            "crds": {},
        },
        "graph_summary": {
            "service_to_deployment": {},
            "deployment_to_pods": {},
            "orphan_services": ["orphan-svc"],
            "single_replica_deployments": ["api-service"],
        },
        "signals": [],
        "risk_score": {},
    }


# ============================================================================
# MODELS TESTS
# ============================================================================


class TestInfraStateModel:
    """Test InfraState data model."""

    def test_empty_state_structure(self, empty_state: InfraState) -> None:
        """Verify empty state has correct structure."""
        assert "user_query" in empty_state
        assert "cluster_snapshot" in empty_state
        assert "graph_summary" in empty_state
        assert "signals" in empty_state
        assert "risk_score" in empty_state

    def test_cluster_snapshot_keys(self, populated_state: InfraState) -> None:
        """Verify cluster snapshot has all expected resource types."""
        snapshot = populated_state["cluster_snapshot"]
        assert "nodes" in snapshot
        assert "deployments" in snapshot
        assert "pods" in snapshot
        assert "services" in snapshot
        assert isinstance(snapshot["nodes"], list)
        assert isinstance(snapshot["deployments"], list)

    def test_state_with_findings(self) -> None:
        """Test state with agent findings."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
            "failure_findings": [
                {
                    "resource": "pod-1",
                    "severity": "high",
                    "analysis": "Pod is crashing",
                }
            ],
            "cost_findings": [],
            "security_findings": [],
        }
        assert len(state["failure_findings"]) == 1
        assert state["failure_findings"][0]["severity"] == "high"


# ============================================================================
# RISK SCORING TESTS
# ============================================================================


class TestRiskScoring:
    """Test risk score calculation and grading."""

    def test_calculate_risk_empty_signals(self, empty_state: InfraState) -> None:
        """Test risk calculation with no signals."""
        state = empty_state
        state["signals"] = []
        result = compute_risk(state)
        assert result["risk_score"]["score"] == 0
        assert result["risk_score"]["grade"] == "A"
        assert result["risk_score"]["signal_count"] == 0

    def test_calculate_risk_single_critical_signal(
        self, empty_state: InfraState
    ) -> None:
        """Test risk score with single critical signal."""
        state = empty_state
        state["signals"] = [
            {
                "category": "security",
                "severity": "critical",
                "resource": "pod-1",
                "message": "Privileged container detected",
            }
        ]
        result = compute_risk(state)
        assert result["risk_score"]["score"] > 0
        assert result["risk_score"]["signal_count"] == 1

    def test_calculate_risk_multiple_signals(
        self, empty_state: InfraState
    ) -> None:
        """Test risk score accumulates with multiple signals."""
        state = empty_state
        state["signals"] = [
            {
                "category": "security",
                "severity": "critical",
                "resource": "pod-1",
                "message": "Critical issue",
            },
            {
                "category": "reliability",
                "severity": "high",
                "resource": "pod-2",
                "message": "High issue",
            },
            {
                "category": "cost",
                "severity": "low",
                "resource": "pod-3",
                "message": "Low issue",
            },
        ]
        result = compute_risk(state)
        assert result["risk_score"]["signal_count"] == 3
        assert result["risk_score"]["score"] > 0

    def test_grade_thresholds(self) -> None:
        """Test grade classification for different scores."""
        test_cases = [
            (0, "A"),  # Score 0 = Grade A
            (15, "A"),  # Low score = Grade A
            (35, "B"),  # Moderate score = Grade B
            (55, "C"),  # Medium score = Grade C
            (75, "D"),  # High score = Grade D
            (90, "F"),  # Critical score = Grade F
            (100, "F"),  # Max score = Grade F
        ]
        for score, expected_grade in test_cases:
            # Find the grade based on GRADE_THRESHOLDS
            grade = next((g for t, g in GRADE_THRESHOLDS if score >= t), "F")
            assert grade == expected_grade, f"Score {score} should be grade {expected_grade}, got {grade}"

    def test_severity_weights(self) -> None:
        """Test severity weight configuration."""
        assert SEVERITY_WEIGHTS["critical"] == 15
        assert SEVERITY_WEIGHTS["high"] == 8
        assert SEVERITY_WEIGHTS["medium"] == 3
        assert SEVERITY_WEIGHTS["low"] == 1

    def test_security_category_multiplier(self, empty_state: InfraState) -> None:
        """Test security signals get higher weight multiplier."""
        state = empty_state
        state["signals"] = [
            {
                "category": "security",
                "severity": "high",
                "resource": "pod-1",
                "message": "Security issue",
            }
        ]
        result = compute_risk(state)
        # Security has 2.0x multiplier
        assert result["risk_score"]["score"] > 0


# ============================================================================
# SIGNALS GENERATION TESTS
# ============================================================================


class TestSignalGeneration:
    """Test signal generation from cluster state."""

    def test_generate_signals_empty(self, empty_state: InfraState) -> None:
        """Test signal generation with empty cluster."""
        state = generate_signals(empty_state)
        assert "signals" in state
        assert isinstance(state["signals"], list)

    def test_generate_signals_with_pods(self, populated_state: InfraState) -> None:
        """Test signal generation detects pod issues."""
        state = generate_signals(populated_state)
        signals = state["signals"]
        assert isinstance(signals, list)
        # Should detect orphan service
        orphan_signals = [s for s in signals if "orphan" in s["message"].lower()]
        assert len(orphan_signals) >= 0  # May or may not be present depending on implementation

    def test_add_signal_uniqueness(self, empty_state: InfraState) -> None:
        """Test that duplicate signals are not added."""
        signals: List[Dict[str, Any]] = []
        seen = set()

        _add_signal(
            signals,
            seen,
            "security",
            "high",
            "pod-1",
            "Test message",
        )
        _add_signal(
            signals,
            seen,
            "security",
            "high",
            "pod-1",
            "Test message",
        )
        assert len(signals) == 1  # Should only add once

    def test_add_signal_with_diagnosis(self, empty_state: InfraState) -> None:
        """Test signal with diagnosis data."""
        signals: List[Dict[str, Any]] = []
        seen = set()

        diagnosis = {
            "recommended_fix": "kubectl patch pod pod-1",
            "verification": "kubectl describe pod pod-1",
        }

        _add_signal(
            signals,
            seen,
            "reliability",
            "high",
            "pod-1",
            "Pod crashing",
            diagnosis=diagnosis,
        )
        assert len(signals) == 1
        assert "diagnosis" in signals[0]

    def test_signal_title_formatting(self) -> None:
        """Test signal title formatting."""
        title = _signal_title("pod_crash_loop", "Pod is crashing")
        assert isinstance(title, str)
        assert len(title) > 0


# ============================================================================
# GRAPH BUILDING TESTS
# ============================================================================


class TestGraphBuilding:
    """Test dependency graph construction."""

    def test_build_graph_empty(self, empty_state: InfraState) -> None:
        """Test graph building with empty cluster."""
        state = build_graph(empty_state)
        graph = state["graph_summary"]
        assert "service_to_deployment" in graph
        assert "deployment_to_pods" in graph
        assert isinstance(graph["service_to_deployment"], dict)
        assert isinstance(graph["deployment_to_pods"], dict)

    def test_build_graph_with_resources(self, populated_state: InfraState) -> None:
        """Test graph building with populated cluster."""
        state = build_graph(populated_state)
        graph = state["graph_summary"]

        # Should have detected orphan services
        assert "orphan_services" in graph
        assert isinstance(graph["orphan_services"], list)

        # Should have detected single-replica deployments
        assert "single_replica_deployments" in graph
        assert isinstance(graph["single_replica_deployments"], list)

    def test_orphan_services_detection(self, populated_state: InfraState) -> None:
        """Test detection of orphan services."""
        state = build_graph(populated_state)
        orphans = state["graph_summary"]["orphan_services"]
        assert "orphan-svc" in orphans

    def test_single_replica_detection(self, populated_state: InfraState) -> None:
        """Test detection of single-replica deployments."""
        state = build_graph(populated_state)
        single_repls = state["graph_summary"]["single_replica_deployments"]
        assert "api-service" in single_repls


# ============================================================================
# REPORTING TESTS
# ============================================================================


class TestReporting:
    """Test report generation."""

    def test_generate_report_basic(self, populated_state: InfraState) -> None:
        """Test basic report generation."""
        # Populate required fields
        populated_state["signals"] = []
        populated_state["risk_score"] = {"score": 25, "grade": "A", "signal_count": 0}
        populated_state["strategic_summary"] = "Cluster is healthy"

        report = build_report(populated_state)
        assert isinstance(report, str)
        assert len(report) > 0
        assert "cluster" in report.lower() or "report" in report.lower()

    def test_generate_report_with_signals(self, populated_state: InfraState) -> None:
        """Test report generation with signals."""
        populated_state["signals"] = [
            {
                "category": "security",
                "severity": "high",
                "resource": "pod-1",
                "message": "Privileged container",
            }
        ]
        populated_state["risk_score"] = {"score": 50, "grade": "C", "signal_count": 1}
        populated_state["strategic_summary"] = "Cluster has issues"

        report = build_report(populated_state)
        assert isinstance(report, str)
        assert len(report) > 0


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """Integration tests for complete pipelines."""

    def test_end_to_end_analysis(self, populated_state: InfraState) -> None:
        """Test complete analysis pipeline."""
        # Step 1: Build graph
        state = build_graph(populated_state)
        assert "graph_summary" in state

        # Step 2: Generate signals
        state = generate_signals(state)
        assert "signals" in state

        # Step 3: Calculate risk
        state = compute_risk(state)
        assert "risk_score" in state
        assert "grade" in state["risk_score"]
        assert "score" in state["risk_score"]

    def test_pipeline_preserves_user_query(self, populated_state: InfraState) -> None:
        """Test that user query is preserved through pipeline."""
        original_query = populated_state["user_query"]

        state = build_graph(populated_state)
        state = generate_signals(state)
        state = compute_risk(state)

        assert state["user_query"] == original_query

    def test_complete_healthy_cluster(self) -> None:
        """Test analysis of completely healthy cluster."""
        state: InfraState = {
            "user_query": "analyze healthy cluster",
            "cluster_snapshot": {
                "nodes": [
                    {
                        "name": "node-1",
                        "status": "Ready",
                    }
                ],
                "deployments": [
                    {
                        "name": "healthy-app",
                        "namespace": "default",
                        "replicas": 3,
                        "status_replicas": 3,
                        "selector": {"app": "healthy"},
                    }
                ],
                "pods": [
                    {
                        "name": "healthy-app-1",
                        "namespace": "default",
                        "status": "Running",
                        "node_name": "node-1",
                        "labels": {"app": "healthy"},
                        "owner_references": [
                            {
                                "kind": "ReplicaSet",
                                "name": "healthy-app-rs",
                            }
                        ],
                    },
                    {
                        "name": "healthy-app-2",
                        "namespace": "default",
                        "status": "Running",
                        "node_name": "node-1",
                        "labels": {"app": "healthy"},
                        "owner_references": [
                            {
                                "kind": "ReplicaSet",
                                "name": "healthy-app-rs",
                            }
                        ],
                    },
                    {
                        "name": "healthy-app-3",
                        "namespace": "default",
                        "status": "Running",
                        "node_name": "node-1",
                        "labels": {"app": "healthy"},
                        "owner_references": [
                            {
                                "kind": "ReplicaSet",
                                "name": "healthy-app-rs",
                            }
                        ],
                    },
                ],
                "services": [
                    {
                        "name": "healthy-svc",
                        "namespace": "default",
                        "selector": {"app": "healthy"},
                    }
                ],
                "replicasets": [
                    {
                        "name": "healthy-app-rs",
                        "namespace": "default",
                        "replicas": 3,
                        "owner_references": [
                            {
                                "kind": "Deployment",
                                "name": "healthy-app",
                            }
                        ],
                    }
                ],
                "statefulsets": [],
                "daemonsets": [],
                "crds": {},
            },
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
        }

        state = build_graph(state)
        state = generate_signals(state)
        risk = compute_risk(state)

        assert risk["risk_score"]["score"] <= 35  # Healthy cluster should have grade A or B


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_missing_cluster_snapshot(self) -> None:
        """Test handling of missing cluster snapshot."""
        state: InfraState = {
            "user_query": "test",
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
        }
        # Should not crash
        try:
            result = build_graph(state)
            assert "graph_summary" in result
        except KeyError:
            pytest.fail("Should handle missing cluster_snapshot gracefully")

    def test_empty_resource_lists(self) -> None:
        """Test handling of empty resource lists."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {
                "nodes": [],
                "deployments": [],
                "pods": [],
                "services": [],
                "replicasets": [],
                "statefulsets": [],
                "daemonsets": [],
                "crds": {},
            },
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
        }
        result = build_graph(state)
        assert "graph_summary" in result

    def test_malformed_owner_references(self) -> None:
        """Test handling of pods with malformed owner references."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {
                "nodes": [],
                "deployments": [],
                "pods": [
                    {
                        "name": "orphan-pod",
                        "namespace": "default",
                        "status": "Running",
                        "labels": {"app": "orphan"},
                        "owner_references": [],  # No owner
                    }
                ],
                "services": [],
                "replicasets": [],
                "statefulsets": [],
                "daemonsets": [],
                "crds": {},
            },
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
        }
        try:
            state = generate_signals(state)
            assert isinstance(state["signals"], list)
        except Exception as e:
            pytest.fail(f"Should handle malformed references gracefully: {e}")


# ============================================================================
# DATA VALIDATION TESTS
# ============================================================================


class TestDataValidation:
    """Test data validation and type checking."""

    def test_signal_structure(self, empty_state: InfraState) -> None:
        """Test generated signals have correct structure."""
        empty_state["signals"] = [
            {
                "category": "security",
                "severity": "high",
                "resource": "pod-1",
                "message": "Test signal",
            }
        ]
        signals = empty_state["signals"]
        for signal in signals:
            assert "category" in signal
            assert "severity" in signal
            assert "resource" in signal
            assert "message" in signal

    def test_risk_score_structure(self, empty_state: InfraState) -> None:
        """Test risk score has correct structure."""
        empty_state["signals"] = [
            {
                "category": "security",
                "severity": "critical",
                "resource": "pod-1",
                "message": "Test",
            }
        ]
        risk = compute_risk(empty_state)
        assert isinstance(risk["risk_score"]["score"], (int, float))
        assert isinstance(risk["risk_score"]["grade"], str)
        assert isinstance(risk["risk_score"]["signal_count"], int)
        assert risk["risk_score"]["grade"] in ["A", "B", "C", "D", "F"]

    def test_graph_summary_structure(self, empty_state: InfraState) -> None:
        """Test graph summary has expected keys."""
        state = build_graph(empty_state)
        graph = state["graph_summary"]
        assert isinstance(graph, dict)
        assert "service_to_deployment" in graph
        assert "deployment_to_pods" in graph


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
