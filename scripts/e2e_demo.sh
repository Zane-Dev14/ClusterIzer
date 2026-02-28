#!/usr/bin/env bash
# e2e_demo.sh — Deploy demo apps, run analyzer, verify report quality.
# Requires: kubectl configured, cluster running, demo apps in demos/sample_app/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"
REPORT="report_e2e.md"
MIN_FINDINGS=3

echo "=== E2E Demo Test ==="

# Deploy demo apps
echo "Deploying demo manifests..."
kubectl apply -f demos/sample_app/crashloop-app.yaml
kubectl apply -f demos/sample_app/oom-app.yaml
kubectl apply -f demos/sample_app/bad-practices-app.yaml

echo "Waiting 60s for failures to manifest..."
sleep 60

# Run analyzer
echo "Running ClusterGPT analyze..."
python -m app.main analyze \
    --kubeconfig "$KUBECONFIG_PATH" \
    --output "$REPORT" \
    --debug

# Verify report exists
if [[ ! -f "$REPORT" ]]; then
    echo "❌  Report file not created"; exit 1
fi
echo "✅  Report generated: $REPORT ($(wc -c < "$REPORT") bytes)"

# Count findings (rows in the findings table, excluding header/separator)
FINDING_COUNT=$(grep -c '^\| F-' "$REPORT" 2>/dev/null || echo 0)
echo "   Findings detected: $FINDING_COUNT"

if (( FINDING_COUNT < MIN_FINDINGS )); then
    echo "❌  Expected at least $MIN_FINDINGS findings, got $FINDING_COUNT"
    exit 1
fi
echo "✅  At least $MIN_FINDINGS findings present"

# Verify key sections exist
for section in "Risk Score" "Findings" "Cost Analysis" "Remediation"; do
    if grep -qi "$section" "$REPORT"; then
        echo "✅  Section found: $section"
    else
        echo "⚠️   Section missing: $section"
    fi
done

# Cleanup
echo "Cleaning up demo apps..."
kubectl delete -f demos/sample_app/ --ignore-not-found
rm -f "$REPORT"

echo "=== E2E Demo Test PASSED ==="
