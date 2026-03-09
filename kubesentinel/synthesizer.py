"""Synthesis module - formats findings into executive summaries.

This module handles post-analysis synthesis of findings into strategic summaries.
It is NOT an agent - it runs AFTER all agents complete.

Responsibilities:
  - Normalize finding structure
  - Generate executive summaries
  - Format recommendations
  - Sanitize diagnostic commands from remediation

This is an orchestration concern, not an agent concern.
"""

import logging
import re
import shlex
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from .models import InfraState

logger = logging.getLogger(__name__)

# Initialize LLM
LLM = ChatOllama(model="qwen3:30b", temperature=0)
PROMPT_DIR = Path(__file__).parent / "prompts"
VERBOSE = __import__("os").getenv("KUBESENTINEL_VERBOSE_AGENTS") == "1"


def ensure_remediation_field(
    findings: List[Dict[str, Any]], signals: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Ensure all findings have remediation field. Prefer deterministic fixes from signals.

    CRITICAL: This normalizes findings to Phase N structure where:
    - finding["remediation"]["commands"] are executed by Slack
    - finding["verification"]["commands"] are for manual inspection only

    Args:
        findings: List of findings from agents
        signals: Optional list of signals with deterministic diagnoses

    Returns:
        List of findings with normalized remediation fields
    """
    if not findings:
        return []

    signals = signals or []
    # Index signals by resource for quick lookup
    signal_map = {sig.get("resource"): sig for sig in signals if sig.get("diagnosis")}

    normalized = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue

        resource = finding.get("resource", "")

        # Start with existing remediation or create new
        remediation = finding.get("remediation", {})
        if not isinstance(remediation, dict):
            remediation = {}

        # Rule 1: Prefer deterministic fixes from signals
        if not remediation.get("commands") and resource in signal_map:
            signal = signal_map[resource]
            diagnosis = signal.get("diagnosis", {})
            if diagnosis.get("recommended_fix"):
                # Extract recommended_fix into remediation commands
                fix = diagnosis["recommended_fix"]
                if isinstance(fix, list):
                    remediation["commands"] = fix
                elif isinstance(fix, str) and fix.strip():
                    remediation["commands"] = [fix]
                remediation["risk_level"] = diagnosis.get("risk_level", "medium")
                remediation["automated"] = True
                if VERBOSE:
                    logger.debug(
                        f"Using deterministic fix for {resource}: {remediation.get('commands')}"
                    )

        # Rule 2: Convert old "recommendation" field to remediation.commands if needed
        if not remediation.get("commands"):
            recommendation = finding.get("recommendation", "")
            if recommendation and isinstance(recommendation, str):
                if "kubectl" in recommendation.lower():
                    # Use recommendation string as a single command
                    remediation["commands"] = [recommendation]
                    remediation["automated"] = True
                    remediation["risk_level"] = remediation.get("risk_level", "medium")

        # Rule 3: Ensure remediation field exists (even if empty)
        if not remediation.get("commands"):
            remediation["commands"] = []
            remediation["automated"] = False
            remediation["risk_level"] = "none"

        # Set remediation on finding
        finding["remediation"] = remediation

        # Rule 4: Create verification field if not present (diagnostic commands only)
        if "verification" not in finding:
            finding["verification"] = {
                "commands": [],
                "automated": False,
                "note": "For manual inspection only - Slack will not execute these",
            }

        normalized.append(finding)

    if VERBOSE and normalized:
        logger.debug(
            f"Normalized {len(normalized)} findings - all have remediation field"
        )

    return normalized


def synthesize_strategic_summary(state: InfraState) -> str:
    """Generate deterministic strategic summary from findings (no LLM).

    Produces structured output based on verified findings and risk assessment.

    Args:
        state: Current infrastructure state with findings

    Returns:
        Strategic summary string
    """
    failure = state.get("failure_findings", [])
    cost = state.get("cost_findings", [])
    security = state.get("security_findings", [])
    risk = state.get("risk_score", {})
    snapshot = state.get("cluster_snapshot", {})

    lines = []
    lines.append("# Strategic Summary")
    lines.append("")

    # Risk assessment header
    risk_score = risk.get("score", 0)
    risk_grade = risk.get("grade", "N/A")
    lines.append(f"## Risk Assessment: {risk_score}/100 ({risk_grade})")
    lines.append(
        f"- Cluster Size: {len(snapshot.get('nodes', []))} nodes, {len(snapshot.get('pods', []))} pods"
    )
    lines.append(f"- Total Signals: {risk.get('signal_count', 0)}")
    lines.append("- Agents Executed: failure_agent, cost_agent (top-2 selection)")
    lines.append("")

    # Critical findings
    critical_findings = []
    for finding_list in [failure, cost, security]:
        critical_findings.extend(
            [f for f in finding_list if f.get("severity") == "critical"]
        )

    if critical_findings:
        lines.append(f"## Critical Issues ({len(critical_findings)} found)")
        for finding in critical_findings[:5]:  # Top 5 critical
            lines.append(f"- **{finding.get('resource')}**: {finding.get('analysis')}")
            if finding.get("verified"):
                lines.append(f"  Evidence: {finding.get('evidence', 'N/A')[:100]}")
            lines.append(f"  Action: {finding.get('recommendation')}")
            lines.append("")
    else:
        lines.append("## No Critical Issues Detected")
        lines.append("")

    # Category breakdown
    lines.append("## Findings by Category")
    lines.append(f"- **Reliability**: {len(failure)} findings")
    if failure:
        high_reliability = [
            f for f in failure if f.get("severity") in ("critical", "high")
        ]
        if high_reliability:
            lines.append(
                f"  - {len(high_reliability)} high-severity findings require immediate attention"
            )

    lines.append(f"- **Cost**: {len(cost)} findings")
    if cost:
        savings_potential = len(
            [f for f in cost if f.get("severity") in ("high", "critical")]
        )
        if savings_potential:
            lines.append(
                f"  - {savings_potential} optimization opportunities identified"
            )

    lines.append(f"- **Security**: {len(security)} findings")
    if security:
        security_critical = [f for f in security if f.get("severity") == "critical"]
        if security_critical:
            lines.append(
                f"  - {len(security_critical)} critical vulnerabilities need remediation"
            )

    lines.append("")

    # Recommendations
    if failure or cost or security:
        lines.append("## Recommended Actions (Prioritized)")
        idx = 1

        # Critical findings first
        for finding in critical_findings[:3]:
            lines.append(
                f"{idx}. **{finding.get('resource', 'Cluster')}** ({finding.get('severity')})"
            )
            lines.append(f"   - Issue: {finding.get('analysis')}")
            lines.append(f"   - Action: {finding.get('recommendation')}")
            evidence = finding.get("evidence")
            if finding.get("verified") and evidence:
                lines.append(f"   - Evidence: {evidence[:100]}")
            idx += 1

        # High-severity findings
        high_findings = []
        for finding_list in [failure, cost, security]:
            high_findings.extend(
                [f for f in finding_list if f.get("severity") == "high"]
            )

        for finding in high_findings[:2]:
            lines.append(
                f"{idx}. **{finding.get('resource', 'Cluster')}** ({finding.get('severity')})"
            )
            lines.append(f"   - Issue: {finding.get('analysis')}")
            lines.append(f"   - Action: {finding.get('recommendation')}")
            idx += 1

    lines.append("")
    lines.append("## Verification Status")
    verified_count = sum(1 for f in failure + cost + security if f.get("verified"))
    total_count = len(failure) + len(cost) + len(security)
    lines.append(
        f"- {verified_count}/{total_count} findings verified with cluster evidence"
    )

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by KubeSentinel at {datetime.utcnow().isoformat()}*")

    return "\n".join(lines)


def sanitize_findings_remediation(
    findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Move diagnostic kubectl commands out of remediation into verification.

    CRITICAL RULE: The remediation field may only contain commands that perform actual fixes.
    Diagnostic verbs (get, describe, logs, exec) are never executed by Slack and must be moved
    to the verification field as "informational only."

    Args:
        findings: List of findings with remediation/verification fields

    Returns:
        Same findings list, with remediation/verification properly sanitized
    """
    diag_verbs = {"get", "describe", "logs", "exec", "top", "explain"}

    for finding in findings:
        if not isinstance(finding, dict):
            continue

        remediation = finding.get("remediation", {})
        if not isinstance(remediation, dict):
            continue

        verification = finding.get("verification", {})
        if not isinstance(verification, dict):
            finding["verification"] = verification = {}

        # Initialize command lists
        remediation_commands = remediation.get("commands", [])
        if not isinstance(remediation_commands, list):
            remediation_commands = (
                [remediation_commands] if remediation_commands else []
            )

        verification_commands = verification.get("commands", [])
        if not isinstance(verification_commands, list):
            verification_commands = (
                [verification_commands] if verification_commands else []
            )

        # Split commands: diagnostic → verification, remediation → remediation
        sanitized_remediation = []
        for cmd in remediation_commands:
            if not cmd or not isinstance(cmd, str):
                continue

            try:
                parts = shlex.split(cmd)
                if not parts:
                    continue

                # Extract verb (handle "kubectl <verb> ..." or just "<verb> ...")
                verb_idx = 1 if parts[0] == "kubectl" else 0
                if verb_idx < len(parts):
                    verb = parts[verb_idx].lower()
                    if verb in diag_verbs:
                        # Move diagnostic command to verification
                        verification_commands.append(cmd)
                        if VERBOSE:
                            logger.debug(
                                f"[sanitizer] Moved diagnostic verb '{verb}' from remediation to verification"
                            )
                        continue

                # Command is not diagnostic, keep in remediation
                sanitized_remediation.append(cmd)
            except ValueError:
                # shlex.split failed (malformed command)
                logger.warning(f"[sanitizer] Failed to parse command: {cmd}")
                # Keep it in remediation for now; slack will reject if invalid
                sanitized_remediation.append(cmd)

        # Update finding with sanitized commands
        remediation["commands"] = sanitized_remediation
        # Set automated=False if we have no remediation commands (only verification)
        if not sanitized_remediation and remediation.get("automated"):
            remediation["automated"] = False

        # Update verification
        verification["commands"] = verification_commands
        finding["verification"] = verification

    return findings


def synthesizer_node(state: InfraState) -> InfraState:
    """Synthesis node - formats agent findings into executive summary.

    NOTE: This is NOT an agent. It runs AFTER agents complete.
    It orchestrates the synthesis of findings into strategic summaries.

    Args:
        state: InfraState with findings from agents

    Returns:
        InfraState with strategic_summary populated
    """
    logger.info("[synthesizer] Starting synthesis...")

    # Phase N: Ensure all findings have remediation field (prefer deterministic fixes)
    try:
        failure_findings = state.get("failure_findings", [])
        cost_findings = state.get("cost_findings", [])
        security_findings = state.get("security_findings", [])
        signals = state.get("signals", [])

        # Normalize all findings to have remediation field
        # This prefers deterministic fixes from signals
        failure_findings = ensure_remediation_field(failure_findings, signals)
        cost_findings = ensure_remediation_field(cost_findings, signals)
        security_findings = ensure_remediation_field(security_findings, signals)

        # CRITICAL: Sanitize diagnostic verbs out of remediation
        # Move kubectl get/describe/logs/exec from remediation → verification
        failure_findings = sanitize_findings_remediation(failure_findings)
        cost_findings = sanitize_findings_remediation(cost_findings)
        security_findings = sanitize_findings_remediation(security_findings)

        # Update state with normalized findings
        state["failure_findings"] = failure_findings
        state["cost_findings"] = cost_findings
        state["security_findings"] = security_findings

        if VERBOSE:
            logger.debug(
                f"Normalized findings: {len(failure_findings)} failure, {len(cost_findings)} cost, {len(security_findings)} security"
            )
    except Exception as e:
        logger.error(f"[synthesizer] Error normalizing findings: {e}")

    # Use deterministic synthesis
    try:
        # First try deterministic summary (no LLM, faster, more reliable)
        summary = synthesize_strategic_summary(state)

        # Optionally, enhance with LLM if available (for richer formatting)
        # but don't fail if LLM unavailable
        try:
            system_prompt = (PROMPT_DIR / "synthesizer.txt").read_text()
            context = f"Create a strategic summary based on this analysis:\n\n{summary}"

            response = LLM.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=context)]
            )
            llm_summary = (
                response.content if hasattr(response, "content") else str(response)
            )
            llm_summary = (
                str(llm_summary) if not isinstance(llm_summary, str) else llm_summary
            )

            # Check for placeholders (indicates hallucination)
            placeholder_pattern = r"<[a-z\-_]+>"
            placeholders_found = re.findall(
                placeholder_pattern, llm_summary, re.IGNORECASE
            )
            if placeholders_found:
                logger.warning(
                    f"[synthesizer] LLM output contains placeholders: {set(placeholders_found)} - using deterministic summary instead"
                )
                summary = summary  # Use deterministic version
            else:
                # LLM enhanced successfully
                summary = llm_summary if llm_summary else summary
                logger.debug("[synthesizer] LLM enhanced strategic summary")
        except Exception as e:
            logger.debug(
                f"[synthesizer] LLM enhancement skipped: {e} - using deterministic summary"
            )
            # Fallback to deterministic summary
            pass

        state["strategic_summary"] = (
            summary[:8000] + "\n[Summary truncated]" if len(summary) > 8000 else summary
        )
        logger.info("[synthesizer] Synthesis complete")
    except Exception as e:
        logger.error(f"[synthesizer] Error: {e}")
        state["strategic_summary"] = "Error generating strategic summary."

    return state
