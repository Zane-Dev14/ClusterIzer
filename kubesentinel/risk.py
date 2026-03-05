import logging
from typing import Dict, Any

from .models import InfraState

logger = logging.getLogger(__name__)

# Base severity weights
SEVERITY_WEIGHTS = {"critical": 15, "high": 8, "medium": 3, "low": 1}

# Category multipliers (context-aware weighting)
CATEGORY_MULTIPLIERS = {
    "security": 2.0,       # Security issues double the weight
    "reliability": 1.8,    # Reliability issues are serious
    "cost": 0.5,           # Cost issues are lower priority in scoring
    "default": 1.0
}

# Grade thresholds (inclusive)
GRADE_THRESHOLDS = [
    (90, "F"),
    (70, "D"),
    (50, "C"),
    (30, "B"),
    (0, "A"),
]

def compute_risk(state: InfraState) -> InfraState:
    """Compute risk score and grade from signals with contextual weighting."""
    logger.info("Computing risk score...")
    signals = state.get("signals", [])
    drift_analysis = state.get("_drift_analysis", {})
    
    # Category-based score with context multipliers
    total_score = 0
    category_breakdown = {"security": 0, "reliability": 0, "cost": 0, "default": 0}
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
        category_breakdown[category] = category_breakdown.get(category, 0) + weighted_score
        signal_contributions.append({
            "signal_id": signal.get("signal_id", "unknown"),
            "resource": signal.get("resource", "unknown"),
            "category": category,
            "severity": severity,
            "base_weight": base_weight,
            "category_multiplier": multiplier,
            "contribution": round(weighted_score, 2),
        })
    
    # Drift impact: critical resource losses add 20 points each
    drift_summary = drift_analysis.get("summary", {})
    if drift_summary:
        critical_lost = drift_summary.get("critical_lost_count", 0)
        critical_risky = drift_summary.get("critical_risky_count", 0)
        
        total_score += critical_lost * 20      # Pod loss is severe
        total_score += critical_risky * 10     # New risk is concerning
    
    # Normalize to 0-100 scale
    score = min(100, int(total_score))
    
    # Adjust grade based on drift severity if present
    drift_grade = drift_summary.get("drift_severity_grade", "A")
    drift_grade_to_points = {"F": -20, "D": -10, "C": 0, "B": 5, "A": 0}
    drift_adjustment = drift_grade_to_points.get(drift_grade, 0)
    score = max(0, min(100, score + drift_adjustment))
    
    # Determine grade
    grade = next((g for t, g in GRADE_THRESHOLDS if score >= t), "F")
    
    # Detect false positives: high signal count but low severity
    signal_severity_ratio = sum(1 for s in signals if s.get("severity") in ["critical", "high"]) / max(len(signals), 1)
    
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
        "explanation": {
            "raw_weighted_total": round(total_score, 2),
            "final_score": score,
            "applied_cap": 100 if total_score > 100 else None,
            "signal_contributions": signal_contributions[:200],
        },
    }
    
    logger.info(f"Risk: {score}/100 (Grade: {grade}), {len(signals)} signals, "
                f"drift: {drift_grade}, confidence: {risk_score['confidence']}")
    state["risk_score"] = risk_score
    return state