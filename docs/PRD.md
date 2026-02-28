# ClusterGPT — PRD (detailed, senior-level)


# 1 — What is this app (the agents)?

**Product name (MVP):** ClusterGPT — Autonomous Kubernetes Auditor & Co-Pilot

**High-level definition:**
A compact CLI + lightweight web/reporting service that connects to any Kubernetes cluster (via kubeconfig), constructs a structured model of cluster state (resources, networking, policies), runs domain rules + heuristics + small LLM-assisted explanation, and emits a single actionable audit report covering **architecture**, **reliability**, **cost**, and **security** — with prioritized fixes and concrete commands/Patch YAMLs.

**Agent concept (the runtime actors):**

* **Connector Agent (Cluster Extractor)** — pulls cluster state via Kubernetes API: deployments, pods, events, node info, HPA, CRDs, RBAC, networkpolicies, services, ingresses, PVCs, images, and resource requests/limits.
* **Graph Builder Agent** — builds an internal dependency/graph model (service → pods → nodes → storage).
* **Heuristic Engine Agent** — rule engine that runs deterministic checks (anti-patterns, CIS checks via kube-bench, Trivy integration) and cost heuristics.
* **Cost Analyst Agent** — computes quick cost estimates from resource requests/limits + node prices (simple price model), and highlights waste + “hot” spenders.
* **Diagnosis Agent (Investigator)** — correlates events (CrashLoopBackOff, OOMKill), recent deploys, restart counts, and resource metrics to identify the most likely root causes.
* **Explainer Agent (LLM Wrapper)** — uses a structured prompt + retrieved structured facts (no raw telemetry dump) to generate three outputs: CTO-level summary, SRE-level action list, and commit/pull-request text. This agent is constrained to produce JSON + deterministic templates to avoid hallucinations.
* **Remediation Agent (optional, gated)** — prepares YAML patches or `kubectl` commands for safe fixes (limits/requests, readinessProbe/livenessProbe, HPA enablement). By default it only **suggests** patches; optionally can apply patches with explicit `--apply` flag.
* **Verifier Agent** — runs short checks after remediation to validate status (pod restarts, current replica counts, probe status).

Agents are synchronous orchestrated steps in a pipeline (Connector → Graph → Heuristic + Cost + Diagnosis → Explainer → Remediation → Verify).

---

# 2 — How will this work (end-to-end flow)?

1. **User runs**:
   `clustergpt analyze --kubeconfig ~/.kube/config --output report.md`
2. **Connector** authenticates to the cluster (k8s Python SDK) and snapshots:

   * `kubectl get` outputs (deployments, pods, nodes, events)
   * YAML manifests for Deployments/StatefulSets/DaemonSets
   * HPA objects, PodDisruptionBudgets, PVC usage
   * RBAC Roles/Bindings, NetworkPolicies, Ingresses
   * Image tags and node labels
3. **Graph Builder** creates dependency graph (NetworkX) and indexes resources by service, namespace, label set, node.
4. **Heuristic Engine** runs deterministic checks (examples below). Each check yields a severity, explanation, and remediation snippet.
5. **Cost Analyst** runs cost estimation:

   * Use request × replicas × uptime × unit price for CPU/RAM.
   * Estimate node cost per hour via small mapping (e.g., t3.medium -> $0.041/hr) or user-provided price overrides.
6. **Diagnosis Agent**:

   * Detects recent failures: CrashLoop/OOM/ImagePullBackOff/Unschedulable.
   * Correlates events by timestamp and pod labels; ranks hypotheses (memory OOM, misconfig, volume issues).
7. **Explainer Agent**:

   * Build RAG-style prompt but only feed structured facts (no raw logs).
   * Generate: (A) Executive summary, (B) Top-3 actionable fixes with confidence scores, (C) `kubectl` or patch YAML for each fix, (D) PR description text.
   * Use local LLM if you want qLoRA demo: fine-tune a small 7B/3B for **log classification / issue summarization** only — optional.
8. **Report generator** writes `report.md` and an optional PDF, with:

   * Summary dashboard (cost, risk score)
   * Findings table (severity, file/manifest pointers, suggested patch)
   * Diffs and `kubectl` commands
9. **Remediation (optional)**: When user passes `--apply` or `--dry-run`, Remediation Agent either outputs commands or applies them using Kubernetes API and records verification results.

---

# 3 — How do we keep it minimal (MVP)?

**MVP Goals (shipped in 72 hours):**

* CLI that analyzes a live cluster and emits a prioritized Markdown audit report.
* Deterministic rule engine covering ~20 high-impact checks (reliability, security, cost).
* Cost estimator using resource requests/limits + node type mapping.
* Diagnosis of pod failures using events, restart counts, and manifest metadata.
* LLM explanation layer using OpenAI (or local tiny HF model) that takes structured facts and returns templated outputs (no log ingestion or heavy RAG).
* Dockerfile + Kubernetes deployment for the service itself (for demo).
* Nice demo README + sample cluster YAMLs and a recorded demo script.

**What we exclude from MVP:**

* Prometheus time-series integration (beyond optional `kubectl top` fallback).
* Full billing API integrations (AWS/Azure/GCP).
* Vector DB / long-term log indexing (Milvus, Elasticsearch).
* Full ML training pipelines (QLoRA optional mini-task).
* Multi-cluster orchestration.

**Why minimal choices hit maximum impact:**
Static cluster state + events + small heuristics catch the highest ROI problems (missing limits, missing probes, single replicas, image:latest, privileged containers) and yield explainable, reproducible remediations that are interview-worthy.

---

# 4 — How will we decide when the goal is achieved? (Acceptance criteria)

**Project Acceptance Criteria (MVP):**

1. **Functional:**

   * `clustergpt analyze` runs against a real cluster and produces `report.md` within 2–5 minutes for a 10-node dev cluster.
   * The report includes: architecture summary, top 10 findings (severity + explanation), cost estimate, and 1-click patch suggestions (YAML or kubectl commands).
   * For at least 3 failure types (CrashLoopBackOff, OOMKill, ImagePullBackOff), the Diagnosis Agent outputs correct root cause *and* a recommended remediation that resolves the issue in a demo cluster (or a simulated cluster).
2. **Quality:**

   * No hallucinated fixes: every remediation has a data pointer (manifest line / object UID / event timestamp).
   * Output is reproducible: running the same snapshot yields the same findings.
3. **Polish:**

   * Report is clear and presentable (Markdown + PDF).
   * Codebase <= ~2,500 lines (target ~1,500).
   * Dockerfile builds and runs the service; Kubernetes manifest deploys the analyzer.
4. **Course deliverables mapped:**

   * HuggingFace/QLoRA: include a small fine-tune on a synthetic log classification task (optional) with demonstration notebook.
   * Docker/K8s: repo contains Dockerfile, Docker Compose (optional), and K8s manifest; shows deployment and demo.
   * Presentation: demo script + 5–8 slide summary template.

When all of the above are true, the MVP is done.

---

# 5 — How is this unique and interview-magnetic?

Concrete reasons interviewers will probe and be impressed:

* **Product scope + depth** — not just a scanner; it reasons across architecture, cost, security, and remediation. That breadth + depth signals systems thinking.
* **Explainability discipline** — structured, data-anchored LLM outputs (not hallucinations) with pointer evidence — demonstrates you know LLM limits and mitigate them.
* **Real-world applicability** — works across arbitrary clusters without prescriptive instrumentation — shows practical operational impact.
* **Consultant/enterprise readiness** — PDF audits + PR generation = consultant deliverable.
* **Extensibility** — clear extension points (prometheus integration, billing APIs, policy engine) — you can explain how you scale this to production.
* **Engineering quality** — small, readable codebase with clean boundaries: connector / graph / rule engine / LLM wrapper — a senior engineer recognizes good architecture.
* **CI/CD hooks** — `clustergpt diff` for PRs in CI demonstrates product thinking (security gates, cost gates) — many interviewers will ask about CI integration and you’ll already have a plan.

In short: this is product + engineering + operational impact. That combo is what gets senior roles.

---

# 6 — How do we do this in the least time possible while satisfying course topics?

**General strategy:**

* Use deterministic rule engine for most intelligence.
* Use LLM only for *templated explanation* — pass structured JSON and ask for fixed JSON/Markdown outputs (avoids hallucination).
* Use OpenAI API for explanation (fast). Optionally: fine-tune a small HF model (QLoRA) *only* to classify log snippets to labels like `OOM`, `OOM_LIKELY`, `CRASH_ON_START` — small dataset, quick run, demonstrates fine-tuning competency without time sink.
* Reuse existing code from your multi-agent system (investigator orchestration) but remove dependencies (Milvus). You already have a multi-agent CLI — refactor and focus it on the new product.

**Exact mapping to your Week 4 deliverables:**

* **Days 1–2 (HF & QLoRA):** Create a tiny dataset (synthetic or scraped sample logs from GitHub) and QLoRA fine-tune a 3B/7B model to perform log classification. Keep it optional/plug-in. Deliverable: notebook + model checkpoint + script `classify_log.py`. If compute limited, show fine-tuning steps and use `AutoModelForSequenceClassification` with PEFT LoRA and 4-bit quantization (demonstration only).
* **Days 3–5 (Docker & Kubernetes):** Dockerize the app, deploy to Minikube/Docker Desktop, provide Docker Compose and K8s manifests. Show a demo where the analyzer runs against a sample cluster and produces a report.

---

# Checkpoints, goals, and stop-conditions

**Sprint: 72 hours — Checkpoint plan**

### Day 0 (prep — 2 hours)

* Repo scaffold + environment setup.
* Confirm local cluster (Minikube / Docker Desktop) ready.
* Create sample vulnerable/demo YAMLs (api CrashLoop, missing probes, high requests).

**Checkpoint 0:** `git init`, `README` with run steps, demo cluster manifests present.

---

### Day 1 (12–14 hours) — Core pipeline

* Implement Connector Agent (k8s SDK) — snapshot objects & produce JSON.
* Implement Graph Builder — basic NetworkX graph of services/pods/nodes.
* Implement Heuristic Engine with 12 rules:

  1. Missing requests
  2. Missing limits
  3. Missing readinessProbe
  4. Missing livenessProbe
  5. Single replica
  6. Image:latest
  7. Privileged container
  8. No networkpolicy in namespace
  9. Wildcard RBAC role
  10. Large resource requests vs node size (overprovision)
  11. HPA missing but high CPU request
  12. PVC not bound / storage class missing
* Implement a simple Diagnosis Agent that checks events / recent deploy timestamp and restart counts for the top 3 failure types.
* Implement report generator that writes `report.md` with findings.

**Checkpoint 1 (end Day 1):** `clustergpt analyze` runs and emits `report.md`. Demo against local sample cluster shows at least 6 findings. Stop feature work if all 12 rules produce expected findings on sample cluster.

---

### Day 2 (12–14 hours) — Cost + LLM explain + remediation

* Implement Cost Analyst (resource→hour estimate, node price table).
* Implement LLM Explainer wrapper (initially OpenAI with structured prompt). Build a `templates/explain_prompt.json` and `explain.py` that accepts structured facts and returns 3 scoped sections.
* Implement Remediation Agent that emits patch YAMLs and `kubectl` commands for the top 5 fixes. (Default: no apply.)
* Integrate verification step to re-check objects after `--dry-run` or `--apply`.
* Hook in optional `classify_log.py` (QLoRA fine-tuning pipeline stub with sample data) — make it optional: if no GPU, skip.

**Checkpoint 2 (end Day 2):** Generated report includes cost estimate and three explanation levels. Remediation YAMLs present for at least 4 rules. Stop feature work when explainer reliably returns JSON schema for 10 test cases.

---

### Day 3 (10–12 hours) — Dockerize, K8s deploy, polish, presentation

* Write Dockerfile and build/run scripts.
* Add Kubernetes manifest to deploy analyzer as a pod (or CronJob).
* Add `clustergpt diff` (minimal) that compares two snapshot JSONs.
* Write demo script, README, slide deck outline, and record short demo (or prepare CLI demo steps).
* Optional: add one small fine-tuned HF artifact demonstration (not required for core product).

**Checkpoint 3 (end Day 3):** Docker image builds, analyzer runs inside K8s against cluster, and `report.md` is produced. Slide deck and demo checklist ready. STOP if all acceptance criteria met.

---

# How to know when to stop working on a feature (Definition of Done)

For each feature:

* **Code complete**: implemented with tests (unit or local smoke), and works on demo cluster.
* **Documented**: README shows how to run it in 3 steps and expected sample outputs.
* **Bounded complexity**: feature must not pull in new heavy dependencies (no Elasticsearch, no vector DB).
* **Timebox**: if a feature is not working within 4 hours, revert to fallback (e.g., “explain via template” instead of heavy parsing).
* **Demoable**: you can show the feature producing a real artifact (report/patch) in under 3 minutes.

Stop adding features when all MVP acceptance criteria are green.

---

# Detailed feature list & how to build each (concrete steps)

I’ll list each feature with precise implementation steps, expected inputs/outputs, and stop condition.

---

## A. CLI & entrypoint

**Files:** `app/main.py`, `cli.py`
**What it does:** Accepts flags: `--kubeconfig`, `--namespace`, `--output`, `--apply`, `--dry-run`.
**Implementation steps:**

1. Use `argparse` or `typer` (typer recommended — small, nice UX).
2. `analyze` command orchestrates pipeline and writes `report.md`.
3. Provide `--debug` flag to emit snapshot JSON to `snapshots/` folder.

**Stop condition:** CLI runs end-to-end producing `report.md`.

---

## B. Connector Agent (k8s snapshot)

**Files:** `app/tools/kubectl.py` or `app/tools/k8s_connector.py`
**How:** Use `kubernetes` Python client (`pip install kubernetes`) to `list_*` objects. Serialize to JSON with Pydantic models. Capture events with `v1.list_namespaced_event()` and recent timestamps.
**Key outputs:** `snapshot.json` (namespaces → deployments → pods → nodes → events).
**Stop condition:** Snapshot contains sufficient data for heuristics (deployments, pods, events, nodes).

---

## C. Graph Builder

**Files:** `app/agents/graph_builder.py`
**How:** Use NetworkX to create nodes (service/deployment/pod/node) and edges (deploy→pod, pod→node). Compute simple centralities and identify single points of failure.
**Stop condition:** Graph saved as `graph.json` and used by heuristic engine.

---

## D. Heuristic Rule Engine

**Files:** `app/agents/rules.py` (single file, rule definitions)
**How:** Rules are simple Python functions returning `(id, severity, message, evidence, remediation_snippet)`. Keep rules declarative (list of dicts) for easy extension. Run rules across snapshot. Example rule: `missing_limits(deployment)` → severity high if no limits set and replicas > 1.
**Stop condition:** All 12 MVP rules implemented and passing sample tests.

---

## E. Cost Analyst

**Files:** `app/tools/cost_model.py`
**How:** Implement per-hour cost model:

```py
cpu_hour_cost = 0.03  # default
ram_gb_hour_cost = 0.004
node_price_map = {"small": 0.05, ...}  # allow overrides via config
```

Calculate per-deployment estimate: `replicas * requests_cpu * cpu_hour_cost + replicas * requests_mem_gb * ram_gb_hour_cost`, scaled to monthly. Provide waste% by comparing requests vs node capacity usage (best-effort using node allocatable).
**Stop condition:** Cost estimates present in report and align with simple expected numbers for demo clusters.

---

## F. Diagnosis Agent (failure correlation)

**Files:** `app/agents/investigator.py`
**How:** For pods with non-running state or high restart counts:

* Check associated events `kubectl describe pod` equivalent.
* If event reason contains `OOMKilled` → tag OOM.
* If `BackOff` and containerStatus.exitCode != 0 → recommend logs check and memory bump.
* Rank top hypotheses with a confidence score (based on event match weight and restart count).
  **Stop condition:** For the 3 demo failure types, diagnosis returns correct hypotheses on demo cluster.

---

## G. Explainer Agent (LLM wrapper)

**Files:** `app/agents/explainer.py`, `app/templates/prompts.json`
**How:** Build a fixed JSON schema for the explainer input:

```json
{
  "summary": "...",
  "top_findings": [{ "id":"", "severity":"", "evidence":[], "remediation":"", "patch":"", "kubectl":"" }],
  "cost": { ... }
}
```

Send this short JSON to OpenAI (or to a local HF model inference server) with a prompt instructing the model to *only* return JSON with three fields: `exec_summary`, `sre_actions`, `pr_text`. Keep prompt strict and include example pairs. If using local model, use small model with `transformers` inference. If QLoRA: fine-tune small model for classification of logs only — not required for explainer.

**Stop condition:** Explainer returns valid JSON matching schema for sample inputs.

---

## H. Remediation (patch generator)

**Files:** `app/agents/remediation.py`
**How:** For each finding generate:

* `kubectl patch` command or
* A YAML snippet showing minimal change (e.g., add `resources.requests/limits`, add `readinessProbe`, add `replicas: 2`).
  Apply only if user supplies `--apply` and confirms. Use k8s API `patch_namespaced_deployment`. Always create a backup of current manifest file in `backups/`.
  **Stop condition:** Suggested YAMLs are syntactically valid and apply in demo cluster with `--dry-run` true.

---

## I. Verifier

**Files:** `app/agents/verifier.py`
**How:** After remediation, re-snapshot target objects and check expected state (pod status healthy, restart counts not increasing). Report verification pass/fail.
**Stop condition:** Verification step validates at least one remediation in demo.

---

## J. Report generator

**Files:** `app/reporting/report.py`
**How:** Combine outputs into `report.md`, with frontmatter (cluster name, time, risk score), findings table, cost summary, and append patch files and `kubectl` commands. Provide an optional `report.pdf` using a small markdown→pdf tool (e.g., `pandoc` if available).
**Stop condition:** Report is present and renders correctly.

---

# Folder / file tree (minimal)

```
clustergpt/
├── app/
│   ├── main.py                 # CLI entry (typer)
│   ├── config.py               # constants, price map
│   ├── agents/
│   │   ├── graph_builder.py
│   │   ├── investigator.py
│   │   ├── remediation.py
│   │   ├── verifier.py
│   │   └── explainer.py
│   ├── tools/
│   │   ├── k8s_connector.py
│   │   ├── cost_model.py
│   │   └── utils.py
│   ├── rules.py
│   └── reporting/
│       └── report.py
├── demos/
│   ├── sample_app/             # sample app manifests to demo failures
│   └── demo_script.md
├── Dockerfile
├── k8s/
│   └── clustergpt-deployment.yaml
├── requirements.txt
├── README.md
└── docs/
    └── slides.md
```

Target ~12–16 files only. Keep modules small.

---

# Lines of code & complexity targets

* **Total LOC** target: **1,200–1,800** (Python only).

  * Connector: ~250 LOC
  * Graph + rules: ~250 LOC
  * Explainer wrapper: ~180 LOC
  * Cost model & remediation: ~200 LOC
  * CLI & reporting: ~200 LOC
  * Tests + demo scripts: ~200 LOC

Keep functions small and well documented.

---

# Tech stack (concise)

* Language: Python 3.11
* Frameworks: `typer` for CLI, `kubernetes` Python client, `networkx`, `pydantic`
* LLM: OpenAI API for explanations (fast). Optional local HF model: `transformers`, `peft`, `bitsandbytes` for qLoRA demo (fine-tuning logs classifier).
* Container: Docker
* K8s: Minikube / Docker Desktop for demo
* Optional security tools: integrate `trivy` & `kube-bench` CLI outputs (shell calls) for extra security checks.

---

# Implementation safety: avoid hallucinations

* **Never** feed raw logs in prompt; only pass structured facts (event reasons, timestamps, manifest references).
* Require the explainer to output only JSON per schema. If it returns invalid JSON, fallback to templated text.
* Always include `evidence` array with object UID and event timestamp for every finding the LLM references.

---

# How to incorporate QLoRA / Hugging Face in the least painful way

**Minimal demonstration (optional, Day 1–2):**

* Create a small labeled dataset of log lines (`OOMKilled`, `Segfault`, `Stacktrace` → labels).
* Fine-tune a small HF model (e.g., `meta-llama/llama-2-7b` or a smaller 3B community model) via PEFT/LoRA + 4-bit quant (QLoRA) to classify log lines. Keep epochs low (1–2) and dataset small (1–2k lines) to finish quickly.
* Expose `classify_log.py` which takes log lines and returns label; use label as evidence for Diagnosis Agent.
* If no GPU, include a notebook showing the exact commands (pass course deliverable) and fallback to OpenAI classification.

**Why this minimal step is enough:** You demonstrate knowledge of HF + QLoRA while keeping the model scope small and focused.

---

# Checkpoint acceptance tests (short)

* **Snapshot test:** Run `python -m app.tools.k8s_connector --kubeconfig ~/.kube/config` → outputs `snapshots/latest.json`. (PASS if file non-empty)
* **Rules test:** Run `python -m app.rules --snapshot snapshots/latest.json` → prints findings (PASS if >= 6 findings for demo cluster)
* **LLM test:** `python -m app.agents.explainer --facts facts.json` → returns valid JSON with `exec_summary`.
* **End-to-end:** `clustergpt analyze --kubeconfig ~/.kube/config` → generates `report.md`.
* **Remediation test:** `clustergpt analyze --kubeconfig ... --apply --target deployment/api --dry-run` → outputs patch YAML; `--apply` actually patches in demo only if user desires.

Each test is a one-liner in README.

---

# Feature-by-feature stop conditions and timeboxes

* **Connector:** 4 hours. If stuck on some API, fallback to `kubectl` subprocess parsing.
* **Graph Builder:** 2 hours. If NetworkX import issues, use plain dicts.
* **Rules:** 8 hours. Implement top 12 rules first; stop once each rule produces output on demo cluster.
* **Cost model:** 3 hours. If node mapping missing, use defaults and allow CLI overrides.
* **Diagnosis:** 6 hours. Implement core failure detection; stop when 3 failure scenarios detected correctly.
* **Explainer:** 6 hours. If OpenAI latency or budget issues, use template generator with simple string formatting.
* **Remediation / Apply:** 4 hours. Default `--dry-run` and only allow `--apply` explicitly; stop if verification unreliable.
* **Docker + K8s manifests:** 4 hours. If CRDs or RBAC complexity arises, run analyzer as local CLI only.

---

# Risks & mitigations

* **Risk:** LLM hallucinates.
  **Mitigation:** Strict JSON schema + pass only structured facts + always include evidence pointers.
* **Risk:** Kubernetes API permission errors in customers’ clusters.
  **Mitigation:** Document required RBAC scope; detect insufficient perms and fail with explicit message.
* **Risk:** Cost estimate inaccurate.
  **Mitigation:** Flag cost estimates as “approximate” with price override option and confidence score.
* **Risk:** Time overruns.
  **Mitigation:** Strict timeboxes and stop conditions above; prioritize end-to-end working demo over polish.

---

# Deliverables you should produce (by the end of the sprint)

1. `clustergpt` repo with files above.
2. `report.md` from sample demo cluster (two reports: before & after remediation).
3. Dockerfile and k8s manifest for analyzer.
4. `demos/sample_app` — vulnerable manifests to demo CrashLoop, missing probes, high requests.
5. Notebook or script showing minimal QLoRA fine-tune (optional but included).
6. Slide deck (6–8 slides) and demo script for the Week 4 presentation.
7. Short recorded demo (optional) or a CLI run transcript.

---

# Presentation / demo plan (what to show)

1. Quick problem statement (30s).
2. Run `clustergpt analyze --kubeconfig demo_kubeconfig` (real-time: show `report.md` generation). (2–3min)
3. Show top findings: missing probes, OOM, image:latest, and cost waste summary (1min).
4. Show remediation snippet and `kubectl apply`/`--dry-run` patch (1min).
5. Run verification and show resolved state (2min).
6. Explain architecture and where you’d extend to Prometheus, billing APIs, policy-as-code (2 min).
7. Answer questions: cost estimation, LLM guardrails, scaling concerns (remaining time).

---

# Final checklist: start coding now (concrete first commands)

1. `git init clustergpt && cd clustergpt`
2. Create venv, `pip install typer kubernetes networkx pydantic openai rich`
3. Create `app/main.py` with a `typer` skeleton for `analyze`
4. Implement quick `k8s_connector.py` that uses `kubernetes` client to `list_namespaced_pod`, `list_namespaced_deployment`, `list_node` and write `snapshots/latest.json`. Test it.
5. Implement `rules.py` with one or two rules (missing probes, missing limits). Iterate.
6. Hook explainer to OpenAI by passing a small JSON and expecting a specific JSON response.
7. Add demo manifests and test end-to-end.
