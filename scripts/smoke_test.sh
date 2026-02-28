#!/usr/bin/env bash
# smoke_test.sh — Meta-script that runs all tests in sequence.
# For offline mode (no cluster): runs only the explainer test.
# For live mode (with cluster): runs all tests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

source .venv/bin/activate 2>/dev/null || true

PASS=0
FAIL=0

run_test() {
    local name="$1"; shift
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Running: $name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if "$@"; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
        echo "⚠️  $name FAILED (continuing)"
    fi
}

# Always run: offline tests
run_test "Explainer Test" python scripts/explainer_test.py

# Cluster-dependent tests (skip if no cluster)
if kubectl cluster-info >/dev/null 2>&1; then
    echo ""
    echo "Cluster detected — running live tests..."
    run_test "Snapshot Test" bash scripts/snapshot_test.sh
    run_test "E2E Demo Test" bash scripts/e2e_demo.sh
    run_test "Dry-Run Remediation" bash scripts/verify_remediation.sh
else
    echo ""
    echo "⚠️  No cluster detected — skipping live tests"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Results: $PASS passed, $FAIL failed"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ $FAIL -eq 0 ]] && echo "✅  All tests passed!" || { echo "❌  Some tests failed"; exit 1; }
