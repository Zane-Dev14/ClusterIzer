"""Slack Socket Mode chat integration for KubeSentinel.

Architecture:
  Slack WebSocket -> slack-bolt -> run_engine() -> format_summary() -> Slack thread reply
  Includes Block Kit buttons for viewing reports and executing fixes.

Includes response caching to avoid re-running analysis on follow-up questions.
"""

import os
import re
import logging
import subprocess
import shlex
import time
from typing import Any, Dict, List, Optional
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from kubesentinel.runtime import run_engine
from kubesentinel.reporting import build_report
from kubesentinel.models import InfraState
from kubesentinel import persistence

# Load env variables from .env
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Validate environment
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    logger.error(
        "Missing required Slack tokens. Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env"
    )
    raise RuntimeError(
        "Slack credentials missing. Check SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env"
    )

# Initialize Slack Bolt app
app = App(token=SLACK_BOT_TOKEN)

# Cache last analysis per thread to avoid re-running on follow-ups
_analysis_cache: dict[str, InfraState] = {}

# Kubectl verb whitelists for safety enforcement
ALLOWED_READ_VERBS = {
    "get",
    "describe",
    "logs",
    "top",
    "explain",
    "api-resources",
    "api-versions",
}
ALLOWED_WRITE_VERBS = {"patch", "scale", "set", "rollout", "apply", "delete"}
ALLOWED_VERBS = ALLOWED_READ_VERBS | ALLOWED_WRITE_VERBS

# Shell injection rejection patterns
SHELL_METACHARACTERS = {";", "|", "&", "$", "`", "(", ")", ">", "<", "\\", "\n"}

# Get allowed approvers from environment
ALLOWED_APPROVERS = (
    set(os.getenv("KUBESENTINEL_OPS", "").split(","))
    if os.getenv("KUBESENTINEL_OPS")
    else None
)

# Emergency automation bypass flag
FORCE_EXEC_ALLOWLIST = os.getenv("KUBESENTINEL_FORCE_EXEC_ALLOWLIST") == "1"


def safe_kubectl_execute(
    argv: List[str],
    user_id: str = "slack_bot",
    approver_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute kubectl command safely with comprehensive validation and logging.

    CRITICAL RULES:
    1. No shell=True; subprocess.run with argv list only
    2. Reject commands with shell metacharacters (;|&$()><\)
    3. Validate verb against whitelist
    4. Log all execution attempts (ok/error) to persistence
    5. Enforce approval for write verbs (unless FORCE_EXEC overwrites)

    Args:
        argv: List of command arguments (e.g., ["kubectl", "patch", "deployment", ...])
        user_id: Slack user who triggered execution
        approver_user_id: Slack user who approved (for audit trail)

    Returns:
        Dict with keys: ok (bool), stdout (str), stderr (str), elapsed_seconds (float)
    """
    cmd_str = " ".join(argv)
    start_time = time.time()

    try:
        # Step 1: Validate argv list
        if not argv or not isinstance(argv, list):
            result = {
                "ok": False,
                "stdout": "",
                "stderr": "Invalid argv: must be non-empty list",
                "elapsed_seconds": time.time() - start_time,
            }
            persistence.log_kubectl_execution(
                user_id,
                cmd_str,
                False,
                "",
                str(result["stderr"]),
                elapsed_seconds=(
                    result["elapsed_seconds"]
                    if isinstance(result["elapsed_seconds"], (int, float))
                    else 0.0
                ),
                approver_user_id=approver_user_id,
            )
            return result

        # Step 2: Reject if any arg contains shell metacharacters
        for arg in argv:
            if isinstance(arg, str):
                for char in SHELL_METACHARACTERS:
                    if char in arg:
                        result = {
                            "ok": False,
                            "stdout": "",
                            "stderr": f"Shell metacharacter '{char}' detected in argument: {arg}",
                            "elapsed_seconds": time.time() - start_time,
                        }
                        persistence.log_kubectl_execution(
                            user_id,
                            cmd_str,
                            False,
                            "",
                            str(result["stderr"]),
                            elapsed_seconds=(
                                result["elapsed_seconds"]
                                if isinstance(result["elapsed_seconds"], (int, float))
                                else 0.0
                            ),
                            approver_user_id=approver_user_id,
                        )
                        return result

        # Step 3: Extract and validate verb
        # Handle both "kubectl patch ..." and just "patch ..." formats
        verb_idx = 1 if argv[0] == "kubectl" else 0
        if verb_idx >= len(argv):
            result = {
                "ok": False,
                "stdout": "",
                "stderr": "No kubectl verb found",
                "elapsed_seconds": time.time() - start_time,
            }
            persistence.log_kubectl_execution(
                user_id,
                cmd_str,
                False,
                "",
                str(result["stderr"]),
                elapsed_seconds=(
                    result["elapsed_seconds"]
                    if isinstance(result["elapsed_seconds"], (int, float))
                    else 0.0
                ),
                approver_user_id=approver_user_id,
            )
            return result

        verb = argv[verb_idx].lower()
        if verb not in ALLOWED_VERBS:
            result = {
                "ok": False,
                "stdout": "",
                "stderr": f"Verb '{verb}' not allowed. Use: {', '.join(sorted(ALLOWED_VERBS))}",
                "elapsed_seconds": time.time() - start_time,
            }
            persistence.log_kubectl_execution(
                user_id,
                cmd_str,
                False,
                "",
                str(result["stderr"]),
                elapsed_seconds=(
                    result["elapsed_seconds"]
                    if isinstance(result["elapsed_seconds"], (int, float))
                    else 0.0
                ),
                approver_user_id=approver_user_id,
            )
            return result

        # Step 4: Check if write verb and require approval (unless FORCE_EXEC)
        if verb in ALLOWED_WRITE_VERBS and not FORCE_EXEC_ALLOWLIST:
            if not approver_user_id:
                result = {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Write verb '{verb}' requires approval. Please approve in Slack first.",
                    "elapsed_seconds": time.time() - start_time,
                }
                persistence.log_kubectl_execution(
                    user_id,
                    cmd_str,
                    False,
                    "",
                    str(result["stderr"]),
                    elapsed_seconds=(
                        result["elapsed_seconds"]
                        if isinstance(result["elapsed_seconds"], (int, float))
                        else 0.0
                    ),
                    approver_user_id=None,
                )
                return result

            # Check approver list if set
            if ALLOWED_APPROVERS and approver_user_id not in ALLOWED_APPROVERS:
                result = {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"User {approver_user_id} not in KUBESENTINEL_OPS allowlist",
                    "elapsed_seconds": time.time() - start_time,
                }
                persistence.log_kubectl_execution(
                    user_id,
                    cmd_str,
                    False,
                    "",
                    str(result["stderr"]),
                    elapsed_seconds=(
                        result["elapsed_seconds"]
                        if isinstance(result["elapsed_seconds"], (int, float))
                        else 0.0
                    ),
                    approver_user_id=approver_user_id,
                )
                return result

        # Step 5: Execute with 60s timeout
        logger.info(
            f"[safe_kubectl] Executing: {cmd_str} (user={user_id}, approver={approver_user_id})"
        )

        proc_result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        elapsed = time.time() - start_time
        ok = proc_result.returncode == 0
        stdout = proc_result.stdout[:2000] if proc_result.stdout else ""
        stderr = proc_result.stderr[:2000] if proc_result.stderr else ""

        # Log execution
        persistence.log_kubectl_execution(
            user_id,
            cmd_str,
            ok,
            stdout,
            stderr,
            elapsed_seconds=elapsed,
            approver_user_id=approver_user_id,
        )

        return {
            "ok": ok,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": elapsed,
        }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        result = {
            "ok": False,
            "stdout": "",
            "stderr": "Command timed out (>60s)",
            "elapsed_seconds": elapsed,
        }
        persistence.log_kubectl_execution(
            user_id,
            cmd_str,
            False,
            "",
            str(result["stderr"]),
            elapsed_seconds=elapsed,
            approver_user_id=approver_user_id,
        )
        return result

    except Exception as e:
        elapsed = time.time() - start_time
        result = {
            "ok": False,
            "stdout": "",
            "stderr": f"Execution error: {str(e)}",
            "elapsed_seconds": elapsed,
        }
        logger.error(f"[safe_kubectl] Error: {e}", exc_info=True)
        persistence.log_kubectl_execution(
            user_id,
            cmd_str,
            False,
            "",
            str(result["stderr"]),
            elapsed_seconds=elapsed,
            approver_user_id=approver_user_id,
        )
        return result


def safe_kubectl_command(command: str, approval_token: str = "") -> str:
    """Legacy wrapper for safe_kubectl_execute (deprecated, for compatibility).

    Parses command string and calls safe_kubectl_execute.
    """
    try:
        argv = shlex.split("kubectl " + command.strip())
        result = safe_kubectl_execute(argv, user_id="slack_command")

        if result["ok"]:
            return f"✅ Success:\n```\n{result['stdout']}\n```"
        else:
            return f"❌ Command failed:\n```\n{result['stderr']}\n```"
    except ValueError as e:
        return f"❌ Invalid command syntax: {str(e)}"


def format_summary_blocks(state: InfraState) -> list:
    """Format analysis state into Slack Block Kit blocks.

    Args:
        state: The InfraState returned from run_engine()

    Returns:
        A list of Slack block dicts with buttons
    """
    risk = state.get("risk_score", {})
    score = risk.get("score", 0)
    grade = risk.get("grade", "N/A")

    grade_label = {
        "A": "🟢 Low Risk",
        "B": "🟢 Low Risk",
        "C": "🟡 Medium Risk",
        "D": "🟠 High Risk",
        "F": "🔴 CRITICAL",
    }.get(grade, "Unknown")

    # Get findings
    failure_findings = state.get("failure_findings", [])[:2]
    cost_findings = state.get("cost_findings", [])[:1]

    strategic = state.get("strategic_summary", "").strip()

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🔍 KubeSentinel Analysis",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Risk Score:*\n{score}/100 ({grade})",
                },
                {"type": "mrkdwn", "text": f"*Level:*\n{grade_label}"},
            ],
        },
    ]

    # Add strategic summary
    if strategic:
        summary_text = strategic[:200] + "..." if len(strategic) > 200 else strategic
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"💡 *Summary*\n{summary_text}",
                },
            }
        )

    # Add failures
    if failure_findings:
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*🚨 Critical Issues:*",
                },
            }
        )
        for i, finding in enumerate(failure_findings, 1):
            title, desc, fix = extract_finding_details(finding)
            finding_text = f"*{i}. {title}*"
            if desc:
                finding_text += f"\n_{desc}_"
            if fix:
                finding_text += f"\n✅ {fix}"

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": finding_text},
                }
            )

    # Add cost findings
    if cost_findings:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*💰 Cost Optimization:*",
                },
            }
        )
        for finding in cost_findings:
            title, desc, fix = extract_finding_details(finding)
            cost_text = f"• {title}"
            if fix:
                cost_text += f"\n   → {fix}"
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": cost_text},
                }
            )

    # Add action buttons
    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "📄 View Full Report",
                        "emoji": True,
                    },
                    "value": "view_report",
                    "action_id": "view_report_action",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "🔧 Run Fixes",
                        "emoji": True,
                    },
                    "value": "run_fixes",
                    "action_id": "run_fixes_action",
                    "style": "danger",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Run Fixes?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": "This will execute recommended fixes. Continue?",
                        },
                        "confirm": {
                            "type": "plain_text",
                            "text": "Yes, run fixes",
                        },
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
            ],
        }
    )

    return blocks


def clean_text(text: str) -> str:
    """Remove Slack mention tokens like <@U04ABC123> from text."""
    return re.sub(r"<@[^>]+>", "", text).strip()


def extract_kubectl_commands(recommendation: str) -> list:
    """Extract kubectl commands from a recommendation string.

    Args:
        recommendation: The recommendation text (may contain multiple steps)

    Returns:
        List of kubectl command strings (without 'kubectl' prefix)
    """
    if not recommendation or "kubectl" not in recommendation.lower():
        return []

    commands = []

    # Pattern 1: kubectl get|describe|logs|rollout|scale ...
    # Match from kubectl through semicolon, period, or newline
    pattern = r"kubectl\s+([a-z]+(?:\s+[^;.\n]*?)?)(?=;|\.|\n|$)"
    matches = re.finditer(pattern, recommendation, re.IGNORECASE | re.MULTILINE)

    for match in matches:
        cmd = match.group(1).strip()
        # Clean up the command
        cmd = " ".join(cmd.split())  # Normalize whitespace
        # Remove trailing special chars
        cmd = re.sub(r"[,;.*]$", "", cmd).strip()
        # Remove comments (everything after #)
        cmd = re.sub(r"\s*#.*$", "", cmd).strip()
        if cmd:
            commands.append(cmd)

    # Pattern 2: Multi-line kubectl commands (split by newlines or pipes)
    # If no matches found above, try to extract from code-like formatting
    if not commands and "kubectl" in recommendation:
        # Split by newlines and extract kubectl lines
        for line in recommendation.split("\n"):
            line = line.strip()
            if line.startswith("kubectl"):
                cmd = line[7:].strip()  # Remove 'kubectl' prefix
                cmd = re.sub(r"\s*#.*$", "", cmd).strip()  # Remove comments
                if cmd:
                    commands.append(cmd)

    return commands


def extract_finding_details(finding: dict) -> tuple:
    """Extract actionable details from a finding.

    Args:
        finding: A finding dict from KubeSentinel agents

    Returns:
        Tuple of (title, description, fix_suggestion)
    """
    # Extract title from resource or issue
    title = finding.get("resource", "Unknown issue")

    # Extract description from analysis
    description = finding.get("analysis", "")

    # Extract fix from recommendation
    fix = finding.get("recommendation", "")

    # Truncate if too long
    title = title[:100] if title else "Unknown issue"
    description = description[:150] if description else ""
    fix = fix[:200] if fix else ""

    return (title, description, fix)


def format_summary(state: InfraState) -> str:
    """Format analysis state into actionable Slack response.

    Args:
        state: The InfraState returned from run_engine()

    Returns:
        A formatted text response with fixes and recommendations
    """
    risk = state.get("risk_score", {})
    score = risk.get("score", 0)
    grade = risk.get("grade", "N/A")

    # Grade to risk level and emoji mapping
    grade_info = {
        "A": ("🟢 Low Risk", "green"),
        "B": ("🟢 Low Risk", "green"),
        "C": ("🟡 Medium Risk", "yellow"),
        "D": ("🟠 High Risk", "orange"),
        "F": ("🔴 CRITICAL", "red"),
    }
    risk_label, _ = grade_info.get(grade, ("Unknown", "gray"))

    # Get findings
    failure_findings = state.get("failure_findings", [])[:3]
    cost_findings = state.get("cost_findings", [])[:2]
    security_findings = state.get("security_findings", [])[:1]

    # Strategic summary
    strategic = state.get("strategic_summary", "").strip()

    # Build the response
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "*🔍 KubeSentinel Analysis*",
        f"*Risk Score:* {score}/100 ({grade}) {risk_label}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # Add strategic summary if available
    if strategic:
        # Extract key actionable insight (first 250 chars)
        summary_preview = strategic[:250]
        if len(strategic) > 250:
            summary_preview = summary_preview.rsplit(" ", 1)[0] + "..."
        lines.append(f"💡 {summary_preview}")
        lines.append("")

    # Critical failures first (reliability issues)
    if failure_findings:
        lines.append("*🚨 Critical Issues (Reliability):*")
        for i, finding in enumerate(failure_findings, 1):
            title, desc, fix = extract_finding_details(finding)
            lines.append(f"\n{i}. *{title}*")
            if desc:
                lines.append(f"   ℹ️ {desc}")
            if fix:
                lines.append(f"   ✅ Fix: {fix}")
        lines.append("")

    # Cost optimizations
    if cost_findings:
        lines.append("*💰 Cost Optimization Opportunities:*")
        for i, finding in enumerate(cost_findings, 1):
            title, desc, fix = extract_finding_details(finding)
            lines.append(f"\n{i}. *{title}*")
            if fix:
                lines.append(f"   → {fix}")
        lines.append("")

    # Security findings
    if security_findings:
        lines.append("*🔒 Security Concerns:*")
        for i, finding in enumerate(security_findings, 1):
            title, desc, fix = extract_finding_details(finding)
            lines.append(f"\n{i}. *{title}*")
            if fix:
                lines.append(f"   → {fix}")
        lines.append("")

    # Quick actions
    lines.append("*Next Steps:*")
    lines.append("• Read full report: `report.md`")
    lines.append("• Restart failing pods: `kubectl rollout restart deployment <name>`")
    lines.append("• Check logs: `kubectl logs -f <pod-name> --namespace <namespace>`")
    lines.append("• Ask for more details: `@kubesentinel <specific question>`")

    return "\n".join(lines)


def run_analysis(query: str, thread_ts: str = "") -> str:
    """Run KubeSentinel analysis or respond with cached result.

    Args:
        query: The user's query
        thread_ts: Thread timestamp for caching context

    Returns:
        A formatted text response ready to post to Slack
    """
    # Check if this is a follow-up question for cached analysis
    is_followup = any(
        keyword in query.lower()
        for keyword in [
            "report",
            "show",
            "full",
            "details",
            "more",
            "explain",
            "why",
            "how",
            "what",
            "tell me about",
        ]
    )

    # If we have cached analysis and this looks like a follow-up, use cache
    if is_followup and thread_ts in _analysis_cache:
        logger.info(f"Using cached analysis for thread {thread_ts}")
        state = _analysis_cache[thread_ts]

        # Special: if asking for report, return the report file
        if any(word in query.lower() for word in ["report", "full report", "full"]):
            report_path = Path("report.md")
            if report_path.exists():
                content = report_path.read_text()
                # Return first 3000 chars (Slack limit is 4000)
                if len(content) > 3000:
                    return (
                        f"```\n{content[:2900]}\n...\n\n"
                        "[Full report too long for Slack. "
                        "View report.md in your terminal or editor]\n```"
                    )
                return f"```\n{content}\n```"
            return "Report not found. Run analysis first."

        # For other follow-ups, return the formatted summary
        return format_summary(state)

    # Run full analysis
    try:
        logger.info(f"Starting analysis for query: {query}")

        # Run the full KubeSentinel engine
        state = run_engine(
            user_query=query,
            namespace=None,
            agents=None,
            git_repo=None,
        )

        # Generate full report (side effect: writes to state["final_report"])
        build_report(state)

        # Cache result
        if thread_ts:
            _analysis_cache[thread_ts] = state

        # Format for Slack
        summary = format_summary(state)
        logger.info("Analysis complete")
        return summary

    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        return (
            f"❌ Analysis failed: {str(e)}\n\n"
            "Please check cluster connectivity and try again."
        )


def _format_report_for_slack(content: str) -> list[dict[str, Any]]:
    """Convert markdown report into Slack mrkdwn blocks.

    Args:
        content: Raw markdown report content

    Returns:
        List of Slack blocks for display
    """
    blocks: list[dict[str, Any]] = []
    lines = content.split("\n")

    buffer: list[str] = []
    in_code = False

    for line in lines:
        # Handle code blocks differently
        if line.startswith("```"):
            if buffer and not in_code:
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(buffer)},
                    }
                )
                buffer = []
            in_code = not in_code
            if in_code:
                buffer = ["```"]
            else:
                buffer.append("```")
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(buffer)},
                    }
                )
                buffer = []
        elif in_code:
            buffer.append(line)
        else:
            # Accumulate lines for batch processing
            buffer.append(line)
            # Flush buffer if it gets too long (Slack has a 2000 char limit per block)
            if len("\n".join(buffer)) > 1800:
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "\n".join(buffer)},
                    }
                )
                buffer = []

    # Flush remaining buffer
    if buffer:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(buffer)}}
        )

    return blocks


@app.action("view_report_action")
def handle_view_report(ack: Any, body: dict, say: Any) -> None:
    """Handle View Full Report button click."""
    ack()

    # Get the thread to find cached analysis
    message_ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", message_ts)

    logger.info(f"View report button clicked for thread {thread_ts}")

    report_path = Path("report.md")
    if report_path.exists():
        content = report_path.read_text()
        # Convert to Slack blocks for better formatting
        blocks = _format_report_for_slack(content)
        say(blocks=blocks, thread_ts=thread_ts)  # type: ignore
    else:
        say(
            text="📄 Report not found. Make sure analysis has completed.",
            thread_ts=thread_ts,
        )


@app.action("run_fixes_action")
def handle_run_fixes(ack: Any, body: dict, say: Any, client: Any) -> None:
    """Handle Run Fixes button click.

    CRITICAL RULES:
    - Extract remediation commands ONLY from structured finding.remediation.commands
    - Show approval buttons for write verbs (patch, scale, set, rollout, apply, delete)
    - Never execute immediately; wait for explicit approval action
    - Respect KUBESENTINEL_OPS allowlist if set
    - Log all attempts to persistence
    """
    ack()

    message_ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", message_ts)
    user_id = body["user"]["id"]

    logger.info(f"[run_fixes] button clicked by {user_id} in thread {thread_ts}")

    # Get cached analysis
    if thread_ts not in _analysis_cache:
        say(
            text="❌ No cached analysis found. Please run analysis first.",
            thread_ts=thread_ts,
        )
        return

    state = _analysis_cache[thread_ts]

    # Collect all findings with remediation.commands
    all_findings = (
        state.get("failure_findings", [])
        + state.get("cost_findings", [])
        + state.get("security_findings", [])
    )

    if not all_findings:
        say(
            text="✅ No issues detected in cluster. No remediation needed.",
            thread_ts=thread_ts,
        )
        return

    # Extract remediation commands (only from structured field, never from report.md)
    commands_to_execute = []  # List of (cmd, resource, finding_index)
    no_automation_count = 0

    for finding in all_findings:
        resource = finding.get("resource", "unknown")
        remediation = finding.get("remediation", {})

        if not isinstance(remediation, dict):
            remediation = {}

        commands = remediation.get("commands", [])
        automated = remediation.get("automated", False)

        if not commands or not automated:
            no_automation_count += 1
            continue

        # Add each command for approval
        for cmd in commands[:1]:  # Max 1 per finding to limit Slack message complexity
            commands_to_execute.append((cmd, resource))

    if not commands_to_execute:
        output_blocks: list = [  # type: ignore
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "📋 **No automated remediation available**\n\n"
                    f"{no_automation_count} finding(s) require manual investigation or have no executable fix.\n"
                    "Please review the findings in the report above.",
                },
            }
        ]
        say(blocks=output_blocks, thread_ts=thread_ts)  # type: ignore
        return

    # Build approval buttons for each command
    output_blocks: list = [  # type: ignore
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "🔒 **Remediation Requires Approval**\n\nReview each command and click 'Approve & Execute' to proceed:",
            },
        },
        {"type": "divider"},
    ]

    for cmd, resource in commands_to_execute:
        # Display command for review
        cmd_display = cmd if len(cmd) < 80 else cmd[:77] + "..."
        output_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📦 **Resource:** `{resource}`\n`{cmd_display}`",
                },
            }
        )

        # Add approval button with command as value
        output_blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve & Execute"},
                        "action_id": "approve_execute_action",
                        "value": f"{thread_ts}|{cmd}",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Skip"},
                        "action_id": "skip_execute_action",
                        "value": f"{thread_ts}|{cmd}",
                    },
                ],
            }
        )
        output_blocks.append({"type": "divider"})

    output_blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "⚠️ Each command will be validated, executed with 60s timeout, and fully logged.",
                }
            ],
        }
    )

    say(blocks=output_blocks, thread_ts=thread_ts)  # type: ignore


@app.action("approve_execute_action")
def handle_approve_execute(ack: Any, body: dict, say: Any) -> None:
    """Handle 'Approve & Execute' button click.

    Re-validates command, checks approval list, executes, and logs result.
    """
    ack()

    user_id = body["user"]["id"]
    value = body["actions"][0].get("value", "")

    if "|" not in value:
        logger.warning(f"[approve_execute] Invalid value format: {value}")
        say(text="❌ Internal error: invalid action value")
        return

    thread_ts, cmd = value.split("|", 1)
    message_ts = body["message"]["ts"]
    thread_ts_msg = body["message"].get("thread_ts", message_ts)

    logger.info(
        f"[approve_execute] user={user_id} thread={thread_ts_msg} cmd={cmd[:80]}"
    )

    # Parse command to argv
    try:
        argv = shlex.split("kubectl " + cmd.strip())
    except ValueError as e:
        say(
            text=f"❌ Command parse error: {str(e)}",
            thread_ts=thread_ts_msg,
        )
        return

    # Execute with approval tracking
    result = safe_kubectl_execute(argv, user_id=user_id, approver_user_id=user_id)

    # Format response
    if result["ok"]:
        output = (
            f"✅ **Success**\n```\n{result['stdout'][:500]}\n```"
            if result["stdout"]
            else "✅ **Success** (no output)"
        )
    else:
        output = f"❌ **Failed**\n```\n{result['stderr'][:500]}\n```"

    output += f"\n⏱️ Executed in {result['elapsed_seconds']:.2f}s"

    say(
        text=output,
        thread_ts=thread_ts_msg,
    )


@app.action("skip_execute_action")
def handle_skip_execute(ack: Any, body: dict, say: Any) -> None:
    """Handle 'Skip' button click."""
    ack()

    user_id = body["user"]["id"]
    message_ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", message_ts)

    say(text="⏭️ Skipped", thread_ts=thread_ts)


@app.event("app_mention")
def handle_app_mention(body: dict, say: Any, ack: Any) -> None:
    """Handle @kubesentinel mentions in channels."""
    # Acknowledge immediately to prevent Slack timeout retries
    ack()

    event = body["event"]
    text = event.get("text", "")
    user = event.get("user", "Unknown")
    thread_ts = event.get("thread_ts") or event["ts"]

    # Clean the mention prefix
    query = clean_text(text)

    if not query:
        say(text="Please ask me something! e.g., `why are pods pending`")
        return

    logger.info(f"App mention from <@{user}>: {query}")

    # Check for follow-ups that use cache
    is_followup = any(
        keyword in query.lower()
        for keyword in [
            "report",
            "show",
            "full",
            "details",
            "more",
            "explain",
        ]
    )

    if is_followup and thread_ts in _analysis_cache:
        logger.info(f"Using cached analysis for thread {thread_ts}")
        state = _analysis_cache[thread_ts]
    else:
        # Run full analysis
        logger.info(f"Starting analysis for query: {query}")
        try:
            state = run_engine(
                user_query=query,
                namespace=None,
                agents=None,
                git_repo=None,
            )
            build_report(state)
            if thread_ts:
                _analysis_cache[thread_ts] = state
        except Exception as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            say(
                text=f"❌ Analysis failed: {str(e)}\n\nPlease check cluster connectivity and try again.",
                thread_ts=thread_ts,
            )
            return

    # Format as blocks with buttons
    blocks = format_summary_blocks(state)

    # Reply in thread with blocks
    say(blocks=blocks, thread_ts=thread_ts)  # type: ignore


@app.event("message")
def handle_message(body: dict, say: Any, ack: Any) -> None:
    """Handle direct messages to the bot."""
    # Acknowledge immediately
    ack()

    event = body["event"]

    # Ignore bot messages to prevent loops
    if event.get("bot_id"):
        logger.debug("Ignoring message from bot")
        return

    # Only process direct messages (DMs), not channel messages
    if event.get("channel_type") != "im":
        logger.debug("Ignoring non-DM message")
        return

    text = event.get("text", "").strip()
    user = event.get("user", "Unknown")
    thread_ts = event.get("thread_ts") or event["ts"]

    if not text:
        return

    logger.info(f"DM from <@{user}>: {text}")

    # Check for follow-ups that use cache
    is_followup = any(
        keyword in text.lower()
        for keyword in [
            "report",
            "show",
            "full",
            "details",
            "more",
            "explain",
        ]
    )

    if is_followup and thread_ts in _analysis_cache:
        logger.info(f"Using cached analysis for thread {thread_ts}")
        state = _analysis_cache[thread_ts]
    else:
        # Run full analysis
        logger.info(f"Starting analysis for query: {text}")
        try:
            state = run_engine(
                user_query=text,
                namespace=None,
                agents=None,
                git_repo=None,
            )
            build_report(state)
            if thread_ts:
                _analysis_cache[thread_ts] = state
        except Exception as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            say(
                text=f"❌ Analysis failed: {str(e)}\n\nPlease check cluster connectivity and try again.",
                thread_ts=thread_ts,
            )
            return

    # Format as blocks with buttons
    blocks = format_summary_blocks(state)

    # Reply in thread with blocks
    say(blocks=blocks, thread_ts=thread_ts)  # type: ignore


def main() -> None:
    """Start the Slack Socket Mode handler."""
    logger.info("Starting KubeSentinel Slack bot...")
    logger.info("Listening for mentions (@kubesentinel) and direct messages...")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
