import logging
from typing import Dict, Any, List

from .models import InfraState

logger = logging.getLogger(__name__)

# Base severity weights
SEVERITY_WEIGHTS = {"critical": 15, "high": 8, "medium": 3, "low": 1}

# Category multipliers (context-aware weighting)
CATEGORY_MULTIPLIERS = {
    "security": 2.0,  # Security issues double the weight
    "reliability": 1.8,  # Reliability issues are serious
    "cost": 0.5,  # Cost issues are lower priority in scoring
    "default": 1.0,
}

# Grade thresholds (non-saturating, adjusted ranges)
GRADE_THRESHOLDS = [
    (90, "F"),  # Critical: 90+
    (75, "D"),  # High: 75-89
    (55, "C"),  # Medium: 55-74
    (35, "B"),  # Moderate: 35-54
    (0, "A"),  # Low: 0-34
]

# Normalization factor - prevents medium signals from saturating score
# Uses adaptive divisor: prevents 30 medium signals from reaching 100/100
# while keeping 1 critical signal serious (~60/100)
NORMALIZATION_DIVISOR = 2.0


def _signal_title(signal_id: str, message: str) -> str:
    if signal_id and signal_id != "unknown":
        return signal_id.replace("_", " ").title()
    return message[:120]


def _first_fix(signal_id: str, category: str, resources: List[str] = None) -> str:
    """Generate kubectl command with actual resource names where possible."""
    # Extract example resource and namespace from provided resources
    # Resources are formatted as: "type/namespace/name" e.g., "pod/social-network/media-frontend-abc"
    # or "deployment/kube-system/coredns"
    
    example_resource = None
    resource_name = None
    namespace = "default"
    resource_type = None
    
    if resources:
        # Get first non-unknown resource
        for res in resources:
            if res and res != "unknown":
                example_resource = res
                break
    
    # Parse resource: format is "type/namespace/name"
    if example_resource and "/" in example_resource:
        parts = example_resource.split("/", 2)  # Split on first 2 "/" only
        if len(parts) == 3:
            resource_type = parts[0]  # e.g., "pod", "deployment"
            namespace = parts[1]      # e.g., "social-network", "kube-system"
            resource_name = parts[2]  # e.g., "media-frontend-abc", "coredns"
        elif len(parts) == 2:
            resource_type = parts[0]
            resource_name = parts[1]
            namespace = "default"
    
    # Build commands with actual resource names where available
    if signal_id == "crashloop_pod" and resource_name and namespace:
        return f"kubectl logs {resource_name} -n {namespace} --previous --tail=100"
    
    if signal_id == "replica_imbalance" and resource_name and namespace:
        return f"kubectl describe deployment {resource_name} -n {namespace}; kubectl get pods -n {namespace} -l app={resource_name}"
    
    # Generic commands (with namespace)
    fixes = {
        "pending_pod_unscheduled": f"kubectl get nodes; kubectl top nodes; kubectl get events -n {namespace} --sort-by='.lastTimestamp'",
        "pending_pods_namespace": f"kubectl get pods --field-selector=status.phase=Pending -n {namespace}",
        "node_not_ready": "kubectl get nodes -o wide; kubectl describe nodes",
        "memory_pressure": "kubectl top nodes; kubectl top pods -A | sort -k3 -rn | head -20",
        "disk_pressure": "kubectl debug node/<node-name> -it --image=ubuntu; df -h",
        "node_cpu_exhaustion": "kubectl top nodes; kubectl top pods -A | sort -k2 -rn | head -20",
        "node_allocation_pressure": "kubectl describe nodes",
        "broken_ownership_chain": "kubectl get all -A; kubectl get replicasets -A -o jsonpath='{.items[?(@.metadata.ownerReferences==null)].metadata.name}'",
        "latest_image_tag": "kubectl get pods -A -o jsonpath='{.items[*].spec.containers[*].image}' | tr -s ' ' '\\n' | grep ':latest'",
        "single_replica": f"kubectl get deployment -n {namespace} -o wide | awk '\\$2==1'",
    }
    
    if signal_id in fixes:
        return fixes[signal_id]
    if category == "security":
        return "kubectl get pods -A -o jsonpath='{.items[].spec.securityContext}'"
    if category == "cost":
        return "kubectl describe all -A | grep -i 'request\\|limit'"
    return "kubectl get events -A --sort-by='.lastTimestamp' | tail -20"


def _build_top_risks(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for signal in signals:
        signal_id = signal.get("signal_id", "unknown")
        category = signal.get("category", "default")
        severity = signal.get("severity", "low")
        message = signal.get("message", "")
        resource = signal.get("resource", "unknown")

        group_key = signal_id if signal_id != "unknown" else message
        if group_key not in grouped:
            grouped[group_key] = {
                "id": signal_id if signal_id != "unknown" else "derived_risk",
                "title": _signal_title(signal_id, message),
                "category": category,
                "severity": severity,
                "affected_count": 0,
                "resources": [],
                "max_weight": 0.0,
            }

        entry = grouped[group_key]
        entry["affected_count"] += 1
        if len(entry["resources"]) < 5 and resource not in entry["resources"]:
            entry["resources"].append(resource)

        base = SEVERITY_WEIGHTS.get(severity, 1)
        mult = CATEGORY_MULTIPLIERS.get(category, CATEGORY_MULTIPLIERS["default"])
        weight = base * mult
        entry["max_weight"] = max(entry["max_weight"], weight)
        if weight >= entry["max_weight"]:
            entry["severity"] = severity

    ranked = []
    for entry in grouped.values():
        impact = float(entry["max_weight"]) * float(entry["affected_count"])
        ranked.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "category": entry["category"],
                "severity": entry["severity"],
                "affected_count": entry["affected_count"],
                "impact_score": round(impact, 2),
                "resources": entry["resources"],
                "first_fix": _first_fix(entry["id"], entry["category"], entry["resources"]),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["impact_score"],
            SEVERITY_WEIGHTS.get(item["severity"], 0),
        ),
        reverse=True,
    )
    return ranked[:5]


def compute_risk(state: InfraState) -> InfraState:
    """Compute risk score and grade from signals with contextual weighting."""
    logger.info("Computing risk score...")
    signals = state.get("signals", [])
    drift_analysis = state.get("_drift_analysis", {})

    # Category-based score with context multipliers
    total_score = 0.0
    category_breakdown = {
        "security": 0.0,
        "reliability": 0.0,
        "cost": 0.0,
        "default": 0.0,
    }
    signal_contributions = []

    for signal in signals:
        severity = signal.get("severity", "low")
        category = signal.get("category", "default")

        # Base weight from severity
        base_weight = SEVERITY_WEIGHTS.get(severity, 1)

        # Apply category multiplier
        multiplier = CATEGORY_MULTIPLIERS.get(category, CATEGORY_MULTIPLIERS["default"])
        weighted_score = base_weight * multiplier

        total_score += weighted_score
        category_breakdown[category] = (
            category_breakdown.get(category, 0) + weighted_score
        )
        signal_contributions.append(
            {
                "signal_id": signal.get("signal_id", "unknown"),
                "resource": signal.get("resource", "unknown"),
                "category": category,
                "severity": severity,
                "base_weight": base_weight,
                "category_multiplier": multiplier,
                "contribution": round(weighted_score, 2),
            }
        )

    # Drift impact: critical resource losses add 20 points each
    drift_analysis = state.get("_drift_analysis") or {}
    drift_summary = drift_analysis.get("summary", {})
    if drift_summary:
        critical_lost = drift_summary.get("critical_lost_count", 0)
        critical_risky = drift_summary.get("critical_risky_count", 0)

        total_score += critical_lost * 20  # Pod loss is severe
        total_score += critical_risky * 10  # New risk is concerning

    # Normalize to 0-100 scale with severity-aware distribution
    # Key insight: Differentiate between many low-severity signals and few high-severity signals
    signal_count = len(signals)

    # Calculate severity distribution
    critical_count = sum(1 for s in signals if s.get("severity") == "critical")
    high_count = sum(1 for s in signals if s.get("severity") == "high")
    medium_count = sum(1 for s in signals if s.get("severity") == "medium")
    low_count = sum(1 for s in signals if s.get("severity") == "low")

    signal_severity_ratio = sum(
        1 for s in signals if s.get("severity") in ["critical", "high"]
    ) / max(signal_count, 1)

    if signal_count > 0:
        if signal_count <= 5:
            # For small counts, use raw weighted score
            score = min(100, int(total_score))
        else:
            # For larger counts, use severity-weighted distribution
            # Base score from average weighted severity
            mean_weight = total_score / max(signal_count, 1)

            # Severity component (weighted intensity)
            severity_component = mean_weight * 3.0

            # Volume component (acknowledge issue count, but cap impact)
            # This ensures excessive low-severity signals don't dominate score
            volume_factor = min(1.5, 1.0 + (signal_count - 5) / 100.0)
            volume_component = min(45.0, signal_count * 0.5)

            # Distribution modifier: reduce score if mostly low/medium
            # E.g., 130 signals with 20% high/critical → dampen more than 130 signals with 80% high/critical
            if signal_severity_ratio < 0.3:
                volume_component *= 0.6  # Mostly low/medium: dampen volume impact
            elif signal_severity_ratio < 0.6:
                volume_component *= 0.8  # Mixed: moderate dampening
            # else: >= 0.6 high/critical: use full volume impact

            normalized = int(severity_component + volume_component)
            score = min(100, normalized)

        logger.debug(
            f"Risk: count={signal_count}, crit={critical_count}, high={high_count}, "
            f"med={medium_count}, low={low_count}, ratio={signal_severity_ratio:.2f}, "
            f"total={total_score:.1f}, score={score}"
        )
    else:
        score = 0

    # Adjust grade based on drift severity if present
    drift_grade = drift_summary.get("drift_severity_grade", "A")
    drift_grade_to_points = {"F": -20, "D": -10, "C": 0, "B": 5, "A": 0}
    drift_adjustment = drift_grade_to_points.get(drift_grade, 0)
    score = max(0, min(100, score + drift_adjustment))

    # Determine grade
    grade = next((g for t, g in GRADE_THRESHOLDS if score >= t), "F")

    risk_score = {
        "score": score,
        "grade": grade,
        "signal_count": len(signals),
        "category_breakdown": category_breakdown,
        "drift_impact": {
            "critical_lost": drift_summary.get("critical_lost_count", 0),
            "critical_risky": drift_summary.get("critical_risky_count", 0),
            "drift_grade": drift_grade,
            "drift_adjustment": drift_adjustment,
        },
        "severity_ratio": round(signal_severity_ratio, 2),
        "confidence": "high" if signal_severity_ratio > 0.3 else "medium",
        "top_risks": _build_top_risks(signals),
        "explanation": {
            "raw_weighted_total": round(total_score, 2),
            "final_score": score,
            "applied_cap": 100 if total_score > 100 else None,
            "signal_contributions": signal_contributions[:200],
        },
    }

    logger.info(
        f"Risk: {score}/100 (Grade: {grade}), {len(signals)} signals, "
        f"drift: {drift_grade}, confidence: {risk_score['confidence']}"
    )
    state["risk_score"] = risk_score
    return state
