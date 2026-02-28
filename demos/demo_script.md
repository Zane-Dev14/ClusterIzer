# ClusterGPT Demo Script

## Prerequisites

- Minikube or Docker Desktop Kubernetes running locally
- Python 3.11+ with virtualenv
- `kubectl` configured and pointing to local cluster

## Setup

```bash
# 1. Install dependencies
cd /path/to/week-4
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Ensure local cluster is running
minikube start  # or: enable Kubernetes in Docker Desktop

# 3. Deploy demo apps with intentional issues
kubectl apply -f demos/sample_app/crashloop-app.yaml
kubectl apply -f demos/sample_app/oom-app.yaml
kubectl apply -f demos/sample_app/bad-practices-app.yaml

# 4. Wait for failures to manifest (30-60 seconds)
sleep 60
kubectl get pods
```

## Run ClusterGPT

```bash
# Full analysis (generates report.md)
python -m app.main analyze --kubeconfig ~/.kube/config --output report.md --debug

# View the report
cat report.md

# Or view specific sections
head -40 report.md              # Header + findings table
grep -A5 "## Cost" report.md    # Cost section only
```

## Expected Output

The report should contain:

1. **Risk Score**: 70-100/100 (multiple critical + high findings)
2. **Findings** (at least these):
   - `wildcard_rbac:cluster/cluster-admin` — critical
   - `privileged_container:default/bad-practices-app/insecure-app` — critical
   - `missing_requests` for crashloop-app and bad-practices-app — high
   - `missing_limits` for crashloop-app and bad-practices-app — high
   - `single_replica` for all three deployments — high
   - `image_latest` for bad-practices-app — medium
   - `missing_readiness_probe` for all — medium
   - `missing_liveness_probe` for all — medium
   - `no_network_policy:default` — medium
3. **Cost Estimate**: should show per-deployment costs
4. **Diagnosis**: CrashLoopBackOff and OOMKilled detected
5. **Remediation Commands**: kubectl commands and YAML patches

## Dry-Run Remediation

```bash
# Show what would be fixed without applying
python -m app.main analyze --kubeconfig ~/.kube/config --dry-run
```

## Cleanup

```bash
kubectl delete -f demos/sample_app/crashloop-app.yaml
kubectl delete -f demos/sample_app/oom-app.yaml
kubectl delete -f demos/sample_app/bad-practices-app.yaml
```

## Snapshot Diff (compare before/after)

```bash
# Take snapshot before fixes
python -m app.main snapshot --kubeconfig ~/.kube/config
cp snapshots/latest.json snapshots/before.json

# Apply fixes, then take another snapshot
python -m app.main snapshot --kubeconfig ~/.kube/config
cp snapshots/latest.json snapshots/after.json

# Compare
python -m app.main diff snapshots/before.json snapshots/after.json
```
