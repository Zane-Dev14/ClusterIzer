"""
Risk model node - computes risk score and grade from signals.

Pure function that assigns weights to signals by severity,
sums them, and maps to a letter grade. Deterministic, no LLM.
"""

import logging

from .models import InfraState

logger = logging.getLogger(__name__)

# Severity weights
SEVERITY_WEIGHTS = {
    "critical": 15,
    "high": 8,
    "medium": 3,
    "low": 1,
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
    """
    Compute risk score and grade from signals.
    
    Score = min(100, sum(severity_weight for each signal))
    
    Grade mapping:
    - 90-100: F (critical)
    - 70-89:  D (high risk)
    - 50-69:  C (medium risk)
    - 30-49:  B (low risk)
    - 0-29:   A (minimal risk)
    
    Args:
        state: InfraState with signals populated
        
    Returns:
        Updated state with risk_score populated
    """
    logger.info("Computing risk score...")
    
    signals = state["signals"]
    
    # Sum severity weights
    total_score = 0
    for signal in signals:
        severity = signal.get("severity", "low")
        weight = SEVERITY_WEIGHTS.get(severity, 1)
        total_score += weight
    
    # Cap at 100
    score = min(100, total_score)
    
    # Determine grade
    grade = "F"
    for threshold, grade_letter in GRADE_THRESHOLDS:
        if score >= threshold:
            grade = grade_letter
            break
    
    risk_score = {
        "score": score,
        "grade": grade,
        "signal_count": len(signals),
    }
    
    logger.info(f"Risk score: {score}/100 (Grade: {grade}), {len(signals)} signals")
    
    state["risk_score"] = risk_score
    return state
