# KubeSentinel

**Kubernetes Intelligence Engine**

A hierarchical, persistent, graph-based multi-agent runtime for Kubernetes infrastructure analysis using LangChain, LangGraph, and Ollama.

---

## Overview

KubeSentinel is a **deterministic-first AI system** that:

1. Connects to any Kubernetes cluster (via kubeconfig or in-cluster)
2. Extracts bounded static state (nodes, deployments, pods, services)
3. Builds a dependency graph and generates deterministic signals
4. Computes risk scores using severity-weighted signal aggregation
5. Delegates analysis to specialized ReAct agents (failure, cost, security)
6. Produces comprehensive markdown reports with strategic AI insights

This is **not a chatbot**. It's a graph-executed reasoning system.

---

## Architecture

```
┌──────────────────────┐
│ CLI (scan command)   │
└─────────────┬────────┘
              ↓
┌─────────────────────────────┐
│ Deterministic Layer         │
│                             │
│ 1. Cluster Snapshot Node    │
│ 2. Graph Builder Node       │
│ 3. Signal Engine Node       │
│ 4. Risk Model Node          │
└─────────────┬───────────────┘
              ↓
┌─────────────────────────────┐
│ DeepAgent Graph             │
│                             │
│ Planner Node                │
│   ├─ FailureAgent Node      │
│   ├─ CostAgent Node         │
│   ├─ SecurityAgent Node     │
│   └─ StrategicSynthesizer   │
└─────────────┬───────────────┘
              ↓
┌─────────────────────────────┐
│ Report Builder              │
└─────────────────────────────┘
```

---

## Prerequisites

- **Python 3.11+**
- **uv** package manager ([install uv](https://github.com/astral-sh/uv))
- **Ollama** with `llama3.1:8b-instruct-q8_0` model
- Access to a Kubernetes cluster (kubeconfig or in-cluster)

---

## Installation

```bash
# Install dependencies
make install

# Or using uv directly
uv sync
```

---

## Usage

### Basic Scan

```bash
# Run full cluster analysis
make run

# Or using uv directly
uv run kubesentinel scan
```

### Custom Query

```bash
# Focus on specific analysis
uv run kubesentinel scan --query "security audit"
uv run kubesentinel scan --query "cost optimization"
uv run kubesentinel scan --query "reliability analysis"
```

### Verbose Mode

```bash
uv run kubesentinel scan --verbose
```

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
