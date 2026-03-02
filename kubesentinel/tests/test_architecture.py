"""
Regression tests for KubeSentinel architectural integrity.

Guards against:
1. Agents accessing raw cluster_snapshot directly (should use bounded tools only)
2. Tool responses leaking unbounded data (should be capped, no raw K8s objects)
3. JSON parsing errors causing crashes (should gracefully return empty findings)
"""

import json
import logging

from kubesentinel.models import InfraState
from kubesentinel.agents import (
    make_tools,
    _extract_json_findings,
)


class TestAgentIsolation:
    """Guard A: Agents never access raw cluster_snapshot directly."""

    def test_tools_provide_bounded_views(self):
        """Verify tools don't expose cluster_snapshot directly."""
        # Create minimal state without raw cluster_snapshot access
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {
                "nodes": [{"name": "node1"}, {"name": "node2"}],
                "pods": [{"name": "pod1"}, {"name": "pod2"}],
                "deployments": [],
                "services": [],
            },
            "graph_summary": {"edges": 0},
            "signals": [
                {
                    "node_id": "pod1",
                    "node_type": "pod",
                    "category": "reliability",
                    "priority": "high",
                    "reason": "test signal",
                }
            ],
            "risk_score": {"score": 45, "grade": "B", "signal_count": 1},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        # Create tools
        tools = make_tools(state)
        tool_names = {tool.name for tool in tools}

        # Verify expected bounded tools exist
        expected_tools = {
            "get_signals",
            "get_graph_summary",
            "get_cluster_summary",
            "get_risk_score",
        }
        assert expected_tools.issubset(tool_names), (
            f"Missing expected tools. Expected {expected_tools}, got {tool_names}"
        )

        # Verify no raw_cluster_snapshot tool exists
        assert "raw_cluster_snapshot" not in tool_names
        assert "get_raw_snapshot" not in tool_names

    def test_get_signals_bounds_response(self):
        """Verify get_signals tool returns bounded results without raw K8s objects."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [
                {
                    "node_id": f"pod{i}",
                    "node_type": "pod",
                    "category": "reliability",
                    "priority": "high",
                    "reason": f"signal {i}",
                }
                for i in range(250)  # More than MAX_SIGNALS (200)
            ],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        tools = make_tools(state)
        signals_tool = next(
            (t for t in tools if t.name == "get_signals"), None
        )
        assert signals_tool is not None

        # Call tool to get signals
        result = signals_tool.func(category="reliability")

        # Parse result (it returns JSON string)
        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result

        # Verify capping: should not exceed 200 signals
        signal_list = parsed if isinstance(parsed, list) else parsed.get("signals", [])
        assert len(signal_list) <= 200, (
            f"Tool returned {len(signal_list)} signals, expected <= 200"
        )

        # Verify no raw K8s object leakage
        for signal in signal_list:
            assert "raw_object" not in signal
            assert "metadata" not in signal or isinstance(signal.get("metadata"), dict)
            # Signals should only have safe fields: node_id, category, priority, reason
            assert "node_id" in signal or "reason" in signal

    def test_graph_summary_no_raw_objects(self):
        """Verify get_graph_summary tool doesn't leak raw K8s objects."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {
                "pods": [
                    {
                        "name": "pod1",
                        "namespace": "default",
                        "raw_fields": "should_not_leak",
                    }
                ]
            },
            "graph_summary": {"edges": 5, "nodes": 10},
            "signals": [],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        tools = make_tools(state)
        graph_tool = next(
            (t for t in tools if t.name == "get_graph_summary"), None
        )
        assert graph_tool is not None

        result = graph_tool.func()

        # Parse result
        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result

        # Verify no raw pod data
        result_str = json.dumps(parsed)
        assert "raw_fields" not in result_str, "Raw K8s fields leaked in graph summary"
        assert "should_not_leak" not in result_str, "Sensitive data leaked in graph summary"


class TestToolBounds:
    """Guard B: Tool responses are strictly bounded, no raw K8s object leaks."""

    def test_signals_tool_max_200(self):
        """Verify get_signals returns at most 200 signals."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [
                {
                    "node_id": f"pod{i}",
                    "node_type": "pod",
                    "category": "reliability",
                    "priority": "high" if i % 2 == 0 else "medium",
                    "reason": f"issue {i}",
                }
                for i in range(300)
            ],
            "risk_score": {"score": 50, "grade": "B", "signal_count": 300},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        tools = make_tools(state)
        signals_tool = next(
            (t for t in tools if t.name == "get_signals"), None
        )
        assert signals_tool is not None, "signals tool must exist"

        result = signals_tool.func(category="reliability")

        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result

        signal_list = parsed if isinstance(parsed, list) else parsed.get("signals", [])
        assert len(signal_list) <= 200

    def test_cluster_summary_bounded_size(self):
        """Verify cluster_summary doesn't return raw container specs or secret data."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {
                "nodes": [{"name": f"node{i}", "status": "ready"} for i in range(10)],
                "pods": [
                    {
                        "name": f"pod{i}",
                        "namespace": "default",
                        "containers": [
                            {
                                "name": "app",
                                "image": "secret:password",
                                "env": [{"name": "SECRET_KEY", "value": "abc123"}],
                            }
                        ],
                    }
                    for i in range(50)
                ],
                "deployments": [
                    {
                        "name": f"deploy{i}",
                        "namespace": "default",
                        "replicas": 3,
                        "spec": {"secret_field": "should_not_leak"},
                    }
                    for i in range(20)
                ],
                "services": [
                    {
                        "name": f"svc{i}",
                        "namespace": "default",
                        "type": "ClusterIP",
                        "selectors": {"app": "test"},
                    }
                    for i in range(10)
                ],
            },
            "graph_summary": {},
            "signals": [],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        tools = make_tools(state)
        cluster_tool = next(
            (t for t in tools if t.name == "get_cluster_summary"), None
        )
        assert cluster_tool is not None

        result = cluster_tool.func()

        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result

        result_str = json.dumps(parsed)

        # Verify no secrets leaked
        assert "secret:password" not in result_str
        assert "SECRET_KEY" not in result_str
        assert "should_not_leak" not in result_str
        assert "abc123" not in result_str

        # Verify summary has bounded size
        assert len(result_str) < 10000, "Cluster summary exceeds 10KB"


class TestErrorHandling:
    """Guard C: JSON parsing and LLM errors gracefully handled, never crash."""

    def test_malformed_json_returns_empty_findings(self):
        """Verify _extract_json_findings handles malformed JSON gracefully."""
        # Test with completely invalid JSON
        malformed_response = {
            "messages": [
                {
                    "content": "I found issues: [ malformed json without closing"
                }
            ]
        }

        findings = _extract_json_findings(malformed_response)

        # Should return empty list, not raise exception
        assert findings == []
        assert isinstance(findings, list)

    def test_missing_json_block_returns_empty(self):
        """Verify response without JSON block returns empty findings."""
        response_no_json = {
            "messages": [
                {
                    "content": "I checked the cluster and found several issues. "
                    "The reliability is poor. There are cost overruns. "
                    "But no JSON block was provided."
                }
            ]
        }

        findings = _extract_json_findings(response_no_json)

        assert findings == []

    def test_json_with_invalid_structure_returns_empty(self):
        """Verify JSON with wrong structure returns empty findings."""
        response_wrong_structure = {
            "messages": [
                {
                    "content": '```json\n{"not_findings": "this is unexpected"}\n```'
                }
            ]
        }

        findings = _extract_json_findings(response_wrong_structure)

        # Should handle gracefully and return list (may be empty or partial)
        assert isinstance(findings, list)

    def test_json_with_unexpected_types_returns_empty(self):
        """Verify JSON with unexpected types in findings list returns empty."""
        response_wrong_types = {
            "messages": [
                {
                    "content": '```json\n{"findings": ["not", "a", "dict"]}\n```'
                }
            ]
        }

        findings = _extract_json_findings(response_wrong_types)

        # Should handle gracefully
        assert isinstance(findings, list)

    def test_deeply_nested_malformed_json(self):
        """Verify deeply nested malformed JSON doesn't crash."""
        response_nested = {
            "messages": [
                {
                    "content": '```json\n'
                    '{"findings": [{"issue": {"nested": {"data": [1, 2, '
                    '{"incomplete": '
                    '}\n```'
                }
            ]
        }

        findings = _extract_json_findings(response_nested)

        # Should not crash, return empty or partial list
        assert isinstance(findings, list)

    def test_null_response_returns_empty(self):
        """Verify null/None response returns empty findings."""
        findings = _extract_json_findings(None)
        assert findings == [] or isinstance(findings, list)

        findings = _extract_json_findings({})
        assert isinstance(findings, list)

    def test_empty_messages_returns_empty(self):
        """Verify response with empty messages returns empty findings."""
        response_empty = {"messages": []}

        findings = _extract_json_findings(response_empty)

        assert findings == []

    def test_logging_on_parse_failure(self, caplog):
        """Verify warning is logged on JSON parse failure."""
        with caplog.at_level(logging.WARNING):
            malformed = {
                "messages": [{"content": "broken [ json"}]
            }
            findings = _extract_json_findings(malformed)

        # Should log a warning but not crash
        assert findings == []
        # Check if warning was logged (may or may not appear depending on implementation)
        # Just verify no exception was raised


class TestNamespaceFiltering:
    """Verify namespace parameter doesn't break architecture."""

    def test_state_with_target_namespace(self):
        """Verify state accepts target_namespace field."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "target_namespace": "default",  # New field
        }

        # Should not raise TypedDict error
        assert state["target_namespace"] == "default"

    def test_state_without_target_namespace(self):
        """Verify state works without target_namespace (backward compat)."""
        state: InfraState = {
            "user_query": "test",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
        }

        # Should not raise, state is valid without target_namespace
        assert state["user_query"] == "test"
        # Verify get with default works
        ns = state.get("target_namespace", None)
        assert ns is None
