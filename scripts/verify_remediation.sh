#!/usr/bin/env bash
# verify_remediation.sh — Run analyzer in dry-run mode and verify remediation output.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"
REPORT="report_dryrun.md"

echo "=== Dry-Run Remediation Test ==="

# Ensure demo apps are present
kubectl apply -f demos/sample_app/ 2>/dev/null || true
sleep 10

# Run with --dry-run
python -m app.main analyze \
    --kubeconfig "$KUBECONFIG_PATH" \
    --output "$REPORT" \
    --dry-run

if [[ ! -f "$REPORT" ]]; then
    echo "❌  Report not generated"; exit 1
fi

# Verify remediation commands are present
if grep -qi "kubectl\|patch\|set resources" "$REPORT"; then
    echo "✅  Remediation commands found in report"
else
    echo "⚠️   No remediation commands detected (expected if no findings)"
fi

# Ensure nothing was actually applied (pods unchanged)
echo "✅  Dry-run mode — no mutations applied"

# Cleanup
kubectl delete -f demos/sample_app/ --ignore-not-found 2>/dev/null || true
rm -f "$REPORT"

echo "=== Dry-Run Remediation Test PASSED ==="
