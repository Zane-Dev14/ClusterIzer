"""Cost Analyst — estimates monthly spend from resource requests × replicas.

Uses a simple unit-price model (no cloud billing API).  Users can override
prices via CLI flags or environment variables.

Usage (standalone)::

    python -m app.tools.cost_model --snapshot snapshots/latest.json
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Optional

from app import config
from app.models import CostEstimate, DeploymentCost

logger = logging.getLogger("clustergpt.cost")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_cpu(value: str | int | float | None) -> float:
    """Convert a K8s CPU quantity (e.g. ``'500m'``, ``'2'``) to cores."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    return float(s)


def _parse_memory_gb(value: str | int | float | None) -> float:
    """Convert a K8s memory quantity to gigabytes."""
    if value is None:
        return 0.0
    s = str(value).strip()
    multipliers = {
        "Ki": 1 / (1024 ** 2),
        "Mi": 1 / 1024,
        "Gi": 1.0,
        "Ti": 1024.0,
        "K": 1e3 / 1e9,
        "M": 1e6 / 1e9,
        "G": 1.0,
        "T": 1e3,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    # Plain bytes
    try:
        return float(s) / (1024 ** 3)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# Main estimation
# ---------------------------------------------------------------------------

def estimate_cost(
    snapshot: dict[str, Any],
    price_map: Optional[dict[str, float]] = None,
) -> CostEstimate:
    """Compute per-deployment and total estimated monthly cost.

    Parameters
    ----------
    snapshot:
        Cluster snapshot dict (must contain ``deployments`` and ``nodes``).
    price_map:
        Optional overrides: ``{"cpu_hour": float, "ram_gb_hour": float}``.
    """
    cpu_hr = (price_map or {}).get("cpu_hour", config.CPU_HOUR_COST)
    ram_hr = (price_map or {}).get("ram_gb_hour", config.RAM_GB_HOUR_COST)

    dep_costs: list[DeploymentCost] = []
    ns_totals: dict[str, float] = {}

    for dep in snapshot.get("deployments", []):
        meta = dep.get("metadata", {})
        ns = meta.get("namespace", "default")
        name = meta.get("name", "unknown")
        replicas = dep.get("spec", {}).get("replicas", 1) or 1

        # Sum requests across all containers in the pod template.
        total_cpu = 0.0
        total_mem_gb = 0.0
        containers = (
            dep.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for ctr in containers:
            reqs = ctr.get("resources", {}).get("requests", {})
            total_cpu += _parse_cpu(reqs.get("cpu"))
            total_mem_gb += _parse_memory_gb(reqs.get("memory"))

        monthly = (
            replicas
            * (total_cpu * cpu_hr + total_mem_gb * ram_hr)
            * config.HOURS_PER_MONTH
        )

        dep_costs.append(DeploymentCost(
            namespace=ns,
            name=name,
            replicas=replicas,
            cpu_requests=round(total_cpu, 4),
            mem_requests_gb=round(total_mem_gb, 4),
            monthly_cost_usd=round(monthly, 2),
        ))
        ns_totals[ns] = round(ns_totals.get(ns, 0.0) + monthly, 2)

    total_monthly = round(sum(d.monthly_cost_usd for d in dep_costs), 2)

    # Waste estimate: compare total requested vs total node allocatable.
    waste_pct = _compute_waste(snapshot, dep_costs)

    return CostEstimate(
        deployments=dep_costs,
        namespace_totals=ns_totals,
        monthly_total_usd=total_monthly,
        waste_pct=waste_pct,
    )


def _compute_waste(
    snapshot: dict[str, Any],
    dep_costs: list[DeploymentCost],
) -> float:
    """Return waste % = 1 − (total requested / total allocatable).

    Conservative: if node data is missing, returns 0.
    """
    total_alloc_cpu = 0.0
    total_alloc_mem_gb = 0.0
    for node in snapshot.get("nodes", []):
        alloc = node.get("status", {}).get("allocatable", {})
        total_alloc_cpu += _parse_cpu(alloc.get("cpu"))
        total_alloc_mem_gb += _parse_memory_gb(alloc.get("memory"))

    if total_alloc_cpu == 0 and total_alloc_mem_gb == 0:
        return 0.0

    total_req_cpu = sum(d.cpu_requests * d.replicas for d in dep_costs)
    total_req_mem = sum(d.mem_requests_gb * d.replicas for d in dep_costs)

    # Use CPU utilisation as primary signal.
    if total_alloc_cpu > 0:
        used_pct = total_req_cpu / total_alloc_cpu
        return round(max(0.0, (1 - used_pct)) * 100, 1)

    if total_alloc_mem_gb > 0:
        used_pct = total_req_mem / total_alloc_mem_gb
        return round(max(0.0, (1 - used_pct)) * 100, 1)

    return 0.0


# ---------------------------------------------------------------------------
# CLI entrypoint for standalone testing
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="Estimate cluster cost.")
    parser.add_argument("--snapshot", required=True, help="Path to snapshot JSON.")
    args = parser.parse_args()

    with open(args.snapshot) as fh:
        snap = json.load(fh)
    cost = estimate_cost(snap)
    print(json.dumps(cost.model_dump(), indent=2))


if __name__ == "__main__":
    _cli()
