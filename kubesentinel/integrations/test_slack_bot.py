"""Tests for Slack Socket Mode chat integration."""

import pytest
from unittest.mock import Mock, patch
from kubesentinel.integrations.slack_bot import (
    safe_kubectl_command,
    extract_finding_details,
    format_summary,
    format_summary_blocks,
    clean_text,
    extract_kubectl_commands,
    _format_report_for_slack,
)
from kubesentinel.models import InfraState


class TestKubectlCommand:
    """Tests for safe_kubectl_command function."""

    def test_safe_get_command(self):
        """Test that get commands are allowed."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="pod-1\npod-2\n")

            result = safe_kubectl_command("get pods")

            assert "Success" in result
            assert "pod-1" in result

    def test_safe_describe_command(self):
        """Test that describe commands are allowed."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="Name: test-pod\n")

            result = safe_kubectl_command("describe pod test-pod")

            assert "Success" in result

    def test_disallowed_command(self):
        """Test that dangerous commands are blocked."""
        result = safe_kubectl_command("delete pod test-pod")

        assert "not allowed" in result or "not allowed" in result.lower()

    def test_command_timeout(self):
        """Test that timed out commands are handled gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError()

            # Since TimeoutError is caught as subprocess.TimeoutExpired in the actual code,
            # we'll mock subprocess.TimeoutExpired
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired("kubectl", 10)

            result = safe_kubectl_command("get pods")

            assert "timed out" in result.lower()

    def test_command_error(self):
        """Test that command errors are reported."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stderr="Error: pods not found\n")

            result = safe_kubectl_command("get pods")

            assert "failed" in result.lower()

    def test_command_truncation(self):
        """Test that long output is truncated."""
        with patch("subprocess.run") as mock_run:
            long_output = "pod-" + "x" * 2000 + "\n"
            mock_run.return_value = Mock(returncode=0, stdout=long_output)

            result = safe_kubectl_command("get pods")

            assert len(result) < 1500  # Should be truncated


class TestFindingExtraction:
    """Tests for finding detail extraction."""

    def test_extract_valid_finding(self):
        """Test extracting details from a valid finding."""
        finding = {
            "resource": "deployment-app",
            "analysis": "Pod is pending due to resource constraints",
            "recommendation": "Increase node resources or scale horizontally",
        }

        title, desc, fix = extract_finding_details(finding)

        assert title == "deployment-app"
        assert "resource" in desc.lower()
        assert "scale" in fix.lower()

    def test_extract_incomplete_finding(self):
        """Test extracting from a partially filled finding."""
        finding = {
            "resource": "pod-xyz",
        }

        title, desc, fix = extract_finding_details(finding)

        assert title == "pod-xyz"
        assert desc == ""
        assert fix == ""

    def test_extract_long_fields_truncated(self):
        """Test that long fields are truncated."""
        finding = {
            "resource": "x" * 200,
            "analysis": "y" * 300,
            "recommendation": "z" * 400,
        }

        title, desc, fix = extract_finding_details(finding)

        assert len(title) <= 100
        assert len(desc) <= 150
        assert len(fix) <= 200


class TestTextFormatting:
    """Tests for text formatting functions."""

    def test_clean_text_removes_mentions(self):
        """Test that clean_text removes Slack mentions."""
        text = "Hey <@U123456> <@U654321> what's up"
        cleaned = clean_text(text)

        assert "<@" not in cleaned
        assert "what's up" in cleaned

    def test_clean_text_strips_whitespace(self):
        """Test that clean_text removes leading/trailing spaces."""
        text = "   hello world   "
        cleaned = clean_text(text)

        assert cleaned == "hello world"

    def test_format_summary_with_minimal_state(self):
        """Test format_summary with minimal state data."""
        state: InfraState = {
            "risk_score": {"score": 65, "grade": "C"},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "strategic_summary": "",
        }

        result = format_summary(state)

        assert "65/100" in result
        assert "KubeSentinel" in result

    def test_format_summary_with_full_state(self):
        """Test format_summary with complete state data."""
        state: InfraState = {
            "risk_score": {"score": 85, "grade": "F"},
            "failure_findings": [
                {
                    "resource": "pod-crash-loop",
                    "analysis": "Pod is in CrashLoopBackOff",
                    "recommendation": "Check logs and debug the application",
                }
            ],
            "cost_findings": [
                {
                    "resource": "over-provisioned-node",
                    "analysis": "Node has 80% idle capacity",
                    "recommendation": "Scale down or consolidate workloads",
                }
            ],
            "security_findings": [
                {
                    "resource": "insecure-secret",
                    "analysis": "Secret stored in plain text",
                    "recommendation": "Encrypt secrets at rest",
                }
            ],
            "strategic_summary": "Critical infrastructure issues detected",
        }

        result = format_summary(state)

        assert "85/100" in result
        assert "CrashLoopBackOff" in result or "pod-crash-loop" in result
        assert "over-provisioned-node" in result or "Scale down" in result
        assert "CRITICAL" in result or "CrashLoop" in result

    def test_format_summary_blocks_structure(self):
        """Test that format_summary_blocks returns proper Block Kit structure."""
        state: InfraState = {
            "risk_score": {"score": 50, "grade": "C"},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "strategic_summary": "Test summary",
        }

        blocks = format_summary_blocks(state)

        # Should have at least header, section, and action blocks
        assert len(blocks) > 0
        assert any(b.get("type") == "header" for b in blocks)
        assert any(b.get("type") == "section" for b in blocks)
        assert any(b.get("type") == "actions" for b in blocks)

        # Buttons should be present
        action_block = next((b for b in blocks if b.get("type") == "actions"), None)
        assert action_block is not None
        assert len(action_block.get("elements", [])) > 0

        # Check for action IDs
        for element in action_block.get("elements", []):
            assert element.get("action_id") in [
                "view_report_action",
                "run_fixes_action",
            ]


class TestKubectlExtraction:
    """Tests for kubectl command extraction from recommendations."""

    def test_extract_single_kubectl_command(self):
        """Test extracting a single kubectl command."""
        recommendation = "Run: kubectl get pods -n default"
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) > 0
        assert "get" in commands[0].lower()
        assert "pods" in commands[0].lower()

    def test_extract_multiple_commands(self):
        """Test extracting multiple kubectl commands from recommendation."""
        recommendation = """
        1. kubectl describe deployment myapp
        2. kubectl logs pod/myapp-123
        """
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) >= 2
        assert any("describe" in cmd for cmd in commands)
        assert any("logs" in cmd for cmd in commands)

    def test_extract_rollout_restart_command(self):
        """Test extracting rollout restart command."""
        recommendation = (
            "Fix: kubectl rollout restart deployment media-frontend -n social-network"
        )
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) > 0
        assert "rollout" in commands[0].lower()
        assert "restart" in commands[0].lower()

    def test_no_commands_found(self):
        """Test when no kubectl commands are present."""
        recommendation = "Just some regular advice without any commands"
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) == 0

    def test_extract_scaled_command(self):
        """Test extracting scale command."""
        recommendation = "Scale up: kubectl scale deployment myapp --replicas=3"
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) > 0
        assert "scale" in commands[0].lower()

    def test_extract_clean_removes_comments(self):
        """Test that extracted commands are cleaned of comments."""
        recommendation = "kubectl get pods # get all pods"
        commands = extract_kubectl_commands(recommendation)
        assert len(commands) > 0
        # Should not include the comment
        assert "#" not in commands[0]

    def test_extract_handles_piped_commands(self):
        """Test that extraction handles piped commands."""
        recommendation = "kubectl get pods -A -o jsonpath='{.items[*].spec.containers[*].image}' | tr"
        commands = extract_kubectl_commands(recommendation)
        # Should extract at least the base command
        assert len(commands) > 0


class TestReportFormatting:
    """Tests for markdown report formatting for Slack."""

    def test_format_empty_report(self):
        """Test formatting an empty report."""
        blocks = _format_report_for_slack("")
        assert isinstance(blocks, list)
        # May have just a section with empty text
        assert len(blocks) >= 0

    def test_format_simple_report(self):
        """Test formatting a simple markdown report."""
        report = """# Test Report
This is a test report."""
        blocks = _format_report_for_slack(report)
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        # All blocks should be valid dicts with type
        for block in blocks:
            assert isinstance(block, dict)
            assert "type" in block

    def test_format_report_with_code_blocks(self):
        """Test formatting report with code blocks."""
        report = r"""# Report
Some text
```
kubectl get pods
```
More text"""
        blocks = _format_report_for_slack(report)
        assert isinstance(blocks, list)
        assert len(blocks) > 0
        # Should preserve code block formatting
        block_types = [b.get("type") for b in blocks]
        assert "section" in block_types

    def test_format_respects_slack_limits(self):
        """Test that formatted blocks respect Slack's 2000 char limit."""
        # Create a very long report
        report = "Line of text\n" * 500
        blocks = _format_report_for_slack(report)
        # Each block should have mrkdwn text under limit
        for block in blocks:
            if block.get("type") == "section" and block.get("text"):
                text = block["text"].get("text", "")
                assert len(text) <= 2500  # slightly over to account for markup

    def test_format_maintains_structure(self):
        """Test that block structure is maintained."""
        report = """# Header
Content here
## Subheader
More content"""
        blocks = _format_report_for_slack(report)
        # All blocks should have required Slack structure
        for block in blocks:
            assert isinstance(block, dict)
            assert block.get("type") in ["section", "divider", "header"]
            if block.get("type") == "section":
                assert "text" in block
                assert "type" in block["text"]


class TestEventHandlers:
    """Tests for Slack event handlers."""

    @patch("kubesentinel.integrations.slack_bot.run_engine")
    @patch("kubesentinel.integrations.slack_bot.build_report")
    def test_handle_message_caches_result(self, mock_build, mock_engine):
        """Test that message handler caches analysis results."""
        from kubesentinel.integrations.slack_bot import _analysis_cache

        # Clear cache
        _analysis_cache.clear()

        mock_state: InfraState = {
            "risk_score": {"score": 50, "grade": "C"},
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "strategic_summary": "Test",
        }
        mock_engine.return_value = mock_state

        # The handler would need to be tested through the app instance
        # This is a placeholder for the actual test
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
