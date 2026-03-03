"""KubeSentinel CLI - main entry point."""
import json
import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .runtime import run_engine
from .reporting import build_report
from .models import InfraState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

app = typer.Typer(name="kubesentinel", help="KubeSentinel - Kubernetes Intelligence Engine", add_completion=False)

# Rich console for pretty output
console = Console()


@app.command()
def scan(
    query: Optional[str] = typer.Option(
        None,
        "--query",
        "-q",
        help="Analysis query (default: 'Full cluster analysis')"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging (DEBUG level)"
    ),
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Kubernetes namespace to scan (default: all namespaces)"
    ),
    ci_mode: bool = typer.Option(
        False,
        "--ci",
        help="CI mode: exit 1 if grade >= D, minimal output"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output results as JSON (implies --ci)"
    )
):
    """
    Scan and analyze Kubernetes cluster infrastructure.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    query = query or "Full cluster analysis"
    console.print(Panel.fit(f"[bold cyan]KubeSentinel[/bold cyan]\nQuery: [yellow]{query}[/yellow]", border_style="cyan"))
    
    try:
        # Run engine
        console.print("\n🔍 [bold]Scanning cluster...[/bold]")
        state = run_engine(query, namespace=namespace)
        
        # Build report
        console.print("📝 [bold]Generating report...[/bold]")
        build_report(state)
        if json_output or ci_mode:
            sys.exit(_handle_ci_mode(state, json_output))
        _display_summary(state)
        console.print("\n✅ [bold green]Complete![/bold green] Report: [cyan]report.md[/cyan]\n")
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
    failure, cost, security = state.get("failure_findings", []), state.get("cost_findings", []), state.get("security_findings", [])
    score, grade = risk.get("score", 0), risk.get("grade", "N/A")
    grade_color = {"A": "green", "B": "blue", "C": "yellow", "D": "orange", "F": "red"}.get(grade, "white")
    console.print(f"\n⚠️  [bold]Risk:[/bold] {score}/100 ([{grade_color}]{grade}[/{grade_color}])")
    table = Table(title="Summary", show_header=True, header_style="bold magenta")
    table.add_column("Category", style="cyan", width=20)
    table.add_column("Count", justify="right", style="yellow")
    for name, count in [("Signals", len(signals)), ("Failure Findings", len(failure)), ("Cost Findings", len(cost)), ("Security Findings", len(security))]:
        table.add_row(name, str(count))
    console.print()
    console.print(table)


def _handle_ci_mode(state: InfraState, json_output: bool) -> int:
    """Handle CI mode execution."""
    risk = state.get("risk_score", {})
    grade, score, signals = risk.get("grade", "F"), risk.get("score", 100), state.get("signals", [])
    exit_code = 0 if grade in ["A", "B", "C"] else 1
    if json_output:
        result = {"metadata": {"version": "0.1.0", "timestamp": __import__("datetime").datetime.utcnow().isoformat()}, "risk": {"grade": grade, "score": score, "total_signals": len(signals)}, "findings": {"reliability": state.get("failure_findings", []), "cost": state.get("cost_findings", []), "security": state.get("security_findings", [])}, "summary": state.get("strategic_summary", ""), "status": {"exit_code": exit_code, "passed": exit_code == 0}}
        console.print(json.dumps(result, indent=2))
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


if __name__ == "__main__":
    app()
