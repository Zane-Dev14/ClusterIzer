# KubeSentinel

**Kubernetes Intelligence Engine**

A deterministic-first, multi-agent analysis system for Kubernetes infrastructure assessment using LangChain, LangGraph, and Ollama.

---

## What is KubeSentinel?

KubeSentinel analyzes Kubernetes clusters to identify reliability, security, and cost issues. It combines:

- **Deterministic rules** (200+ built-in signals)
- **AI agents** (cost, security, reliability analyzers)
- **Query-aware routing** (understands what you're asking)
- **Safe remediation** (kubectl approval gates, audit logs)
- **Slack integration** (real-time cluster monitoring)

This is **not a chatbot**. It's a graph-based reasoning system that produces actionable intelligence.

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **uv** package manager ([install](https://github.com/astral-sh/uv))
- **Ollama** with `llama3.1:8b-instruct-q8_0` ([install](https://ollama.ai))
- Kubernetes cluster with kubeconfig or in-cluster auth

### Installation

```bash
make install
# or: uv sync
```

### Run a Scan

```bash
# Full cluster analysis
uv run kubesentinel scan

# Focus on cost
uv run kubesentinel scan --query "reduce costs"

# Focus on security
uv run kubesentinel scan --query "security audit"

# CI mode (exit 0 for A/B/C, exit 1 for D/F)
uv run kubesentinel scan --ci
```

**Output**:
- `report.md` - Full analysis with findings and recommendations
- Risk grade: A (healthy) through F (critical)

---

## Documentation

Comprehensive documentation is organized into 5 files:

| Document | Purpose |
|----------|---------|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | System design, pipeline, modules, design principles |
| **[IMPLEMENTATION.md](IMPLEMENTATION.md)** | Implementation details, algorithms, code structure |
| **[OPERATIONS_AND_USAGE.md](OPERATIONS_AND_USAGE.md)** | Installation, configuration, Slack setup, troubleshooting |
| **[REPORTS_AND_RESULTS.md](REPORTS_AND_RESULTS.md)** | Test results, performance metrics, safety validation |
| **[README.md](README.md)** | Quick start, overview, key features |

---

## Key Features

### Deterministic-First Analysis
- 200+ built-in signals for reliability, security, cost, and architecture
- Rules-based checks run first, LLM provides deeper insights when needed
- No hallucinations in numeric contexts (costs, replicas, etc.)

### Query-Aware Agent Routing
- Understand what you're asking ("reduce costs" vs "security audit")
- Route to appropriate analysis agents automatically
- Default fallback behavior for generic queries

### Safe Remediation
- Approval gates for dangerous kubectl commands
- Audit trail for all executions
- Clear distinction between diagnostics and remediation

### Slack Integration
- Interactive analysis directly in Slack
- Cache-aware follow-up questions (instant responses)
- Safe kubectl command execution with approval dialogs
- Rich formatting with risk scores and actionable findings

### Comprehensive Testing
- 73+ test scenarios covering all features
- 100% test pass rate
- Type-safe (mypy validation clean)
- Production-grade error handling

---

## Slack Integration (Socket Mode)

KubeSentinel can be accessed directly from Slack without any HTTP endpoint or ngrok.

### Setup

1. **Create a Slack app:**
   - Go to [api.slack.com/apps](https://api.slack.com/apps)
   - Click "Create New App" → "From scratch"
   - Name: `KubeSentinel`
   - Workspace: your workspace

2. **Enable Socket Mode:**
   - Go to Settings → Socket Mode
   - Toggle "Enable Socket Mode" ON
   - Generate an App-Level Token (`xapp-...`)
   - Copy this to `SLACK_APP_TOKEN` in `.env`

3. **Configure OAuth & Permissions:**
   - Go to OAuth & Permissions
   - Under "Bot Token Scopes", add:
     - `chat:write`
     - `app_mentions:read`
     - `channels:history`
     - `im:history`
   - Copy the Bot Token (`xoxb-...`) to `SLACK_BOT_TOKEN` in `.env`

4. **Subscribe to Events:**
   - Go to Event Subscriptions
   - Toggle "Enable Events" ON
   - Under "Subscribe to bot events", add:
     - `app_mention`
     - `message.im`
   - Save

5. **Get local credentials:**
   ```bash
   cp .env.example .env
   # Edit .env and paste your Bot Token and App Token
   ```

6. **Run the bot:**
   ```bash
   uv run kubesentinel-slack
   ```

   (Or: `uv run python -m kubesentinel.integrations.slack_bot`)

### Usage

Once the bot is running, you can ask KubeSentinel questions in Slack:

**Mention the bot in a channel:**
```
@kubesentinel why are pods pending
```

**Send a direct message to the bot:**
```
why are pods pending
```

The bot will respond in a thread with:
- Risk score (0-100) and grade (A-F)
- Strategic summary
- Top findings (reliability, cost, security)

Example reply:
```
KubeSentinel Analysis

Risk Score: 63/100 (C) 🟡 Medium

Summary:
Cluster has 2 pods in pending state due to node resource constraints.
Redis deployment lacks resource limits.

Top Findings:
• Unschedulable pods due to insufficient CPU
• Missing resource limits on deployments
• CrashLoopBackOff in media-frontend
```

### Important Security Notes

- **Never commit `.env` with real tokens.**
- `.env` is in `.gitignore` — keep it local only.
- Rotate tokens immediately if exposed:
  - Go to api.slack.com/apps → your app → Regenerate tokens

---

## Output

KubeSentinel generates `report.md` with the following sections:

1. **Architecture Report** - Cluster topology, orphan services, single-replica deployments
2. **Cost Optimization Report** - Over-provisioning, missing limits, waste
3. **Security Audit** - Privileged containers, :latest tags, vulnerabilities
4. **Reliability Risk Score** - Weighted risk assessment (0-100, grades A-F)
5. **Strategic AI Analysis** - Executive summary with prioritized recommendations

---

## Development

### Run Tests

```bash
make test

# Or with pytest directly
uv run pytest kubesentinel/tests/ -v
```

### Lint

```bash
make lint
```

### Type Check

```bash
make typecheck
```

### Clean

```bash
make clean
```

---

## Project Structure

```
kubesentinel/
├── __init__.py
├── models.py           # State contract (TypedDict)
├── cluster.py          # Cluster snapshot node
├── graph_builder.py    # Dependency graph node
├── signals.py          # Signal engine node
├── risk.py             # Risk model node
├── tools.py            # Deterministic tools for agents
├── agents.py           # Planner, agent nodes, synthesizer
├── runtime.py          # LangGraph orchestration
├── reporting.py        # Markdown report builder
├── main.py             # Typer CLI
├── prompts/            # Agent system prompts
│   ├── planner.txt
│   ├── failure_agent.txt
│   ├── cost_agent.txt
│   ├── security_agent.txt
│   └── synthesizer.txt
└── tests/              # Unit tests
    ├── test_signals.py
    ├── test_risk.py
    └── test_graph.py
```

---

## Design Principles

### 1. Deterministic Before Generative

All cluster inspection is deterministic. The LLM:

- NEVER connects to the cluster
- NEVER receives full raw cluster JSON
- NEVER mutates the cluster

LLMs only see:

- Slim snapshots
- Signal summaries
- Graph summaries
- Structured tool outputs

### 2. Explicit State

All execution state is stored in a typed schema (`InfraState`).

- No hidden memory
- State flows node-to-node
- Full checkpointing support

### 3. Graph-Based Execution

Uses LangGraph's `StateGraph` for explicit delegation:

```
scan_cluster → build_graph → generate_signals → compute_risk
    → planner → [agents] → synthesizer → END
```

Delegation = graph traversal.

---

## Hard Limits

To prevent unbounded growth:

- Max 1000 pods
- Max 200 deployments
- Max 200 services
- Max 200 signals
- Max 50 findings per agent

---

## MVP Completion Criteria

✅ End-to-end execution works  
✅ DeepAgent graph delegates properly  
✅ Risk score computed  
✅ Reports generated  
✅ Memory checkpointing works  
✅ < 20 files  
✅ ~1000 LOC  

---

## What's NOT Included (By Design)

This is an MVP. The following are **explicitly out of scope**:

❌ Auto-remediation  
❌ UI/Dashboard  
❌ RAG/Vector databases  
❌ Telemetry/Observability  
❌ Fine-tuning  
❌ Additional agent types  
❌ Docker/Kubernetes deployment (dev-only for MVP)  

---

## License

MIT License - See LICENSE file for details.

---

## Contributing

This is an MVP implementation following strict requirements.  
Feature additions beyond scope will not be accepted.

For bugs or improvements within scope, please open an issue.

---

**Built with:**
- [LangChain](https://github.com/langchain-ai/langchain)
- [LangGraph](https://github.com/langchain-ai/langgraph)
- [Ollama](https://ollama.ai/)
- [Kubernetes Python Client](https://github.com/kubernetes-client/python)
