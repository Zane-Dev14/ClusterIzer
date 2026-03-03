"""Risk model - computes risk score and grade from signals."""
import logging

from .models import InfraState

logger = logging.getLogger(__name__)

SEVERITY_WEIGHTS = {"critical": 15, "high": 8, "medium": 3, "low": 1}

# Grade thresholds (inclusive)
GRADE_THRESHOLDS = [
    (90, "F"),
    (70, "D"),
    (50, "C"),
    (30, "B"),
    (0, "A"),
]


def compute_risk(state: InfraState) -> InfraState:
    """Compute risk score and grade from signals."""
    logger.info("Computing risk score...")
    signals = state.get("signals", [])
    total_score = sum(SEVERITY_WEIGHTS.get(signal.get("severity", "low"), 1) for signal in signals)
    score = min(100, total_score)
    grade = next((g for t, g in GRADE_THRESHOLDS if score >= t), "F")
    risk_score = {"score": score, "grade": grade, "signal_count": len(signals)}
    logger.info(f"Risk: {score}/100 (Grade: {grade}), {len(signals)} signals")
    state["risk_score"] = risk_score
    return state
