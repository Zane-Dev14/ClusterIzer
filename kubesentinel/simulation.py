"""
Node failure simulation for KubeSentinel.

Simulates the impact of node failures on cluster workloads.
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def simulate_node_failure(
    cluster_snapshot: Dict[str, Any], graph_summary: Dict[str, Any], node_name: str
) -> Dict[str, Any]:
    """
    Simulate the impact of a node failure on cluster workloads.

    Args:
        cluster_snapshot: Cluster state with nodes, pods, deployments
        graph_summary: Dependency graph with ownership mappings
        node_name: Name of the node to simulate failure for

    Returns:
        Dictionary with simulation results:
        - node: Node name
        - affected_pods: List of pods on the node
        - affected_deployments: List of impacted deployments with severity
        - affected_services: List of impacted services
        - impact_severity: Overall impact level (critical/high/medium/low)
        - summary: Human-readable summary
    """
    logger.info(f"Simulating failure of node: {node_name}")

    nodes = cluster_snapshot.get("nodes", [])
    pods = cluster_snapshot.get("pods", [])
    deployments = cluster_snapshot.get("deployments", [])
    statefulsets = cluster_snapshot.get("statefulsets", [])
    services = cluster_snapshot.get("services", [])

    # Verify node exists
    node = next((n for n in nodes if n.get("name") == node_name), None)
    if not node:
        return {
            "error": f"Node '{node_name}' not found in cluster",
            "available_nodes": [n.get("name") for n in nodes],
        }

    # Find all pods scheduled on this node
    affected_pods = [p for p in pods if p.get("node_name") == node_name]

    if not affected_pods:
        return {
            "node": node_name,
            "affected_pods": [],
            "affected_deployments": [],
            "affected_services": [],
            "impact_severity": "none",
            "summary": f"Node {node_name} has no scheduled pods - failure would have no impact",
        }

    # Resolve ownership: pod → deployment/statefulset
    ownership_index = graph_summary.get("ownership_index", {})
    # deployment_to_pods = graph_summary.get("deployment_to_pods", {})

    affected_workloads = []
    critical_workloads = []
    degraded_workloads = []

    # Analyze impact per workload
    for pod in affected_pods:
        pod_id = f"{pod.get('namespace')}/{pod.get('name')}"
        owner_info = ownership_index.get(pod_id, {})

        if not owner_info:
            # Orphan pod - critical loss
            affected_workloads.append(
                {
                    "type": "pod",
                    "name": pod.get("name"),
                    "namespace": pod.get("namespace"),
                    "impact": "critical",
                    "reason": "Orphan pod (no controller) - will not be rescheduled",
                }
            )
            critical_workloads.append(pod.get("name"))
            continue

        owner_kind = owner_info.get("owner_kind", "Deployment")
        owner_name = owner_info.get("owner_name", "unknown")

        # Find the owning workload
        workload = None
        if owner_kind == "Deployment":
            workload = next(
                (
                    d
                    for d in deployments
                    if d.get("name") == owner_name
                    and d.get("namespace") == pod.get("namespace")
                ),
                None,
            )
        elif owner_kind == "StatefulSet":
            workload = next(
                (
                    s
                    for s in statefulsets
                    if s.get("name") == owner_name
                    and s.get("namespace") == pod.get("namespace")
                ),
                None,
            )

        if workload:
            replicas = workload.get("replicas", 1)
            # workload_id = f"{workload.get('namespace')}/{owner_name}"

            # Check if already analyzed
            if any(w.get("name") == owner_name for w in affected_workloads):
                continue

            # Determine impact based on replica count
            if replicas == 1:
                affected_workloads.append(
                    {
                        "type": owner_kind,
                        "name": owner_name,
                        "namespace": workload.get("namespace"),
                        "replicas": replicas,
                        "impact": "critical",
                        "reason": "Single replica - service outage expected",
                    }
                )
                critical_workloads.append(owner_name)
            elif replicas == 2:
                affected_workloads.append(
                    {
                        "type": owner_kind,
                        "name": owner_name,
                        "namespace": workload.get("namespace"),
                        "replicas": replicas,
                        "impact": "high",
                        "reason": "2 replicas - significant degradation (50% capacity loss)",
                    }
                )
                degraded_workloads.append(owner_name)
            else:
                affected_workloads.append(
                    {
                        "type": owner_kind,
                        "name": owner_name,
                        "namespace": workload.get("namespace"),
                        "replicas": replicas,
                        "impact": "medium",
                        "reason": f"{replicas} replicas - partial degradation",
                    }
                )
                degraded_workloads.append(owner_name)

    # Find affected services
    service_to_deployment = graph_summary.get("service_to_deployment", {})
    affected_services = []
    for svc in services:
        svc_id = f"{svc.get('namespace')}/{svc.get('name')}"
        target_deployment = service_to_deployment.get(svc_id)

        if target_deployment:
            # Check if any affected workload matches
            for workload in affected_workloads:
                if (
                    f"{workload.get('namespace')}/{workload.get('name')}"
                    == target_deployment
                ):
                    affected_services.append(
                        {
                            "name": svc.get("name"),
                            "namespace": svc.get("namespace"),
                            "backend": workload.get("name"),
                            "impact": workload.get("impact"),
                        }
                    )
                    break

    # Determine overall impact severity
    if critical_workloads:
        impact_severity = "critical"
    elif len(degraded_workloads) > 3:
        impact_severity = "high"
    elif degraded_workloads:
        impact_severity = "medium"
    else:
        impact_severity = "low"

    # Generate summary
    summary_parts = [
        f"Node {node_name} failure would affect {len(affected_pods)} pods across {len(affected_workloads)} workloads.",
    ]

    if critical_workloads:
        summary_parts.append(
            f"⚠️  CRITICAL: {len(critical_workloads)} workloads would experience service outage (single replica)."
        )

    if degraded_workloads:
        summary_parts.append(
            f"⚠️  DEGRADED: {len(degraded_workloads)} workloads would experience performance degradation."
        )

    if affected_services:
        summary_parts.append(f"📡 {len(affected_services)} services would be impacted.")

    summary = " ".join(summary_parts)

    return {
        "node": node_name,
        "affected_pods": [
            {"name": p.get("name"), "namespace": p.get("namespace")}
            for p in affected_pods
        ],
        "affected_workloads": affected_workloads,
        "affected_services": affected_services,
        "impact_severity": impact_severity,
        "summary": summary,
        "recommendations": _generate_recommendations(
            affected_workloads, critical_workloads
        ),
    }


def _generate_recommendations(
    workloads: List[Dict[str, Any]], critical: List[str]
) -> List[str]:
    """Generate recommendations based on simulation results."""
    recommendations = []

    if critical:
        recommendations.append(
            f"⚠️  URGENT: Increase replica count to 3+ for {len(critical)} critical workloads to enable high availability"
        )

    single_node_deps = [w for w in workloads if w.get("impact") in ["critical", "high"]]
    if len(single_node_deps) > 2:
        recommendations.append(
            "⚠️  Consider implementing pod anti-affinity rules to distribute replicas across multiple nodes"
        )

    if len(workloads) > 5:
        recommendations.append(
            "💡 High pod density on this node - consider rebalancing workloads for better fault tolerance"
        )

    recommendations.append(
        "✅ Enable PodDisruptionBudgets (PDB) to prevent simultaneous disruption of multiple replicas"
    )

    return recommendations
