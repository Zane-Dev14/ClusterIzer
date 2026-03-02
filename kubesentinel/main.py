"""
KubeSentinel CLI - main entry point.

Typer-based CLI for running Kubernetes intelligence analysis.
Single command: scan
"""

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

# Create Typer app
app = typer.Typer(
    name="kubesentinel",
    help="KubeSentinel - Kubernetes Intelligence Engine",
    add_completion=False
)

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
    
    Connects to the configured cluster (via kubeconfig or in-cluster),
    extracts bounded state, generates signals, runs DeepAgent analysis,
    and produces a comprehensive markdown report.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")
    
    # Default query
    if not query:
        query = "Full cluster analysis"
    
    console.print(Panel.fit(
        f"[bold cyan]KubeSentinel - Kubernetes Intelligence Engine[/bold cyan]\n"
        f"Query: [yellow]{query}[/yellow]",
        border_style="cyan"
    ))
    
    try:
        # Run engine
        console.print("\n🔍 [bold]Scanning cluster...[/bold]")
        state = run_engine(query, namespace=namespace)
        
        # Build report
        console.print("📝 [bold]Generating report...[/bold]")
        build_report(state)
        
        # Handle CI/JSON mode
        if json_output or ci_mode:
            exit_code = _handle_ci_mode(state, json_output)
            sys.exit(exit_code)
        
        # Display summary
        _display_summary(state)
        
        console.print(
            "\n✅ [bold green]Analysis complete![/bold green] "
            "Report written to [cyan]report.md[/cyan]\n"
        )
        
        sys.exit(0)
        
    except RuntimeError as e:
        console.print(f"\n❌ [bold red]Error:[/bold red] {e}\n", style="red")
        logger.error(f"Execution failed: {e}", exc_info=True)
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n⚠️  [yellow]Interrupted by user[/yellow]\n")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n❌ [bold red]Unexpected error:[/bold red] {e}\n", style="red")
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


def _display_summary(state: InfraState) -> None:
    """Display rich summary table."""
    risk = state.get("risk_score", {})
    signals = state.get("signals", [])
    failure_findings = state.get("failure_findings", [])
    cost_findings = state.get("cost_findings", [])
    security_findings = state.get("security_findings", [])
    
    # Risk score panel
    score = risk.get("score", 0)
    grade = risk.get("grade", "N/A")
    
    grade_color = {
        "A": "green",
        "B": "blue",
        "C": "yellow",
        "D": "orange",
        "F": "red"
    }.get(grade, "white")
    
    console.print(f"\n⚠️  [bold]Risk Score:[/bold] {score}/100 (Grade: [{grade_color}]{grade}[/{grade_color}])")
    
    # Findings table
    table = Table(title="Analysis Summary", show_header=True, header_style="bold magenta")
    table.add_column("Category", style="cyan", width=20)
    table.add_column("Count", justify="right", style="yellow")
    
    table.add_row("Signals", str(len(signals)))
    table.add_row("Failure Findings", str(len(failure_findings)))
    table.add_row("Cost Findings", str(len(cost_findings)))
    table.add_row("Security Findings", str(len(security_findings)))
    
    console.print()
    console.print(table)


def _handle_ci_mode(state: InfraState, json_output: bool) -> int:
    """Handle CI mode execution.
    
    Returns:
        Exit code: 0 if grade < D, 1 if grade >= D
    """
    risk = state.get("risk_score", {})
    grade = risk.get("grade", "F")
    score = risk.get("score", 100)
    signals = state.get("signals", [])
    
    # Determine exit code based on grade
    exit_code = 0 if grade in ["A", "B", "C"] else 1
    
    if json_output:
        # Clean JSON output with full findings
        result = {
            "metadata": {
                "version": "0.1.0",
                "timestamp": __import__("datetime").datetime.utcnow().isoformat()
            },
            "risk": {
                "grade": grade,
                "score": score,
                "total_signals": len(signals)
            },
            "findings": {
                "reliability": state.get("failure_findings", []),
                "cost": state.get("cost_findings", []),
                "security": state.get("security_findings", [])
            },
            "summary": state.get("strategic_summary", ""),
            "status": {
                "exit_code": exit_code,
                "passed": exit_code == 0
            }
        }
        console.print(json.dumps(result, indent=2))
    else:
        # Minimal CI output
        status = "✅ PASSED" if exit_code == 0 else "❌ FAILED"
        console.print(f"\n{status} - Risk Grade: {grade} (Score: {score}/100)")
        console.print("Report: report.md\n")
    
    return exit_code


@app.command()
def version():
    """Show version information."""
    from . import __version__
    console.print(f"KubeSentinel version [cyan]{__version__}[/cyan]")


if __name__ == "__main__":
    app()
