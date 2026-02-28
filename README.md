# ClusterGPT — Autonomous Kubernetes Auditor & Co-Pilot

An AI-powered CLI that connects to any Kubernetes cluster, audits its architecture, reliability, cost, and security posture, then emits a single actionable report with prioritized fixes.

## Quick Start

```bash
# 1. Clone & install
git clone <repo-url> && cd week-4
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Analyze your cluster
python -m app.main analyze --kubeconfig ~/.kube/config --output report.md

# 3. Read the report
cat report.md
```

## Architecture

```
Connector → Graph Builder → Rules Engine (12 checks)
                          → Cost Analyst
                          → Investigator (diagnosis)
                                ↓
                          Explainer (LLM / template)
                                ↓
                          Report Generator → report.md / report.json
                                ↓
                          Remediation (--apply / --dry-run)
                                ↓
                          Verifier (post-fix health check)
```

**Pipeline**: All agents run synchronously in a single pass:
`Connector → Graph → Rules + Cost + Investigator → Explainer → Report → Remediation → Verifier`

### Agent Modules

| Agent | File | Purpose |
|-------|------|---------|
| Connector | `app/tools/k8s_connector.py` | Snapshots cluster state via K8s API |
| Graph Builder | `app/agents/graph_builder.py` | NetworkX dependency graph |
| Rules Engine | `app/rules.py` | 12 deterministic audit checks |
| Cost Analyst | `app/tools/cost_model.py` | Per-deployment monthly cost estimates |
| Investigator | `app/agents/investigator.py` | CrashLoop / OOM / ImagePull diagnosis |
| Explainer | `app/agents/explainer.py` | LLM summary with template fallback |
| Remediation | `app/agents/remediation.py` | kubectl commands & YAML patches |
| Verifier | `app/agents/verifier.py` | Post-remediation health checks |
| Report | `app/reporting/report.py` | Markdown / JSON / PDF output |

## CLI Usage

```bash
# Full analysis with debug output
python -m app.main analyze \
    --kubeconfig ~/.kube/config \
    --output report.md \
    --namespace default \
    --debug

# Dry-run remediation (show patches without applying)
python -m app.main analyze --kubeconfig ~/.kube/config --dry-run

# Apply remediations (safety-gated with confirmation prompt)
python -m app.main analyze --kubeconfig ~/.kube/config --apply

# Skip confirmation
python -m app.main analyze --kubeconfig ~/.kube/config --apply --yes

# Custom pricing
python -m app.main analyze \
    --kubeconfig ~/.kube/config \
    --price-cpu 0.05 \
    --price-ram 0.006

# Snapshot only (no analysis)
python -m app.main snapshot --kubeconfig ~/.kube/config

# Compare two snapshots
python -m app.main diff snapshots/before.json snapshots/after.json
```

### Commands

| Command | Description |
|---------|-------------|
| `analyze` | Full pipeline: snapshot → audit → report |
| `snapshot` | Save cluster state to `snapshots/latest.json` |
| `diff` | Compare two snapshot files |

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--kubeconfig` | `~/.kube/config` | Path to kubeconfig |
| `--output` | `report.md` | Report output path |
| `--namespace` | all | Filter to specific namespace |
| `--apply` | off | Apply remediations |
| `--dry-run` | off | Show patches without applying |
| `--yes` | off | Skip confirmation prompts |
| `--debug` | off | Write intermediate JSON to `snapshots/` |
| `--price-cpu` | 0.03 | CPU cost per core-hour (USD) |
| `--price-ram` | 0.004 | RAM cost per GB-hour (USD) |

## Audit Rules (12)

| ID | Category | Severity | What it checks |
|----|----------|----------|----------------|
| `missing_requests` | Reliability | High | Containers without CPU/memory requests |
| `missing_limits` | Reliability | High | Containers without CPU/memory limits |
| `single_replica` | Reliability | High | Deployments with replicas=1 |
| `missing_readiness_probe` | Reliability | Medium | No readinessProbe |
| `missing_liveness_probe` | Reliability | Medium | No livenessProbe |
| `image_latest` | Security | Medium | `:latest` or untagged images |
| `wildcard_rbac` | Security | Critical | ClusterRoles with `*` verbs |
| `privileged_container` | Security | Critical | `privileged: true` containers |
| `no_network_policy` | Security | Medium | Namespaces without NetworkPolicies |
| `overprovision` | Cost | Medium | CPU/memory requests > 2× actual usage |
| `hpa_missing_high_cpu` | Architecture | Medium | High-CPU pods without HPA |
| `pvc_not_bound` | Reliability | High | PVCs stuck in non-Bound state |

## Report Output

The report includes:
- **Risk Score** (0–100): weighted sum of findings by severity
- **Findings Table**: severity, resource, description, remediation
- **Cost Analysis**: per-deployment monthly cost, total, waste %
- **Remediation Commands**: copy-paste `kubectl` commands
- **Explainer Output** (when available): executive summary, SRE actions, PR text

## Demo

See [demos/demo_script.md](demos/demo_script.md) for a walkthrough using purpose-built sample apps:

```bash
# Deploy intentionally broken apps
kubectl apply -f demos/sample_app/

# Run ClusterGPT
python -m app.main analyze --output report.md --debug

# Cleanup
kubectl delete -f demos/sample_app/
```

Demo manifests trigger: CrashLoopBackOff, OOMKilled, privileged containers, missing probes, no resource limits, `:latest` images, and single-replica deployments.

## Configuration

| Environment Variable | Purpose |
|---------------------|---------|
| `OPENAI_API_KEY` | Enable LLM-powered explainer (optional) |
| `KUBECONFIG` | Default kubeconfig path |

Pricing constants are in `app/config.py`. Override at CLI with `--price-cpu` / `--price-ram`.

## Testing

```bash
# Run all tests (offline + live if cluster available)
bash scripts/smoke_test.sh

# Offline only (no cluster needed)
python scripts/explainer_test.py

# With a live cluster
bash scripts/snapshot_test.sh
bash scripts/e2e_demo.sh
bash scripts/verify_remediation.sh
```

## Docker

```bash
# Build
docker build -t clustergpt:latest .

# Run (mount kubeconfig)
docker run --rm \
    -v ~/.kube/config:/home/clustergpt/.kube/config:ro \
    clustergpt:latest analyze --output /dev/stdout
```

## Kubernetes Deployment

Deploy as a CronJob that audits every 6 hours:

```bash
kubectl apply -f k8s/clustergpt-deployment.yaml
```

This creates a ServiceAccount with read-only ClusterRole — no write permissions unless you add the Remediation Agent's RBAC.

## Project Structure

```
app/
├── __init__.py
├── main.py              # CLI entrypoint (typer)
├── models.py            # Pydantic data models
├── config.py            # Constants & pricing
├── rules.py             # 12 audit rules
├── tools/
│   ├── utils.py         # Shared helpers
│   ├── k8s_connector.py # Cluster snapshot
│   └── cost_model.py    # Cost estimation
├── agents/
│   ├── graph_builder.py # Dependency graph
│   ├── investigator.py  # Failure diagnosis
│   ├── explainer.py     # LLM / template explainer
│   ├── remediation.py   # Patch generation
│   └── verifier.py      # Post-fix validation
└── reporting/
    └── report.py        # Report generator

demos/
├── demo_script.md
└── sample_app/          # Intentionally broken K8s manifests

scripts/
├── smoke_test.sh        # Meta test runner
├── snapshot_test.sh     # Connector test
├── e2e_demo.sh          # Full pipeline test
├── explainer_test.py    # Offline explainer test
└── verify_remediation.sh

k8s/
└── clustergpt-deployment.yaml  # CronJob + RBAC

docs/
└── PRD.md               # Product Requirements Document
```

## Tech Stack

- **Python 3.11** with type hints throughout
- **typer** — CLI framework
- **kubernetes** (>=28.1) — official K8s Python client
- **pydantic** v2 — data validation & models
- **networkx** — dependency graph
- **openai** — optional LLM integration
- **rich** — terminal formatting (graceful fallback)

## License

Internal use — IBM course deliverable.
