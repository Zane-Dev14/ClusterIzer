"""KubeSentinel CLI - Infrastructure Intelligence Engine."""

import json
import logging
import sys
from typing import Optional

import typer

from .models import InfraState
from .cluster import scan_cluster
from .graph_builder import build_graph
from .signals import generate_signals
from .risk import compute_risk
from .reporting import build_report

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer(
    name="kubesentinel",
    help="Infrastructure Intelligence Engine for Kubernetes clusters",
    no_args_is_help=True,
)


@app.command()
def scan(
    namespace: Optional[str] = typer.Option(
        None,
        "--namespace",
        "-n",
        help="Kubernetes namespace to scan (default: all namespaces)",
    ),
    output_format: Optional[str] = typer.Option(
        "markdown",
        "--output",
        "-o",
        help="Output format: markdown, json, or summary",
    ),
    query: Optional[str] = typer.Option(
        "analyze cluster health",
        "--query",
        "-q",
        help="Analysis query for LLM agents",
    ),
) -> None:
    """
    Scan a Kubernetes cluster for infrastructure issues.

    Examples:
        kubesentinel scan                          # Scan all namespaces
        kubesentinel scan -n default               # Scan specific namespace
        kubesentinel scan -o json                  # Output as JSON
        kubesentinel scan --query "security audit" # Custom analysis query
    """
    try:
        logger.info(
            f"Scanning cluster{f' (namespace: {namespace})' if namespace else ' (all namespaces)'}"
        )

        # Initialize analysis state
        state: InfraState = {
            "user_query": query or "analyze cluster health",
            "target_namespace": namespace,
            "cluster_snapshot": {},
            "graph_summary": {},
            "signals": [],
            "risk_score": {},
        }

        # Run analysis pipeline
        logger.info("Connecting to Kubernetes cluster...")
        state = scan_cluster(state)

        logger.info("Building dependency graph...")
        state = build_graph(state)

        logger.info("Generating signals...")
        state = generate_signals(state)

        logger.info("Computing risk score...")
        state = compute_risk(state)

        logger.info("Generating report...")
        report = build_report(state)

        # Output results
        risk = state.get("risk_score", {})

        if output_format == "json":
            # Output JSON format
            output_data = {
                "query": state["user_query"],
                "risk_score": risk,
                "signals_count": len(state.get("signals", [])),
                "cluster_summary": {
                    "nodes": len(state["cluster_snapshot"].get("nodes", [])),
                    "deployments": len(
                        state["cluster_snapshot"].get("deployments", [])
                    ),
                    "pods": len(state["cluster_snapshot"].get("pods", [])),
                    "services": len(state["cluster_snapshot"].get("services", [])),
                },
            }
            typer.echo(json.dumps(output_data, indent=2))
        elif output_format == "summary":
            # Output summary format
            typer.echo("\n📊 Cluster Health Summary")
            typer.echo(f"Grade: {risk.get('grade', 'N/A')}")
            typer.echo(f"Risk Score: {risk.get('score', 'N/A')}/100")
            typer.echo(f"Signals Detected: {len(state.get('signals', []))}")
            typer.echo(f"Nodes: {len(state['cluster_snapshot'].get('nodes', []))}")
            typer.echo(
                f"Deployments: {len(state['cluster_snapshot'].get('deployments', []))}"
            )
            typer.echo(f"Pods: {len(state['cluster_snapshot'].get('pods', []))}")
        else:
            # Default: markdown format
            typer.echo(report)

        # Exit with status code based on grade
        if risk.get("grade") in ["D", "F"]:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Scan failed: {e}", exc_info=True)
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)


@app.command()
def version() -> None:
    """Show KubeSentinel version."""
    from . import __version__

    typer.echo(f"KubeSentinel v{__version__}")


@app.command()
def health() -> None:
    """Check Kubernetes cluster health and connectivity."""
    try:
        from kubernetes import client, config

        # Load Kubernetes config
        try:
            config.load_incluster_config()
        except config.config_exception.ConfigException:
            config.load_kube_config()

        # Try to connect to cluster
        v1 = client.CoreV1Api()
        nodes = v1.list_node()

        typer.echo("✓ Connected to Kubernetes cluster")
        typer.echo(f"  Nodes: {len(nodes.items)}")

        for node in nodes.items:
            status = (
                "Ready"
                if any(
                    c.status == "True"
                    for c in node.status.conditions
                    if c.type == "Ready"
                )
                else "Not Ready"
            )
            typer.echo(f"    - {node.metadata.name}: {status}")

    except Exception as e:
        typer.echo(f"✗ Failed to connect to Kubernetes cluster: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    app()
