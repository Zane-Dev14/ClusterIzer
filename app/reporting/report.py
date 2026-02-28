"""Report generator — produces ``report.md``, ``report.json``, and optional PDF.

The report is fully functional with ``explainer_output=None`` (Waves 1-3).
Explainer sections are conditionally rendered when present (Wave 4+).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from app import config
from app.models import (
    ClusterMeta,
    CostEstimate,
    ExplainerOutput,
    Finding,
    Severity,
)
from app.tools.utils import pandoc_available, write_json

logger = logging.getLogger("clustergpt.report")


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------

def compute_risk_score(findings: list[Finding]) -> int:
    """Weighted risk score (0–100) from finding severities."""
    total = 0
    for f in findings:
        total += config.SEVERITY_WEIGHTS.get(f.severity.value, 0)
    return min(100, total)


# ---------------------------------------------------------------------------
# Markdown sections
# ---------------------------------------------------------------------------

def _header(meta: ClusterMeta, risk: int, cost: CostEstimate) -> str:
    return (
        f"# ClusterGPT Audit Report\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Cluster** | {meta.cluster_name} |\n"
        f"| **Time** | {meta.timestamp} |\n"
        f"| **Nodes** | {meta.node_count} |\n"
        f"| **Namespaces** | {meta.namespace_count} |\n"
        f"| **Pods** | {meta.pod_count} |\n"
        f"| **Risk Score** | {risk}/100 |\n"
        f"| **Est. Monthly Cost** | ${cost.monthly_total_usd:,.2f} |\n"
    )


def _findings_table(findings: list[Finding]) -> str:
    if not findings:
        return "\n## Findings\n\nNo findings detected — cluster looks healthy.\n"

    lines = [
        "\n## Findings\n",
        "| # | Severity | Category | Summary | Evidence | Remediation |",
        "|---|----------|----------|---------|----------|-------------|",
    ]
    for i, f in enumerate(findings, 1):
        ev_str = "; ".join(
            f"{e.kind} {e.namespace}/{e.name}" if e.namespace else f"{e.kind} {e.name}"
            for e in f.evidence[:2]
        )
        kubectl_str = f.remediation.kubectl[0] if f.remediation.kubectl else ""
        # Escape pipes in table cells
        summary = f.summary.replace("|", "\\|")[:120]
        ev_str = ev_str.replace("|", "\\|")[:80]
        kubectl_str = kubectl_str.replace("|", "\\|")[:80]
        lines.append(
            f"| {i} | **{f.severity.value}** | {f.category.value} | "
            f"{summary} | {ev_str} | `{kubectl_str}` |"
        )
    return "\n".join(lines) + "\n"


def _cost_section(cost: CostEstimate) -> str:
    lines = [
        "\n## Cost Analysis\n",
        f"**Estimated monthly total:** ${cost.monthly_total_usd:,.2f}  ",
        f"**Estimated waste:** {cost.waste_pct:.1f}%\n",
    ]
    if cost.deployments:
        lines.append(
            "| Namespace | Deployment | Replicas | CPU Req | Mem Req (Gi) | $/month |"
        )
        lines.append(
            "|-----------|------------|----------|---------|--------------|---------|"
        )
        for d in sorted(cost.deployments, key=lambda x: -x.monthly_cost_usd):
            lines.append(
                f"| {d.namespace} | {d.name} | {d.replicas} | "
                f"{d.cpu_requests:.3f} | {d.mem_requests_gb:.3f} | "
                f"${d.monthly_cost_usd:,.2f} |"
            )
    return "\n".join(lines) + "\n"


def _remediation_section(findings: list[Finding]) -> str:
    """Emit remediation commands for all findings that have them."""
    actionable = [f for f in findings if f.remediation.kubectl or f.remediation.patch_yaml]
    if not actionable:
        return ""

    lines = ["\n## Remediation Commands\n"]
    for f in actionable:
        lines.append(f"### {f.id}\n")
        lines.append(f"> {f.remediation.description}\n")
        if f.remediation.kubectl:
            lines.append("```bash")
            for cmd in f.remediation.kubectl:
                lines.append(cmd)
            lines.append("```\n")
        if f.remediation.patch_yaml:
            lines.append("```yaml")
            lines.append(f.remediation.patch_yaml)
            lines.append("```\n")
    return "\n".join(lines) + "\n"


def _explainer_section(explainer: ExplainerOutput) -> str:
    """Render explainer output.  Returns empty string if explainer is empty."""
    parts: list[str] = []

    if explainer.exec_summary:
        parts.append(f"\n## Executive Summary\n\n{explainer.exec_summary}\n")

    if explainer.sre_actions:
        parts.append("\n## SRE Action Plan\n")
        for a in explainer.sre_actions:
            parts.append(f"### Priority {a.priority}: {a.title} (confidence: {a.confidence.value})\n")
            for step in a.steps:
                parts.append(f"- {step}")
            parts.append("")

    if explainer.pr_text and explainer.pr_text.title:
        parts.append(f"\n## Recommended PR\n")
        parts.append(f"**{explainer.pr_text.title}**\n")
        parts.append(f"{explainer.pr_text.description}\n")
        if explainer.pr_text.changes:
            parts.append("Changes:")
            for c in explainer.pr_text.changes:
                parts.append(f"- {c}")
            parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(
    cluster_meta: ClusterMeta,
    findings: list[Finding],
    cost: CostEstimate,
    explainer_output: Optional[ExplainerOutput] = None,
    out_path: str = config.DEFAULT_OUTPUT,
) -> Path:
    """Generate audit report as Markdown (+ JSON + optional PDF).

    Parameters
    ----------
    cluster_meta:
        Lightweight cluster metadata.
    findings:
        All findings (rules + diagnosis), sorted by severity.
    cost:
        Cost estimate from the cost model.
    explainer_output:
        Optional LLM / template explanation.  ``None`` is perfectly fine.
    out_path:
        Destination file (Markdown).

    Returns
    -------
    Path
        The written report path.
    """
    risk = compute_risk_score(findings)

    sections = [
        _header(cluster_meta, risk, cost),
        _findings_table(findings),
        _cost_section(cost),
        _remediation_section(findings),
    ]

    if explainer_output is not None:
        sections.append(_explainer_section(explainer_output))

    md = "\n".join(sections)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    logger.info("Report written to %s", out)

    # --- JSON snapshot of the report data ---
    json_path = out.with_suffix(".json")
    report_data = {
        "cluster_meta": cluster_meta.model_dump(),
        "risk_score": risk,
        "findings_count": len(findings),
        "findings": [f.model_dump() for f in findings],
        "cost": cost.model_dump(),
    }
    if explainer_output:
        report_data["explainer"] = explainer_output.model_dump()
    write_json(report_data, json_path)
    logger.info("JSON report written to %s", json_path)

    # --- Optional PDF via pandoc ---
    if pandoc_available():
        pdf_path = out.with_suffix(".pdf")
        try:
            subprocess.run(
                ["pandoc", str(out), "-o", str(pdf_path), "--pdf-engine=xelatex"],
                capture_output=True,
                timeout=30,
                check=True,
            )
            logger.info("PDF report written to %s", pdf_path)
        except Exception as exc:
            logger.debug("PDF generation skipped: %s", exc)

    return out
