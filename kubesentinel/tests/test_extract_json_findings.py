"""
Tests for agents._extract_json_findings() - robust JSON extraction from LLM output.

Tests cover various output formats agents might produce.
"""

from kubesentinel.agents import _extract_json_findings


def test_extract_direct_json_array():
    """Test extraction from valid JSON array without markdown fences."""
    result = {
        "output": '[{"resource": "ns/deploy/app", "severity": "high", '
        '"analysis": "Test issue", "recommendation": "kubectl fix"}]'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 1
    assert findings[0]["resource"] == "ns/deploy/app"
    assert findings[0]["severity"] == "high"


def test_extract_markdown_fenced_json():
    """Test extraction from markdown-fenced JSON."""
    result = {
        "output": "Here are my findings:\n```json\n"
        '[{"resource": "ns/deploy/app", "severity": "critical", '
        '"analysis": "Bad thing", "recommendation": "kubectl patch"}]\n```\n'
        "That's all!"
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 1
    assert findings[0]["severity"] == "critical"


def test_extract_json_with_extra_text():
    """Test extraction when JSON array is surrounded by prose."""
    result = {
        "output": "I analyzed the cluster and found these issues:\n"
        '[{"resource": "prod/deploy/web", "severity": "medium", '
        '"analysis": "Issue here", "recommendation": "kubectl scale"}]\n'
        "Hope this helps!"
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 1
    assert findings[0]["resource"] == "prod/deploy/web"


def test_extract_multiple_findings():
    """Test extraction of multiple findings in one array."""
    result = {
        "output": '[{"resource": "a/b/c", "severity": "low", "analysis": "x", "recommendation": "y"}, '
        '{"resource": "d/e/f", "severity": "high", "analysis": "z", "recommendation": "w"}]'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 2
    assert findings[0]["resource"] == "a/b/c"
    assert findings[1]["resource"] == "d/e/f"


def test_invalid_json_returns_empty():
    """Test that invalid JSON returns empty list."""
    result = {"output": "This is not JSON at all, just plain text."}
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 0


def test_missing_required_fields():
    """Test that findings missing required fields are filtered out."""
    result = {
        "output": '[{"resource": "ns/deploy/app", "severity": "high"}, '
        '{"resource": "ns/deploy/app2", "severity": "low", "analysis": "x", "recommendation": "y"}]'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    # Only second finding should be valid
    assert len(findings) == 1
    assert findings[0]["resource"] == "ns/deploy/app2"


def test_empty_result():
    """Test that None or empty result returns empty list."""
    assert _extract_json_findings(None, agent_name="test") == []
    assert _extract_json_findings({}, agent_name="test") == []
    assert _extract_json_findings({"output": ""}, agent_name="test") == []


def test_non_array_json():
    """Test that non-array JSON (like object) returns empty list."""
    result = {
        "output": '{"error": "I could not analyze the cluster"}'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 0


def test_extra_fields_allowed():
    """Test that findings with extra fields (beyond required) are accepted."""
    result = {
        "output": '[{"resource": "ns/pod/x", "severity": "critical", '
        '"analysis": "OOMKilled", "recommendation": "increase memory", '
        '"extra_debug_info": "some debugging context"}]'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 1
    assert "extra_debug_info" in findings[0]


def test_markdown_fence_without_json_label():
    """Test extraction from markdown fence without 'json' label."""
    result = {
        "output": "Findings:\n```\n"
        '[{"resource": "ns/svc/api", "severity": "high", '
        '"analysis": "No replicas", "recommendation": "scale up"}]\n```'
    }
    findings = _extract_json_findings(result, agent_name="test_agent")
    assert len(findings) == 1
    assert findings[0]["resource"] == "ns/svc/api"
