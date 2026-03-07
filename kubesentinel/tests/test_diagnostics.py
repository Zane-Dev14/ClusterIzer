"""Tests for crashloop diagnostics and error signature matching."""

from unittest.mock import Mock
from kubernetes.client.rest import ApiException

from kubesentinel.diagnostics.error_signatures import (
    diagnose_crash_logs,
    DiagnosisResult,
    FixStep,
)
from kubesentinel.diagnostics.log_collector import fetch_pod_logs


class TestErrorSignatures:
    """Test error signature pattern matching."""

    def test_nginx_lua_signature(self):
        """Test NGINX Lua VM initialization failure detection."""
        log_text = """
2026/03/07 06:41:41 [error] 1#1: failed to initialize Lua VM in /usr/local/openresty/nginx/conf/nginx.conf:123
nginx: [error] failed to initialize Lua VM in /usr/local/openresty/nginx/conf/nginx.conf:123
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="media-frontend-abc",
            namespace="social-network",
            container="nginx",
        )

        assert result is not None, "Should detect Nginx Lua failure"
        assert result.type == "nginx_lua_init_fail"
        assert result.confidence >= 0.85
        assert "Lua VM" in result.root_cause
        assert "123" in result.evidence or "nginx.conf" in result.evidence
        assert len(result.fix_plan) > 0

        # Check first fix step mentions inspecting the config
        first_step = result.fix_plan[0]
        assert "nginx.conf" in first_step.description or "123" in first_step.description
        assert first_step.command is not None
        assert "kubectl exec" in first_step.command
        assert "social-network" in first_step.command
        assert "media-frontend-abc" in first_step.command

    def test_oom_killed_signature(self):
        """Test OOMKilled detection."""
        log_text = """
panic: runtime error: out of memory
Container was OOMKilled by the system
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="memory-hog",
            namespace="default",
            container="app",
        )

        assert result is not None
        assert result.type == "oom_killed"
        assert result.confidence >= 0.85
        assert "memory" in result.root_cause.lower()
        assert len(result.fix_plan) > 0

        # Check fix plan mentions memory limits
        fix_plan_text = " ".join([step.description for step in result.fix_plan])
        assert "memory" in fix_plan_text.lower()

    def test_permission_denied_signature(self):
        """Test permission denied error detection."""
        log_text = """
Error: failed to open file: /var/run/docker.sock: permission denied
EACCES: permission denied, access to restricted resource
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="restricted-pod",
            namespace="default",
            container="app",
        )

        assert result is not None
        assert result.type == "permission_denied"
        assert "permission" in result.root_cause.lower()
        assert "docker.sock" in result.evidence or "EACCES" in result.evidence
        assert len(result.fix_plan) > 0

        # Check fix plan mentions securityContext
        fix_plan_text = " ".join([step.description for step in result.fix_plan])
        assert (
            "securitycontext" in fix_plan_text.lower()
            or "owner" in fix_plan_text.lower()
        )

    def test_address_in_use_signature(self):
        """Test address already in use detection."""
        log_text = """
Error: listen EADDRINUSE: address already in use 0.0.0.0:8080
Failed to bind to port 8080: address already in use
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="web-server",
            namespace="default",
            container="nginx",
        )

        assert result is not None
        assert result.type == "address_already_in_use"
        assert (
            "port" in result.root_cause.lower()
            or "address" in result.root_cause.lower()
        )
        assert "8080" in result.evidence or "EADDRINUSE" in result.evidence
        assert len(result.fix_plan) > 0

    def test_module_not_found_signature(self):
        """Test module not found / import error detection."""
        log_text = """
Traceback (most recent call last):
  File "/app/main.py", line 5, in <module>
    import missing_module
ModuleNotFoundError: No module named 'missing_module'
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="python-app",
            namespace="default",
            container="app",
        )

        assert result is not None
        assert result.type == "module_not_found"
        assert (
            "module" in result.root_cause.lower()
            or "import" in result.root_cause.lower()
        )
        assert (
            "missing_module" in result.evidence
            or "ModuleNotFoundError" in result.evidence
        )
        assert len(result.fix_plan) > 0

        # Check fix plan mentions installing the module
        fix_plan_text = " ".join([step.description for step in result.fix_plan])
        assert (
            "install" in fix_plan_text.lower() or "requirement" in fix_plan_text.lower()
        )

    def test_connection_refused_signature(self):
        """Test connection refused detection."""
        log_text = """
Error: connect ECONNREFUSED 10.0.0.5:5432
dial tcp 10.0.0.5:5432: connection refused
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="api-server",
            namespace="default",
            container="app",
        )

        assert result is not None
        assert result.type == "connection_refused"
        assert (
            "connection" in result.root_cause.lower()
            or "service" in result.root_cause.lower()
        )
        assert "refused" in result.evidence.lower() or "ECONNREFUSED" in result.evidence
        assert len(result.fix_plan) > 0

    def test_database_unavailable_signature(self):
        """Test database connection failure detection."""
        log_text = """
Error: could not connect to database server
postgres connection failed: timeout after 30s
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="backend",
            namespace="default",
            container="app",
        )

        assert result is not None
        assert result.type == "database_unavailable"
        assert "database" in result.root_cause.lower()
        assert (
            "postgres" in result.evidence.lower()
            or "database" in result.evidence.lower()
        )
        assert len(result.fix_plan) > 0

    def test_no_signature_match(self):
        """Test handling of logs with no recognized error signature."""
        log_text = """
INFO: Application starting up
INFO: Server listening on port 3000
INFO: Ready to accept connections
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="healthy-pod",
            namespace="default",
            container="app",
        )

        assert result is None, "Should return None when no error signature matches"

    def test_empty_log_text(self):
        """Test handling of empty or whitespace-only log text."""
        result = diagnose_crash_logs(
            log_text="",
            pod_name="pod",
            namespace="default",
            container="app",
        )
        assert result is None

        result = diagnose_crash_logs(
            log_text="   \n\n   ",
            pod_name="pod",
            namespace="default",
            container="app",
        )
        assert result is None

    def test_multiple_signatures_match(self):
        """Test that confidence increases when multiple patterns match."""
        # Log with both OOM and permission denied patterns
        log_text = """
Error: permission denied accessing /data
panic: runtime error: out of memory
        """

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="pod",
            namespace="default",
            container="app",
        )

        # Should match at least one (likely first one found)
        assert result is not None
        # Confidence should be relatively high (actual value depends on implementation)
        assert result.confidence >= 0.85

    def test_fix_plan_structure(self):
        """Test that fix plans have proper structure."""
        log_text = "failed to initialize Lua VM in nginx.conf:123"

        result = diagnose_crash_logs(
            log_text=log_text,
            pod_name="test-pod",
            namespace="test-ns",
            container="test-container",
        )

        assert result is not None
        assert len(result.fix_plan) > 0

        for step in result.fix_plan:
            assert isinstance(step, FixStep)
            assert step.step_number > 0
            assert step.description
            # Some steps may not have commands (informational steps)
            assert isinstance(step.command, (str, type(None)))

            # If command exists, should be a kubectl command
            if step.command:
                assert any(
                    keyword in step.command
                    for keyword in ["kubectl", "grep", "cat", "ls", "#"]
                )


class TestLogCollector:
    """Test pod log collection functionality."""

    def test_fetch_pod_logs_success(self):
        """Test successful log fetching."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.return_value = "Sample log output\nLine 2"

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="test-pod",
            namespace="test-ns",
            container="test-container",
            tail_lines=100,
        )

        assert logs == "Sample log output\nLine 2"
        mock_api.read_namespaced_pod_log.assert_called_once_with(
            name="test-pod",
            namespace="test-ns",
            container="test-container",
            previous=True,
            tail_lines=100,
        )

    def test_fetch_pod_logs_not_found(self):
        """Test handling of pod not found (404)."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.side_effect = ApiException(status=404)

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="nonexistent-pod",
            namespace="test-ns",
            container="test-container",
        )

        assert logs is None

    def test_fetch_pod_logs_no_previous_logs(self):
        """Test handling of no previous logs (400)."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.side_effect = ApiException(status=400)

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="never-crashed-pod",
            namespace="test-ns",
            container="test-container",
        )

        assert logs is None

    def test_fetch_pod_logs_permission_denied(self):
        """Test handling of RBAC permission denied (403)."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.side_effect = ApiException(status=403)

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="test-pod",
            namespace="test-ns",
            container="test-container",
        )

        assert logs is None

    def test_fetch_pod_logs_generic_exception(self):
        """Test handling of unexpected exceptions."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.side_effect = Exception("Unexpected error")

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="test-pod",
            namespace="test-ns",
            container="test-container",
        )

        assert logs is None

    def test_fetch_pod_logs_without_container(self):
        """Test log fetching when container name not specified."""
        mock_api = Mock()
        mock_api.read_namespaced_pod_log.return_value = "Default container logs"

        logs = fetch_pod_logs(
            api_client=mock_api,
            pod_name="test-pod",
            namespace="test-ns",
            container=None,
        )

        assert logs == "Default container logs"
        # Should still pass container=None to the API
        mock_api.read_namespaced_pod_log.assert_called_once()
        call_args = mock_api.read_namespaced_pod_log.call_args
        assert call_args.kwargs["container"] is None


class TestDiagnosisResult:
    """Test DiagnosisResult and FixStep dataclasses."""

    def test_fix_step_to_dict(self):
        """Test FixStep serialization to dict."""
        step = FixStep(
            step_number=1,
            description="Test step",
            command="kubectl get pods",
            expected_result="Pod list",
        )

        step_dict = step.to_dict()
        assert step_dict["step_number"] == 1
        assert step_dict["description"] == "Test step"
        assert step_dict["command"] == "kubectl get pods"
        assert step_dict["expected_result"] == "Pod list"

    def test_fix_step_to_dict_optional_fields(self):
        """Test FixStep serialization with optional fields omitted."""
        step = FixStep(
            step_number=2,
            description="Informational step",
        )

        step_dict = step.to_dict()
        assert step_dict["step_number"] == 2
        assert step_dict["description"] == "Informational step"
        assert "command" not in step_dict or step_dict["command"] is None
        assert (
            "expected_result" not in step_dict or step_dict["expected_result"] is None
        )

    def test_diagnosis_result_structure(self):
        """Test DiagnosisResult structure."""
        fix_plan = [
            FixStep(1, "Step 1", "command 1", "result 1"),
            FixStep(2, "Step 2", "command 2", "result 2"),
        ]

        diagnosis = DiagnosisResult(
            type="test_error",
            root_cause="Test root cause explanation",
            confidence=0.95,
            evidence="Error log excerpt",
            fix_plan=fix_plan,
            verification_commands=["kubectl get pods"],
        )

        assert diagnosis.type == "test_error"
        assert diagnosis.root_cause == "Test root cause explanation"
        assert diagnosis.confidence == 0.95
        assert diagnosis.evidence == "Error log excerpt"
        assert len(diagnosis.fix_plan) == 2
        assert len(diagnosis.verification_commands) == 1
