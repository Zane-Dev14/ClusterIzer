"""Explainer Agent — LLM wrapper with strict JSON contract.

If ``OPENAI_API_KEY`` is set, calls OpenAI with structured facts and
expects the model to return ``ExplainerOutput`` JSON.  If the key is
missing **or** the response is invalid JSON, falls back to a fully
functional deterministic template.

Usage::

    from app.agents.explainer import explain, build_facts
    facts = build_facts(findings, cost, cluster_meta)
    output = explain(facts)      # ExplainerOutput
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app import config
from app.models import (
    Confidence,
    CostEstimate,
    ClusterMeta,
    ExplainerOutput,
    Finding,
    PRText,
    SREAction,
)

logger = logging.getLogger("clustergpt.explainer")


# ---------------------------------------------------------------------------
# Schema string (embedded in the system prompt for the LLM)
# ---------------------------------------------------------------------------

_EXPLAINER_SCHEMA = """{
  "exec_summary": "string (1-3 short paragraphs)",
  "sre_actions": [
    {"priority": 1, "title": "string", "steps": ["string"], "confidence": "high|medium|low"}
  ],
  "pr_text": {
    "title": "string",
    "description": "string",
    "changes": ["string"]
  },
  "confidence_overall": "high|medium|low"
}"""

_SYSTEM_PROMPT = f"""\
You are ClusterGPT, an expert Kubernetes auditor.  You receive structured
facts about a cluster audit (findings, cost summary, metadata) and must
return ONLY a JSON object matching this exact schema — no markdown, no
explanation, no extra keys:

{_EXPLAINER_SCHEMA}

Rules:
- exec_summary: 1-3 concise paragraphs for a CTO.
- sre_actions: top 3 actionable items ordered by priority.
- pr_text: a pull-request description summarising the recommended changes.
- confidence_overall: your confidence that the recommendations are correct.
- Do NOT hallucinate.  Only reference data provided in the user message.
- If asked about something not in the data, say "insufficient data".
"""


# ---------------------------------------------------------------------------
# Fact builder — assembles structured input from findings + cost
# ---------------------------------------------------------------------------

def build_facts(
    findings: list[Finding],
    cost: CostEstimate,
    cluster_meta: ClusterMeta,
) -> dict[str, Any]:
    """Build the structured facts dict to send to the explainer.

    This intentionally excludes raw logs and only includes counts,
    top findings (id + summary + evidence pointer), and cost totals.
    """
    severity_counts: dict[str, int] = {}
    for f in findings:
        severity_counts[f.severity.value] = severity_counts.get(f.severity.value, 0) + 1

    top_findings = [
        {
            "id": f.id,
            "severity": f.severity.value,
            "category": f.category.value,
            "summary": f.summary[:200],
            "evidence_pointer": f.evidence[0].pointer if f.evidence else "",
            "remediation": f.remediation.description[:200],
        }
        for f in findings[:10]  # cap at 10 to fit in context
    ]

    return {
        "cluster": {
            "name": cluster_meta.cluster_name,
            "nodes": cluster_meta.node_count,
            "namespaces": cluster_meta.namespace_count,
            "pods": cluster_meta.pod_count,
            "timestamp": cluster_meta.timestamp,
        },
        "findings_count": len(findings),
        "severity_counts": severity_counts,
        "top_findings": top_findings,
        "cost": {
            "monthly_total_usd": cost.monthly_total_usd,
            "waste_pct": cost.waste_pct,
            "top_spenders": [
                {"name": f"{d.namespace}/{d.name}", "usd": d.monthly_cost_usd}
                for d in sorted(cost.deployments, key=lambda x: -x.monthly_cost_usd)[:5]
            ],
        },
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def explain(facts: dict[str, Any]) -> ExplainerOutput:
    """Generate an explanation from structured facts.

    Attempts OpenAI if ``OPENAI_API_KEY`` is set; otherwise uses the
    deterministic template fallback.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if api_key:
        try:
            return _call_openai(facts, api_key)
        except Exception as exc:
            logger.warning("OpenAI call failed, falling back to template: %s", exc)

    return _template_fallback(facts)


# ---------------------------------------------------------------------------
# OpenAI path
# ---------------------------------------------------------------------------

def _call_openai(facts: dict[str, Any], api_key: str) -> ExplainerOutput:
    """Call OpenAI and parse the response strictly."""
    import openai

    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        temperature=config.OPENAI_TEMPERATURE,
        max_tokens=config.OPENAI_MAX_TOKENS,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(facts, default=str)},
        ],
    )

    raw = response.choices[0].message.content or ""
    logger.debug("Raw LLM response: %s", raw[:500])

    # Strip markdown fences if the model wraps them.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Remove ```json ... ``` wrapper
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    parsed = json.loads(cleaned)
    return ExplainerOutput.model_validate(parsed)


# ---------------------------------------------------------------------------
# Deterministic template fallback
# ---------------------------------------------------------------------------

def _template_fallback(facts: dict[str, Any]) -> ExplainerOutput:
    """Build ExplainerOutput from structured facts without any LLM."""
    cluster = facts.get("cluster", {})
    sev = facts.get("severity_counts", {})
    cost_info = facts.get("cost", {})
    top = facts.get("top_findings", [])

    # --- Executive summary ---
    critical = sev.get("critical", 0)
    high = sev.get("high", 0)
    medium = sev.get("medium", 0)
    total = facts.get("findings_count", 0)

    exec_summary = (
        f"Audit of cluster '{cluster.get('name', 'unknown')}' "
        f"({cluster.get('nodes', '?')} nodes, {cluster.get('pods', '?')} pods) "
        f"identified {total} findings: "
        f"{critical} critical, {high} high, {medium} medium. "
        f"Estimated monthly cost is ${cost_info.get('monthly_total_usd', 0):,.2f} "
        f"with approximately {cost_info.get('waste_pct', 0):.0f}% resource waste."
    )

    if critical > 0:
        exec_summary += (
            " Immediate action is required to address critical "
            "security and reliability issues."
        )

    # --- SRE actions (top 3) ---
    sre_actions: list[SREAction] = []
    for i, f in enumerate(top[:3], 1):
        sre_actions.append(SREAction(
            priority=i,
            title=f.get("summary", "")[:80],
            steps=[
                f.get("remediation", "Review and fix."),
                f"Evidence: {f.get('evidence_pointer', 'see report')}",
            ],
            confidence=Confidence.high if f.get("severity") in ("critical", "high") else Confidence.medium,
        ))

    # --- PR text ---
    changes = [
        f"[{f.get('severity', '?')}] {f.get('id', '?')}: {f.get('remediation', '')[:100]}"
        for f in top[:5]
    ]
    pr_text = PRText(
        title=f"[ClusterGPT] Fix {total} audit findings in {cluster.get('name', 'cluster')}",
        description=(
            f"This PR addresses {total} findings identified by ClusterGPT audit. "
            f"Top priorities: {critical} critical, {high} high severity issues."
        ),
        changes=changes,
    )

    return ExplainerOutput(
        exec_summary=exec_summary,
        sre_actions=sre_actions,
        pr_text=pr_text,
        confidence_overall=Confidence.high if not critical else Confidence.medium,
    )
