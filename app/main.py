"""ClusterGPT CLI — ``clustergpt analyze`` orchestrator.

Pipeline (v1 — Wave 1):
    connector → rules → cost → report

Later waves add: graph_builder, investigator, explainer, remediation, verifier.

Usage::

    python -m app.main analyze --kubeconfig ~/.kube/config --output report.md
    python -m app.main snapshot --kubeconfig ~/.kube/config
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

from app import config
from app.models import Finding
from app.tools.utils import rprint, write_json

# ---------------------------------------------------------------------------
# Typer application
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="clustergpt",
    help="ClusterGPT — Autonomous Kubernetes Auditor & Co-Pilot",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------
# analyze — the primary command
# ---------------------------------------------------------------------------

@app.command()
def analyze(
    kubeconfig: str = typer.Option(
        config.DEFAULT_KUBECONFIG,
        "--kubeconfig", "-k",
        help="Path to kubeconfig file.",
    ),
    output: str = typer.Option(
        config.DEFAULT_OUTPUT,
        "--output", "-o",
        help="Path for the Markdown report.",
    ),
    namespace: Optional[list[str]] = typer.Option(
        None,
        "--namespace", "-n",
        help="Restrict analysis to specific namespace(s).  Repeat for multiple.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply top remediations (requires confirmation).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show remediation patches without applying.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes", "-y",
        help="Skip interactive confirmation (for CI).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Write intermediate JSON outputs to snapshots/.",
    ),
    price_cpu: Optional[float] = typer.Option(
        None,
        "--price-cpu",
        help="Override CPU $/hour (default: 0.03).",
    ),
    price_ram: Optional[float] = typer.Option(
        None,
        "--price-ram",
        help="Override RAM (GB) $/hour (default: 0.004).",
    ),
) -> None:
    """Analyse a Kubernetes cluster and produce an audit report."""
    _setup_logging(debug)
    logger = logging.getLogger("clustergpt")

    # --- lazy imports to keep CLI startup fast ---
    from app.tools.k8s_connector import (
        build_cluster_meta,
        save_snapshot,
        snapshot_cluster,
    )
    from app.agents.graph_builder import build_graph, save_graph, graph_summary
    from app.rules import run_all_rules
    from app.tools.cost_model import estimate_cost
    from app.agents.investigator import investigate
    from app.agents.explainer import build_facts, explain
    from app.agents.remediation import generate_remediations, apply_remediation
    from app.agents.verifier import verify
    from app.reporting.report import generate_report, compute_risk_score

    # 1. Snapshot -----------------------------------------------------------
    rprint("[bold cyan]▶ Snapshotting cluster…[/bold cyan]")
    snap = snapshot_cluster(kubeconfig, namespace)

    if debug:
        path = save_snapshot(snap)
        rprint(f"  Snapshot saved to {path}", style="dim")

    meta = build_cluster_meta(snap)
    rprint(
        f"  Cluster: [bold]{meta.cluster_name}[/bold]  "
        f"Nodes: {meta.node_count}  Pods: {meta.pod_count}"
    )

    # 2. Graph builder ------------------------------------------------------
    rprint("[bold cyan]▶ Building dependency graph…[/bold cyan]")
    graph = build_graph(snap)
    g_summary = graph_summary(graph)
    rprint(
        f"  Graph: {g_summary['node_count']} nodes, "
        f"{g_summary['edge_count']} edges"
    )
    if debug:
        save_graph(graph)

    # 3. Rules (all 12) -----------------------------------------------------
    rprint("[bold cyan]▶ Running audit rules…[/bold cyan]")
    findings: list[Finding] = run_all_rules(snap, graph)
    rprint(f"  {len(findings)} finding(s) detected")

    if debug:
        write_json(
            [f.model_dump() for f in findings],
            Path(config.SNAPSHOT_DIR) / "findings.json",
        )

    # 4. Cost estimate ------------------------------------------------------
    rprint("[bold cyan]▶ Estimating cost…[/bold cyan]")
    price_map: dict[str, float] | None = None
    if price_cpu is not None or price_ram is not None:
        price_map = {}
        if price_cpu is not None:
            price_map["cpu_hour"] = price_cpu
        if price_ram is not None:
            price_map["ram_gb_hour"] = price_ram

    cost = estimate_cost(snap, price_map)
    rprint(f"  Monthly estimate: [bold green]${cost.monthly_total_usd:,.2f}[/bold green]  Waste: {cost.waste_pct:.1f}%")

    # 5. Investigator — diagnose failures -----------------------------------
    rprint("[bold cyan]▶ Diagnosing pod failures…[/bold cyan]")
    diagnosis_findings = investigate(snap)
    if diagnosis_findings:
        rprint(f"  {len(diagnosis_findings)} failure(s) diagnosed")
        findings = findings + diagnosis_findings
        # Re-sort by severity
        sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        findings.sort(key=lambda f: sev_order.get(f.severity.value, 99))
    else:
        rprint("  No active pod failures detected")

    # 6. Explainer ----------------------------------------------------------
    rprint("[bold cyan]▶ Generating explanation…[/bold cyan]")
    facts = build_facts(findings, cost, meta)
    explainer_output = explain(facts)
    if debug:
        write_json(facts, Path(config.SNAPSHOT_DIR) / "facts.json")
        write_json(explainer_output.model_dump(), Path(config.SNAPSHOT_DIR) / "explainer.json")
    rprint(f"  Confidence: {explainer_output.confidence_overall.value}")

    # 7. Remediation --------------------------------------------------------
    findings = generate_remediations(findings)

    if apply or dry_run:
        rprint("[bold cyan]▶ Preparing remediations…[/bold cyan]")
        applied_findings: list[Finding] = []
        for f in findings[:5]:  # Top 5 only
            if not f.remediation.kubectl and not f.remediation.patch_yaml:
                continue
            if dry_run:
                rprint(f"  [DRY-RUN] {f.id}")
                for cmd in f.remediation.kubectl:
                    rprint(f"    $ {cmd}", style="dim")
            elif apply:
                if not yes:
                    confirmed = typer.confirm(
                        f"Apply remediation for {f.id}?", default=False,
                    )
                    if not confirmed:
                        continue
                result = apply_remediation(f, kubeconfig, apply=True, dry_run=False)
                if result["applied"]:
                    applied_findings.append(f)
                    rprint(f"  [bold green]✔ Applied: {f.id}[/bold green]")
                elif result["error"]:
                    rprint(f"  [bold red]✘ Failed: {f.id}: {result['error']}[/bold red]")

        # 8. Verifier (only if we actually applied something) ---------------
        if applied_findings:
            rprint("[bold cyan]▶ Verifying remediations…[/bold cyan]")
            verification = verify(kubeconfig, applied_findings)
            for v_result in verification:
                status_style = "green" if v_result["status"] == "pass" else "red"
                rprint(
                    f"  [{status_style}]{v_result['status'].upper()}[/{status_style}] "
                    f"{v_result['finding_id']}: {v_result['detail']}"
                )

    # 9. Generate report ----------------------------------------------------
    rprint("[bold cyan]▶ Generating report…[/bold cyan]")
    report_path = generate_report(
        cluster_meta=meta,
        findings=findings,
        cost=cost,
        explainer_output=explainer_output,
        out_path=output,
    )
    rprint(f"[bold green]✔ Report written to {report_path}[/bold green]")

    # Summary line
    risk = compute_risk_score(findings)
    _severity_counts: dict[str, int] = {}
    for f in findings:
        _severity_counts[f.severity.value] = _severity_counts.get(f.severity.value, 0) + 1
    summary_parts = [f"{v} {k}" for k, v in _severity_counts.items()]
    rprint(
        f"\n  Risk score: [bold]{risk}/100[/bold]  "
        f"Findings: {', '.join(summary_parts) or 'none'}"
    )


# ---------------------------------------------------------------------------
# snapshot — standalone snapshot command
# ---------------------------------------------------------------------------

@app.command()
def snapshot(
    kubeconfig: str = typer.Option(config.DEFAULT_KUBECONFIG, "--kubeconfig", "-k"),
    namespace: Optional[list[str]] = typer.Option(None, "--namespace", "-n"),
) -> None:
    """Take a cluster snapshot and save to snapshots/latest.json."""
    _setup_logging(debug=False)
    from app.tools.k8s_connector import save_snapshot, snapshot_cluster

    snap = snapshot_cluster(kubeconfig, namespace)
    path = save_snapshot(snap)
    rprint(f"[bold green]✔ Snapshot saved to {path}[/bold green]")


# ---------------------------------------------------------------------------
# diff — minimal snapshot comparison (Wave 3+)
# ---------------------------------------------------------------------------

@app.command()
def diff(
    before: str = typer.Argument(..., help="Path to the earlier snapshot JSON."),
    after: str = typer.Argument(..., help="Path to the later snapshot JSON."),
) -> None:
    """Compare two snapshots and report new / resolved findings."""
    _setup_logging(debug=False)
    from app.tools.utils import read_json
    from app.rules import run_all_rules

    snap_before = read_json(before)
    snap_after = read_json(after)

    findings_before = {f.id for f in run_all_rules(snap_before)}
    findings_after_list = run_all_rules(snap_after)
    findings_after = {f.id for f in findings_after_list}

    new_ids = findings_after - findings_before
    resolved_ids = findings_before - findings_after

    rprint(f"[bold cyan]New findings ({len(new_ids)}):[/bold cyan]")
    for f in findings_after_list:
        if f.id in new_ids:
            rprint(f"  + [{f.severity.value}] {f.summary}")

    rprint(f"\n[bold green]Resolved ({len(resolved_ids)}):[/bold green]")
    for fid in sorted(resolved_ids):
        rprint(f"  - {fid}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
