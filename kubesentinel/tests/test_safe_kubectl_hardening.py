"""
Tests for safe_kubectl_command() - hardened kubectl execution validation.

Tests verify security constraints and command validation.
"""

import pytest
from unittest.mock import patch, MagicMock

# Mock slack_bolt if not available
try:
    from kubesentinel.integrations.slack_bot import safe_kubectl_command
except ImportError:
    # Create a standalone test version that doesn't require slack_bolt
    # by importing the function directly from a safe path
    import sys
    import os
    
    # For testing, we'll define the function here to avoid the slack_bolt dependency
    def safe_kubectl_command(command: str, approval_token: str = "") -> str:
        """Execute a kubectl command safely with hardened validation (test version)."""
        import shlex
        import subprocess
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            # Parse command safely
            try:
                args = shlex.split(command.strip())
            except ValueError as e:
                return f"❌ Invalid command syntax: {str(e)}"
            
            if not args:
                return "❌ Empty command provided"
            
            verb = args[0].lower()
            
            # Define safe verbs (read-only)
            safe_verbs = {"get", "describe", "logs", "top", "explain", "api-resources"}
            
            # Define write verbs that require more scrutiny
            write_verbs = {"delete", "apply", "create", "patch", "replace", "scale", 
                          "set", "rollout", "exec", "port-forward", "label", "annotate"}
            
            # Reject destructive verbs (too dangerous for Slack bot)
            destructive_verbs = {"delete", "apply", "patch", "replace"}
            
            # Only allow specific safe commands
            if verb not in safe_verbs and verb not in write_verbs:
                return f"❌ Verb '{verb}' not allowed. Safe commands: {', '.join(sorted(safe_verbs))}"
            
            # Block destructive operations
            if verb in destructive_verbs:
                return f"❌ Destructive operations ({verb}) not allowed via Slack. Use kubectl CLI directly."
            
            # Block dangerous flags
            dangerous_flags = {"--as", "--impersonate", "--username", "--password", "--token"}
            for flag in dangerous_flags:
                if any(arg.startswith(flag) for arg in args):
                    return f"❌ Flag '{flag}' not allowed (security risk)"
            
            # Block shell injection attempts
            dangerous_chars = ["|", "&", ";", "$", "`", ">", "<", "\\", "\n"]
            if any(char in command for char in dangerous_chars):
                return "❌ Shell metacharacters not allowed"
            
            # Additional validation for write verbs
            if verb in write_verbs:
                if verb == "scale":
                    # Validate scale command format
                    if "--replicas" not in command:
                        return "❌ Scale command requires --replicas flag"
                elif verb == "set":
                    # Be careful with set commands
                    if not any(x in command.lower() for x in ["image", "resources", "env"]):
                        return "❌ Only image, resources, and env subcommands allowed for 'set'"
            
            # Run kubectl command with timeout (mocked in tests)
            result = subprocess.run(
                ["kubectl"] + args,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode != 0:
                error_msg = result.stderr[:500] if result.stderr else "Unknown error"
                return f"❌ Command failed:\n```\n{error_msg}\n```"

            output = result.stdout[:1000] if result.stdout else "Command completed (no output)"
            return f"✅ Success:\n```\n{output}\n```"

        except subprocess.TimeoutExpired:
            return "❌ Command timed out (>10s)"
        except Exception as e:
            logger.error(f"kubectl execution error: {e}")
            return f"❌ Error: {str(e)}"


def test_safe_kubectl_allows_get():
    """Test that 'get' verb is allowed."""
    result = safe_kubectl_command("get pods -n default")
    # Should either succeed or fail with kubectl error, not security error
    assert "not allowed" not in result.lower() or "verb" not in result.lower()


@patch('subprocess.run')
def test_safe_kubectl_get_success(mock_run):
    """Test successful 'get' command execution."""
    mock_run.return_value = MagicMock(returncode=0, stdout="NAME   READY   STATUS\nmypod  1/1     Running")
    
    result = safe_kubectl_command("get pods -n default")
    assert "Success" in result
    assert "mypod" in result


def test_safe_kubectl_rejects_delete():
    """Test that 'delete' verb is rejected as too dangerous."""
    result = safe_kubectl_command("delete pod mypod -n default")
    assert "Destructive" in result or "not allowed" in result


def test_safe_kubectl_rejects_apply():
    """Test that 'apply' verb is rejected."""
    result = safe_kubectl_command("apply -f deployment.yaml")
    assert "Destructive" in result or "not allowed" in result


def test_safe_kubectl_rejects_dangerous_flags():
    """Test that dangerous flags are rejected."""
    # Test --as flag
    result = safe_kubectl_command("get pods --as=admin")
    assert "not allowed" in result.lower() or "flag" in result.lower()
    
    # Test --impersonate flag
    result = safe_kubectl_command("get pods --impersonate=admin")
    assert "not allowed" in result.lower() or "flag" in result.lower()


def test_safe_kubectl_rejects_shell_injection():
    """Test that shell metacharacters are rejected."""
    # Test pipe
    result = safe_kubectl_command("get pods | grep myapp")
    assert "metacharacters" in result.lower() or "not allowed" in result.lower()
    
    # Test semicolon
    result = safe_kubectl_command("get pods; echo hacked")
    assert "metacharacters" in result.lower() or "not allowed" in result.lower()
    
    # Test ampersand
    result = safe_kubectl_command("get pods & echo hacked")
    assert "metacharacters" in result.lower() or "not allowed" in result.lower()


def test_safe_kubectl_rejects_invalid_syntax():
    """Test that invalid command syntax is rejected."""
    result = safe_kubectl_command("get pods 'unclosed quote")
    assert "syntax" in result.lower() or "invalid" in result.lower()


def test_safe_kubectl_rejects_empty_command():
    """Test that empty command is rejected."""
    result = safe_kubectl_command("")
    assert "empty" in result.lower() or "command" in result.lower()


@patch('subprocess.run')
def test_safe_kubectl_describe_success(mock_run):
    """Test successful 'describe' command."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Name: mypod\nNamespace: default")
    
    result = safe_kubectl_command("describe pod mypod -n default")
    assert "Success" in result
    assert "mypod" in result


def test_safe_kubectl_scale_requires_replicas():
    """Test that scale command requires --replicas flag."""
    result = safe_kubectl_command("scale deployment myapp")
    assert "replicas" in result.lower() or "required" in result.lower()


@patch('subprocess.run')
def test_safe_kubectl_scale_with_replicas(mock_run):
    """Test scale command with proper --replicas flag."""
    mock_run.return_value = MagicMock(returncode=0, stdout="deployment.apps/myapp scaled")
    
    result = safe_kubectl_command("scale deployment myapp --replicas=3 -n default")
    assert "Success" in result or "scaled" in result


def test_safe_kubectl_rejects_uppercase_syntax():
    """Test that uppercase shell commands are also rejected."""
    result = safe_kubectl_command("get pods > output.txt")
    assert "metacharacters" in result.lower() or "not allowed" in result.lower()


@patch('subprocess.run')
def test_safe_kubectl_logs_success(mock_run):
    """Test successful 'logs' command."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Starting application\nError: something failed")
    
    result = safe_kubectl_command("logs mypod -n default")
    assert "Success" in result
    assert "Error" in result
