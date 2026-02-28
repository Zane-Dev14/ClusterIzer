#!/usr/bin/env bash
# snapshot_test.sh — Verify the connector agent can snapshot a real cluster.
# Requires: kubectl configured and cluster reachable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

echo "=== Snapshot Test ==="

python -m app.main snapshot --kubeconfig "${KUBECONFIG:-$HOME/.kube/config}"

if [[ -f snapshots/latest.json ]]; then
    echo "✅  snapshots/latest.json exists ($(wc -c < snapshots/latest.json) bytes)"
else
    echo "❌  snapshots/latest.json not found"
    exit 1
fi

# Validate JSON
python -c "import json, sys; json.load(open('snapshots/latest.json')); print('✅  Valid JSON')" || {
    echo "❌  Invalid JSON in snapshot"; exit 1
}

echo "=== Snapshot Test PASSED ==="
