

# 📄 PRODUCT REQUIREMENTS DOCUMENT

# KubeSentinel — DeepAgent Kubernetes Intelligence Engine (MVP)

---

# 1. PRODUCT DEFINITION

## 1.1 Product Category

Local AI-powered Kubernetes infrastructure intelligence runtime.

## 1.2 Core Definition

KubeSentinel is a **hierarchical, persistent, graph-based multi-agent runtime** built using:

* LangChain
* LangGraph
* Ollama

It:

* Connects to any Kubernetes cluster
* Extracts full static state
* Builds a structural dependency graph
* Generates deterministic signals
* Runs DeepAgents with planner-based delegation
* Produces structured infrastructure intelligence reports
* Runs fully locally
* Is Dockerized and Kubernetes-deployable

This is not a chatbot.
This is a **graph-executed reasoning system**.

---

# 2. ARCHITECTURAL PRINCIPLES

## 2.1 Deterministic Before Generative

All cluster inspection is deterministic.

LLM:

* NEVER connects to cluster
* NEVER receives full raw cluster JSON
* NEVER mutates cluster

LLM only sees:

* Slim snapshot
* Signal summaries
* Graph summary
* Structured tool outputs

---

## 2.2 Explicit State

All execution state is stored in a typed schema.

No hidden memory.

State flows node-to-node.

---

## 2.3 Graph-Based Execution

This system is not built using `create_agent`.

It uses:

DeepAgents-style graph execution:

```
User Input
   ↓
Planner Node
   ↓
Conditional Graph Edges
   ↓
Worker Agent Nodes
   ↓
Synthesis Node
```

Delegation = graph traversal.

---

# 3. SYSTEM ARCHITECTURE

---

# 3.1 High-Level Execution Diagram

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

# 4. STATE SCHEMA (CRITICAL)

File: `models.py`

This defines execution contract.

```python
from typing import TypedDict, List, Dict, Any

class InfraState(TypedDict):
    user_query: str
    
    # Deterministic outputs
    cluster_snapshot: Dict[str, Any]
    graph_summary: Dict[str, Any]
    signals: List[Dict[str, Any]]
    risk_score: Dict[str, Any]
    
    # Agent outputs
    failure_findings: List[Dict[str, Any]]
    cost_findings: List[Dict[str, Any]]
    security_findings: List[Dict[str, Any]]
    
    strategic_summary: str
    final_report: str
```

Rules:

* No nested arbitrary growth.
* No unbounded logs.
* All lists capped.

Checkpoint:

* State schema compiles and is used in graph builder.

STOP WORK HERE until state is stable.

---

# 5. DETERMINISTIC LAYER DESIGN

---

# 5.1 Cluster Snapshot Node

File: `cluster.py`

Function:

```python
def scan_cluster(state: InfraState) -> InfraState:
```

Responsibilities:

* load_kube_config()
* fallback load_incluster_config()
* Fetch:

  * Nodes
  * Pods
  * Deployments
  * Services

Transform to slim structure:

```python
{
  "nodes": [{name, allocatable_cpu, allocatable_mem}],
  "deployments": [{name, replicas, image, resources}],
  "pods": [{name, status, node}],
  "services": [{name, selector}]
}
```

Hard caps:

* Max 1000 pods
* Max 1000 logs lines

Checkpoint:

* JSON prints cleanly
* No LLM involved

STOP until stable.

---

# 5.2 Graph Builder Node

File: `graph.py`

Build simple adjacency dict:

```python
{
  "service_to_deployment": {...},
  "deployment_to_pods": {...},
  "pod_to_node": {...}
}
```

Derived metrics:

* orphan_services
* single_replica_deployments
* node_fanout

Checkpoint:

* Graph summary prints correctly
* No networkx
* No advanced graph logic

STOP.

---

# 5.3 Signal Engine Node

File: `signals.py`

Pure function:

```python
def generate_signals(state: InfraState) -> InfraState:
```

Generate signals:

Reliability:

* CrashLoopBackOff
* Single replica
* Orphan service

Security:

* privileged container
* image:latest
* no resource limits

Cost:

* replicas > 3
* no limits
* no HPA

Signals must be structured:

```python
{
  "category": "security",
  "severity": "high",
  "resource": "deployment/foo",
  "message": "Uses privileged container"
}
```

Checkpoint:

* Signals list correct
* No duplication

STOP.

---

# 5.4 Risk Model Node

File: `risk.py`

Severity weights:

* critical = 15
* high = 8
* medium = 3
* low = 1

Score = min(100, sum(weights))

Grade mapping:
A, B, C, D, F

Checkpoint:

* risk_score stable

STOP.

---

# 6. DEEPAGENT DESIGN

All agents defined in:

`agents.py`

All tools defined in:

`tools.py`

No splitting.

---

# 6.1 LLM Initialization

```python
from langchain_community.chat_models import ChatOllama

llm = ChatOllama(
    model="qwen2.5",
    temperature=0
)
```

---

# 6.2 Deterministic Tools

File: `tools.py`

These are plain Python functions:

* get_cluster_summary
* get_graph_summary
* get_signals
* get_risk_score
* get_pod_logs

These tools:

* Accept InfraState
* Return structured data
* Enforce size caps
* No side effects

Checkpoint:

* Tools callable directly

---

# 6.3 Sub-Agent Nodes

We do NOT use `create_agent`.

We define agent nodes manually using LangGraph node pattern.

Each agent is:

```python
def failure_agent_node(state: InfraState) -> InfraState:
```

Inside:

* Build tool-using ReAct agent
* Feed only:

  * signals
  * graph_summary
* Enforce structured JSON output

Output goes to:
state["failure_findings"]

Repeat for:

* cost_agent_node
* security_agent_node

Checkpoint:

* Each node runs independently

STOP.

---

# 6.4 Planner Node

Planner must:

* Inspect user_query
* Inspect signals
* Decide which agents to run

Example output:

```json
{
  "agents_to_run": ["failure_agent", "security_agent"]
}
```

Planner node writes decision to state.

Must enforce:

* Only allowed agent names
* No arbitrary graph traversal

Checkpoint:

* Planner returns valid list

STOP.

---

# 6.5 Graph Construction

Using LangGraph:

```python
from langgraph.graph import StateGraph

builder = StateGraph(InfraState)
```

Add nodes:

* scan_cluster
* build_graph
* generate_signals
* compute_risk
* planner
* failure_agent
* cost_agent
* security_agent
* synthesizer

Edges:

scan_cluster → build_graph
build_graph → generate_signals
generate_signals → compute_risk
compute_risk → planner

Conditional edges:

planner → selected agents

Each agent → synthesizer

synthesizer → END

Compile with checkpoint:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

memory = SqliteSaver("infra_memory.db")
graph = builder.compile(checkpointer=memory)
```

Checkpoint:

* Graph executes end-to-end
* State persists

STOP.

---

# 7. SYNTHESIS NODE

Final LLM node.

Input:

* failure_findings
* cost_findings
* security_findings
* risk_score

Produces:

* strategic_summary

Must return structured text only.

No hallucination allowed.

Checkpoint:

* Summary coherent
* Deterministic data preserved

STOP.

---

# 8. REPORT BUILDER

File: `reporting.py`

Pure function:

```python
def build_report(state: InfraState) -> str:
```

Sections:

# Architecture Report

# Cost Optimization Report

# Security Audit

# Reliability Risk Score

# Strategic AI Explanation

Writes markdown file.

Checkpoint:

* report.md generated
* No LLM dependency for structure

STOP.

---

# 9. CLI DESIGN

File: `main.py`

Typer CLI:

```
kubesentinel scan
```

Scan runs compiled graph:

```python
engine.invoke({"user_query": "Full cluster analysis"})
```

Checkpoint:

* Single command works end-to-end

STOP.

---

# 10. MAKEFILE

Must include:

* make install
* make run
* make k8s-deploy

Checkpoint:

* make run works

STOP.

---

# 12. KUBERNETES DEPLOYMENT

k8s/deployment.yaml:

* ServiceAccount
* ClusterRole (read-only)
* ClusterRoleBinding
* Deployment

Checkpoint:

* Pod can read cluster
* Scan executes inside cluster

STOP.

---

# 13. MVP COMPLETION CRITERIA

Project is DONE when:

* End-to-end execution works
* DeepAgent graph delegates properly
* Risk score computed
* Reports generated
* Memory checkpointing works
* Docker works
* K8 deploy works
* < 20 files
* < ~1000 LOC

No new features after this.

---

# 14. WHAT NOT TO DO

❌ Add more agents
❌ Add auto remediation
❌ Add UI
❌ Add RAG
❌ Add telemetry
❌ Add more signal types
❌ Add fine-tuning

MVP stops here.
