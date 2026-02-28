


"""Graph Builder Agent — constructs a dependency graph from cluster snapshot.

Nodes:  ``deployment:{ns}/{name}``, ``pod:{ns}/{name}``, ``node:{name}``,
        ``service:{ns}/{name}``
Edges:  deployment → pod, pod → node, service → deployment (by label selector).

Usage::

    from app.agents.graph_builder import build_graph, find_single_points_of_failure
    G = build_graph(snapshot)
    spofs = find_single_points_of_failure(G)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx

from app.tools.utils import write_json

logger = logging.getLogger("clustergpt.graph")


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(snapshot: dict[str, Any]) -> nx.DiGraph:
    """Build a directed dependency graph from a cluster snapshot.

    Parameters
    ----------
    snapshot:
        Cluster snapshot dict (from :func:`k8s_connector.snapshot_cluster`).

    Returns
    -------
    nx.DiGraph
        A NetworkX directed graph with typed node IDs.
    """
    G = nx.DiGraph()

    # --- Nodes (infrastructure) ---
    for node in snapshot.get("nodes", []):
        node_name = node.get("metadata", {}).get("name", "unknown")
        G.add_node(f"node:{node_name}", kind="node", data={"name": node_name})

    # --- Deployments ---
    deploy_labels: dict[str, dict] = {}  # deploy_id -> matchLabels
    for dep in snapshot.get("deployments", []):
        meta = dep.get("metadata", {})
        ns = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        dep_id = f"deployment:{ns}/{name}"
        G.add_node(dep_id, kind="deployment", namespace=ns, data={"name": name})

        # Store selector labels for service → deployment matching later.
        match_labels = (
            dep.get("spec", {})
            .get("selector", {})
            .get("matchLabels", {})
        )
        deploy_labels[dep_id] = match_labels

    # --- Pods → link to deployment + node ---
    for pod in snapshot.get("pods", []):
        meta = pod.get("metadata", {})
        ns = meta.get("namespace", "default")
        pod_name = meta.get("name", "unknown")
        pod_id = f"pod:{ns}/{pod_name}"
        node_name = pod.get("spec", {}).get("nodeName", "")

        G.add_node(pod_id, kind="pod", namespace=ns, data={"name": pod_name})

        # pod → node
        if node_name:
            node_id = f"node:{node_name}"
            if node_id not in G:
                G.add_node(node_id, kind="node", data={"name": node_name})
            G.add_edge(pod_id, node_id, relation="runs-on")

        # deployment → pod (match via ownerReferences)
        for owner in meta.get("ownerReferences", []):
            if owner.get("kind") == "ReplicaSet":
                # Try to find the deployment that owns this RS.
                rs_name: str = owner.get("name", "")
                # Deployment name is usually the RS name minus the trailing hash.
                dep_name_guess = "-".join(rs_name.rsplit("-", 1)[:-1]) if "-" in rs_name else rs_name
                dep_id = f"deployment:{ns}/{dep_name_guess}"
                if dep_id in G:
                    G.add_edge(dep_id, pod_id, relation="owns")

    # --- Services → deployments (via selector match) ---
    for svc in snapshot.get("services", []):
        meta = svc.get("metadata", {})
        ns = meta.get("namespace", "default")
        svc_name = meta.get("name", "unknown")
        svc_id = f"service:{ns}/{svc_name}"
        selector = svc.get("spec", {}).get("selector", {})
        G.add_node(svc_id, kind="service", namespace=ns, data={"name": svc_name})

        if selector:
            for dep_id, dep_labels in deploy_labels.items():
                if dep_labels and all(
                    selector.get(k) == v for k, v in dep_labels.items()
                ):
                    # Only match if the deployment is in the same namespace.
                    dep_ns = G.nodes[dep_id].get("namespace")
                    if dep_ns == ns:
                        G.add_edge(svc_id, dep_id, relation="routes-to")

    return G


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def find_single_points_of_failure(graph: nx.DiGraph) -> list[str]:
    """Return deployment IDs that have exactly one pod (no HA).

    This complements the ``single_replica`` rule by using graph structure
    rather than manifest spec.
    """
    spofs: list[str] = []
    for node_id, data in graph.nodes(data=True):
        if data.get("kind") != "deployment":
            continue
        pod_successors = [
            s for s in graph.successors(node_id)
            if graph.nodes[s].get("kind") == "pod"
        ]
        if len(pod_successors) <= 1:
            spofs.append(node_id)
    return spofs


def graph_summary(graph: nx.DiGraph) -> dict[str, Any]:
    """Return a plain-dict summary of the graph for debugging / reports."""
    kind_counts: dict[str, int] = {}
    for _, data in graph.nodes(data=True):
        kind = data.get("kind", "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    return {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
        "kind_counts": kind_counts,
        "single_points_of_failure": find_single_points_of_failure(graph),
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_graph(graph: nx.DiGraph, path: str | Path = "snapshots/graph.json") -> Path:
    """Serialise graph to JSON via ``node_link_data``."""
    data = nx.node_link_data(graph)
    return write_json(data, path)


def load_graph(path: str | Path = "snapshots/graph.json") -> nx.DiGraph:
    """Load graph from JSON file."""
    with open(path) as fh:
        data = json.load(fh)
    return nx.node_link_graph(data, directed=True)
