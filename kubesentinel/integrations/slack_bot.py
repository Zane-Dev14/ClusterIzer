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
from typing import Any
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from kubesentinel.runtime import run_engine
from kubesentinel.reporting import build_report
from kubesentinel.models import InfraState

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


def safe_kubectl_command(command: str, approval_token: str = "") -> str:
    """Execute a kubectl command safely with hardened validation.

    Args:
        command: The kubectl command to run (without 'kubectl' prefix)
        approval_token: Optional approval token for destructive commands (not yet implemented)

    Returns:
        Command output or error message
    """
    import shlex
    
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
        
        # Run kubectl command with timeout
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
        
        # Log successful execution for audit
        logger.info(f"Slack kubectl: {verb} {' '.join(args[1:3])} (user via Slack bot)")
        
        return f"✅ Success:\n```\n{output}\n```"

    except subprocess.TimeoutExpired:
        return "❌ Command timed out (>10s)"
    except Exception as e:
        logger.error(f"kubectl execution error: {e}")
        return f"❌ Error: {str(e)}"


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
        say(blocks=blocks, thread_ts=thread_ts)
    else:
        say(
            text="📄 Report not found. Make sure analysis has completed.",
            thread_ts=thread_ts,
        )


@app.action("run_fixes_action")
def handle_run_fixes(ack: Any, body: dict, say: Any) -> None:
    """Handle Run Fixes button click."""
    ack()

    # Get the thread
    message_ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", message_ts)

    logger.info(f"Run fixes button clicked for thread {thread_ts}")

    # Get cached analysis
    if thread_ts not in _analysis_cache:
        say(
            text="❌ No cached analysis found. Please run analysis first.",
            thread_ts=thread_ts,
        )
        return

    state = _analysis_cache[thread_ts]

    # Get recommendations from findings
    all_findings = (
        state.get("failure_findings", [])
        + state.get("cost_findings", [])
        + state.get("security_findings", [])
    )

    if not all_findings:
        say(
            text="✅ No specific fixes available for this analysis.",
            thread_ts=thread_ts,
        )
        return

    # Build output with kubectl commands and execution results
    output_blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "🔧 *Executing Recommended Fixes:*"},
        },
        {"type": "divider"},
    ]

    executed_count = 0
    kubectl_commands_run = []

    # Extract all kubectl commands from recommendations
    for finding in all_findings[:5]:  # Process up to 5 findings
        recommendation = finding.get("recommendation", "")
        if not recommendation:
            continue

        commands = extract_kubectl_commands(recommendation)
        if commands:
            for cmd in commands[:2]:  # Max 2 commands per finding
                logger.info(f"Running kubectl command: {cmd}")
                result = safe_kubectl_command(cmd)
                kubectl_commands_run.append((cmd, result))
                executed_count += 1

    # If no commands found in findings, try to extract from the report
    if not kubectl_commands_run:
        logger.info("No kubectl commands in findings, checking report.md...")
        report_path = Path("report.md")
        if report_path.exists():
            report_content = report_path.read_text()
            # Extract all unique kubectl commands from the report
            all_commands_from_report: dict[str, tuple[str, str]] = {}
            for line in report_content.split("\n"):
                if "kubectl" in line.lower():
                    commands = extract_kubectl_commands(line)
                    for cmd in commands:
                        if cmd not in all_commands_from_report:
                            # Try to execute but limit to 5 commands total
                            if len(all_commands_from_report) < 5:
                                logger.info(f"Executing from report: {cmd}")
                                result = safe_kubectl_command(cmd)
                                all_commands_from_report[cmd] = (cmd, result)
                                kubectl_commands_run.append((cmd, result))

            # Use collected commands if any were found
            if kubectl_commands_run:
                logger.info(f"Found {len(kubectl_commands_run)} commands from report")

    # If we found and executed kubectl commands, show them
    if kubectl_commands_run:
        output_blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*✅ kubectl Commands Executed:*"},
            }
        )

        for cmd, result in kubectl_commands_run:
            cmd_text = f"\\`kubectl {cmd}\\`"
            output_blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"{cmd_text}\n{result}"},
                }
            )
            output_blocks.append({"type": "divider"})

        say(blocks=output_blocks, thread_ts=thread_ts)
    else:
        # Show recommendation text at the end
        output_blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*📋 Recommended Commands:*",
                },
            }
        )

        rec_text_lines = []
        for i, finding in enumerate(all_findings[:3], 1):
            rec = finding.get("recommendation", "")
            if rec:
                # Limit length for Slack
                rec_preview = rec[:300] + "..." if len(rec) > 300 else rec
                rec_text_lines.append(f"{i}. {rec_preview}")

        if rec_text_lines:
            output_blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "\n\n".join(rec_text_lines)},
                }
            )

        say(blocks=output_blocks, thread_ts=thread_ts)


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
    say(blocks=blocks, thread_ts=thread_ts)


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
    say(blocks=blocks, thread_ts=thread_ts)


def main() -> None:
    """Start the Slack Socket Mode handler."""
    logger.info("Starting KubeSentinel Slack bot...")
    logger.info("Listening for mentions (@kubesentinel) and direct messages...")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
