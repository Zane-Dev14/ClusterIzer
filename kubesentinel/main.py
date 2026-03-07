import json
import logging
import os
import re
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .runtime import run_engine
from .reporting import build_report
from .models import InfraState
from .cluster import scan_cluster
from .graph_builder import build_graph
from .simulation import simulate_node_failure

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# Silence noisy kubernetes client debug logs that flood terminals
# Keep global logging as configured, but raise the level for Kubernetes internals
for _name in ("kubernetes.client.rest", "kubernetes.client", "kubernetes", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)

app = typer.Typer(
    name="kubesentinel",
    help="KubeSentinel - Kubernetes Intelligence Engine",
    add_completion=False,
)

# Rich console for pretty output
console = Console()


@app.command()
def scan(
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Analysis query (default: 'Full cluster analysis')"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging (DEBUG level)"
    ),
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Kubernetes namespace to scan (default: all namespaces)",
    ),
    agents: Optional[str] = typer.Option(
        None,
        "--agents",
        help="Override planner: comma-separated list of agents (failure,cost,security)",
    ),
    git_repo: Optional[str] = typer.Option(
        None,
        "--git-repo",
        help="Desired state source (Git URL or local manifest path)",
    ),
    ci_mode: bool = typer.Option(
        False, "--ci", help="CI mode: exit 1 if grade >= D, minimal output"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output results as JSON (implies --ci)"
    ),
):
    if verbose and not json_output:
        logging.getLogger().setLevel(logging.DEBUG)
        # Enable verbose agent logging
        os.environ["KUBESENTINEL_VERBOSE_AGENTS"] = "1"
        logger.debug("Verbose logging enabled")
    elif json_output:
        logging.getLogger().setLevel(logging.ERROR)
        ci_mode = True

    query = query or "Full cluster analysis"
    if not json_output:
        console.print(
            Panel.fit(
                f"[bold cyan]KubeSentinel[/bold cyan]\nQuery: [yellow]{query}[/yellow]",
                border_style="cyan",
            )
        )

    try:
        if not json_output:
            console.print("\n🔍 [bold]Scanning cluster...[/bold]")

        # Parse agents override
        agents_list = None
        if agents:
            agents_list = [a.strip() for a in agents.split(",")]
            # Validate agents
            valid_agents = {"failure_agent", "cost_agent", "security_agent"}
            agents_list = [a for a in agents_list if a in valid_agents]
            if not agents_list:
                console.print(
                    "[yellow]Warning: No valid agents specified. Using planner.[/yellow]"
                )
                agents_list = None

        state = run_engine(
            query,
            namespace=namespace,
            agents=agents_list,
            git_repo=git_repo,
        )

        if not json_output:
            console.print("📝 [bold]Generating report...[/bold]")
        build_report(state)
        if json_output or ci_mode:
            sys.exit(_handle_ci_mode(state, json_output))
        _display_summary(state)
        console.print(
            "\n✅ [bold green]Complete![/bold green] Report: [cyan]report.md[/cyan]\n"
        )
        sys.exit(0)
    except RuntimeError as e:
        console.print(f"\n❌ [bold red]Error:[/bold red] {e}\n", style="red")
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n⚠️  [yellow]Interrupted[/yellow]\n")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n❌ [bold red]Unexpected:[/bold red] {e}\n", style="red")
        logger.error(f"Unexpected: {e}", exc_info=True)
        sys.exit(1)


def _display_summary(state: InfraState) -> None:
    """Display rich summary."""
    risk = state.get("risk_score", {})
    signals = state.get("signals", [])
    failure, cost, security = (
        state.get("failure_findings", []),
        state.get("cost_findings", []),
        state.get("security_findings", []),
    )
    score, grade = risk.get("score", 0), risk.get("grade", "N/A")
    grade_color = {
        "A": "green",
        "B": "blue",
        "C": "yellow",
        "D": "orange",
        "F": "red",
    }.get(grade, "white")
    console.print(
        f"\n⚠️  [bold]Risk:[/bold] {score}/100 ([{grade_color}]{grade}[/{grade_color}])"
    )
    table = Table(title="Summary", show_header=True, header_style="bold magenta")
    table.add_column("Category", style="cyan", width=20)
    table.add_column("Count", justify="right", style="yellow")
    for name, count in [
        ("Signals", len(signals)),
        ("Failure Findings", len(failure)),
        ("Cost Findings", len(cost)),
        ("Security Findings", len(security)),
    ]:
        table.add_row(name, str(count))
    console.print()
    console.print(table)


def _sanitize_json_text(text: str) -> str:
    """Remove non-printable control characters for JSON safety."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")


def _sanitize_for_json(value):
    """Recursively sanitize nested values for JSON output."""
    if isinstance(value, str):
        return _sanitize_json_text(value)
    if isinstance(value, list):
        return [_sanitize_for_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_for_json(item) for key, item in value.items()}
    return value


def _handle_ci_mode(state: InfraState, json_output: bool) -> int:
    """Handle CI mode execution."""
    risk = state.get("risk_score", {})
    grade, score, signals = (
        risk.get("grade", "F"),
        risk.get("score", 100),
        state.get("signals", []),
    )
    exit_code = 0 if grade in ["A", "B", "C"] else 1
    if json_output:
        result = _sanitize_for_json(
            {
                "metadata": {
                    "version": "0.1.0",
                    "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
                },
                "risk": risk,
                "findings": {
                    "reliability": state.get("failure_findings", []),
                    "cost": state.get("cost_findings", []),
                    "security": state.get("security_findings", []),
                },
                "signals": signals,
                "drift": state.get("_drift_analysis", {}),
                "summary": state.get("strategic_summary", ""),
                "status": {"exit_code": exit_code, "passed": exit_code == 0},
            }
        )
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
        sys.stdout.write("\n")
    else:
        status = "✅ PASSED" if exit_code == 0 else "❌ FAILED"
        console.print(f"\n{status} - Risk: {grade} ({score}/100)")
        console.print("Report: report.md\n")
    return exit_code


@app.command()
def version():
    """Show version information."""
    from . import __version__

    console.print(f"KubeSentinel version [cyan]{__version__}[/cyan]")


@app.command()
def simulate(
    action: str = typer.Argument(..., help="Simulation action (node-failure)"),
    node: Optional[str] = typer.Option(
        None, "--node", help="Node name to simulate failure for"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output results as JSON"),
):
    """Simulate infrastructure failure scenarios."""

    if action != "node-failure":
        print(
            f"[red]Error:[/red] Unknown simulation action '{action}'. Available: node-failure",
            file=sys.stderr,
        )
        sys.exit(1)

    if not node:
        print(
            "[red]Error:[/red] --node is required for node-failure simulation",
            file=sys.stderr,
        )
        sys.exit(1)

    # Suppress logging for JSON output
    if json_output:
        logging.getLogger().setLevel(logging.ERROR)

    try:
        if not json_output:
            console.print(
                Panel.fit(
                    f"[bold cyan]Node Failure Simulation[/bold cyan]\\nTarget: [yellow]{node}[/yellow]",
                    border_style="cyan",
                )
            )
            console.print("\\n🔍 [bold]Scanning cluster...[/bold]")

        # Scan cluster and build graph
        state: InfraState = {
            "user_query": "simulation",
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
            "planner_decision": [],
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "strategic_summary": "",
            "final_report": "",
        }

        state = scan_cluster(state)
        state = build_graph(state)

        if not json_output:
            console.print("🧪 [bold]Simulating node failure...[/bold]\\n")

        # Run simulation
        result = simulate_node_failure(
            state.get("cluster_snapshot", {}), state.get("graph_summary", {}), node
        )

        # Check for errors
        if "error" in result:
            if json_output:
                print(json.dumps(result, indent=2))
            else:
                console.print(f"[red]Error:[/red] {result['error']}")
                if "available_nodes" in result:
                    console.print("\\n[yellow]Available nodes:[/yellow]")
                    for n in result["available_nodes"]:
                        console.print(f"  • {n}")
            sys.exit(1)

        # Display results
        if json_output:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            _display_simulation_results(result)

        # Exit with appropriate code based on severity
        exit_code = 0 if result.get("impact_severity") in ["low", "medium"] else 1
        sys.exit(exit_code)

    except Exception as e:
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"\\n❌ [bold red]Error:[/bold red] {e}\\n", style="red")
        logger.error(f"Simulation error: {e}", exc_info=True)
        sys.exit(1)


def _display_simulation_results(result: dict) -> None:
    """Display simulation results in rich format."""
    # node = result.get("node")
    severity = result.get("impact_severity", "unknown")
    summary = result.get("summary", "")

    # Severity styling
    severity_color = {
        "critical": "red",
        "high": "orange",
        "medium": "yellow",
        "low": "green",
        "none": "blue",
    }.get(severity, "white")

    console.print(
        f"[bold]Impact Severity:[/bold] [{severity_color}]{severity.upper()}[/{severity_color}]\\n"
    )
    console.print(f"[dim]{summary}[/dim]\\n")

    # Affected pods
    pods = result.get("affected_pods", [])
    if pods:
        console.print(f"[bold cyan]Affected Pods ({len(pods)}):[/bold cyan]")
        for pod in pods[:10]:  # Limit display
            console.print(f"  • {pod.get('namespace')}/{pod.get('name')}")
        if len(pods) > 10:
            console.print(f"  [dim]... and {len(pods) - 10} more[/dim]")
        console.print()

    # Affected workloads
    workloads = result.get("affected_workloads", [])
    if workloads:
        table = Table(
            title="Affected Workloads", show_header=True, header_style="bold magenta"
        )
        table.add_column("Type", style="cyan")
        table.add_column("Name", style="white")
        table.add_column("Namespace", style="dim")
        table.add_column("Replicas", justify="right", style="yellow")
        table.add_column("Impact", style="white")

        for wl in workloads:
            impact = wl.get("impact", "unknown")
            impact_style = {
                "critical": "bold red",
                "high": "bold orange",
                "medium": "yellow",
                "low": "green",
            }.get(impact, "white")

            table.add_row(
                wl.get("type", "Unknown"),
                wl.get("name", "unknown"),
                wl.get("namespace", "default"),
                str(wl.get("replicas", "?")),
                f"[{impact_style}]{impact.upper()}[/{impact_style}]",
            )

        console.print(table)
        console.print()

    # Affected services
    services = result.get("affected_services", [])
    if services:
        console.print(f"[bold cyan]Affected Services ({len(services)}):[/bold cyan]")
        for svc in services:
            console.print(
                f"  • {svc.get('namespace')}/{svc.get('name')} → {svc.get('backend')}"
            )
        console.print()

    # Recommendations
    recommendations = result.get("recommendations", [])
    if recommendations:
        console.print("[bold green]Recommendations:[/bold green]")
        for rec in recommendations:
            console.print(f"  {rec}")
        console.print()


if __name__ == "__main__":
    app()
