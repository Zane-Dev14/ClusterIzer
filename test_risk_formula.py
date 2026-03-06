#!/usr/bin/env python3
"""Quick verification of new risk normalization formula."""


def test_formula():
    """Test the new risk formula."""
    # Weights and multipliers
    SEVERITY_WEIGHTS = {"critical": 15, "high": 8, "medium": 3, "low": 1}
    CATEGORY_MULTIPLIERS = {
        "security": 2.0,
        "reliability": 1.8,
        "cost": 0.5,
        "default": 1.0,
    }

    def compute_score(signal_list):
        """Compute score for a list of signals."""
        total_score = 0
        for sig in signal_list:
            severity = sig["severity"]
            category = sig["category"]
            base_weight = SEVERITY_WEIGHTS[severity]
            multiplier = CATEGORY_MULTIPLIERS.get(category, 1.0)
            total_score += base_weight * multiplier

        signal_count = len(signal_list)
        if signal_count > 0:
            divisor = max(0.5, signal_count / 10.0)
            normalized = int(total_score / divisor)
            score = min(100, normalized)
        else:
            score = 0

        return score, total_score, signal_count

    # Test cases
    print("Risk Scoring Tests:")
    print("=" * 70)

    # Test 1: 1 critical security signal
    signals = [{"severity": "critical", "category": "security", "message": "test"}]
    score, total, count = compute_score(signals)
    print("1 critical security signal")
    print(f"  Raw total: {total}, Signal count: {count}")
    print(f"  Score: {score}/100")
    assert 55 <= score <= 65, f"Expected ~60, got {score}"
    print("  ✓ PASS\n")

    # Test 2: 20 critical reliability signals
    signals = [
        {"severity": "critical", "category": "reliability", "message": "test"}
    ] * 20
    score, total, count = compute_score(signals)
    print("20 critical reliability signals")
    print(f"  Raw total: {total}, Signal count: {count}")
    print(f"  Score: {score}/100")
    assert score == 100, f"Expected 100, got {score}"
    print("  ✓ PASS\n")

    # Test 3: 30 medium reliability signals (the saturation problem)
    signals = [
        {"severity": "medium", "category": "reliability", "message": "test"}
    ] * 30
    score, total, count = compute_score(signals)
    print("30 medium reliability signals (SATURATION TEST)")
    print(f"  Raw total: {total}, Signal count: {count}")
    print(f"  Score: {score}/100")
    assert score < 100, f"Score should be <100 to prevent saturation, got {score}"
    assert 45 <= score <= 70, f"Expected range 45-70, got {score}"
    print("  ✓ PASS - No saturation!\n")

    # Test 4: 10 medium signals
    signals = [
        {"severity": "medium", "category": "reliability", "message": "test"}
    ] * 10
    score, total, count = compute_score(signals)
    print("10 medium reliability signals")
    print(f"  Raw total: {total}, Signal count: {count}")
    print(f"  Score: {score}/100")
    print("  ✓ OK\n")

    # Test 5: 100 low signals
    signals = [{"severity": "low", "category": "cost", "message": "test"}] * 100
    score, total, count = compute_score(signals)
    print("100 low cost signals")
    print(f"  Raw total: {total}, Signal count: {count}")
    print(f"  Score: {score}/100")
    assert score < 50, f"Many low signals should not be high risk, got {score}"
    print("  ✓ PASS\n")

    print("=" * 70)
    print("✅ All formula verification tests passed!")


if __name__ == "__main__":
    test_formula()
