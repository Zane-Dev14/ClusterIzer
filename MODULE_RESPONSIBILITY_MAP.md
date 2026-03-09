# KubeSentinel Module Responsibility Map

## Core Runtime Modules

### 1. **agents.py** (1,218 lines) ✅ REFACTORED
**Responsibility:** Execute analysis agents

**Functions:**
- `planner_node(state)` - Select which agents to run based on query
- `failure_agent_node(state)` - Detect reliability issues
- `cost_agent_node(state)` - Find cost optimization opportunities  
- `security_agent_node(state)` - Identify security risks

**Helper Functions:**
- `make_tools(state)` - Create kubectl tools for agents
- `_run_agent(state, agent_name, prompt, category)` - Common agent execution
- `_extract_json_findings(response_text)` - Parse agent output
- `_validate_findings(findings)` - Verify finding structure
- `_verify_findings_with_evidence(findings, cluster_data)` - Cross-check with evidence
- `_deterministic_*_check(state)` - Fallback analyses (no LLM required)
- `with_timeout(seconds)` - Execution timeout wrapper

**Does NOT contain:**
- ❌ Synthesis logic
- ❌ Report generation
- ❌ Strategic summary construction

---

### 2. **synthesizer.py** (335 lines) 🆕 NEW MODULE
**Responsibility:** Format agent findings into executive summaries

**Functions:**
- `synthesizer_node(state)` - Main synthesis orchestrator
  - Normalizes findings to standard structure
  - Calls strategic summary generator
  - Returns state with `strategic_summary` field

- `synthesize_strategic_summary(state)` - Deterministic summary
  - Generates structured Markdown summary
  - Lists critical findings
  - Provides recommendations
  - No LLM required (fast, reliable)

- `ensure_remediation_field(findings)` - Normalize findings
  - Adds `remediation` field to each finding
  - Prefers deterministic fixes from signals
  - Phase N normalization

**Does NOT contain:**
- ❌ Individual agent logic
- ❌ Risk computation
- ❌ Signal generation

---

### 3. **runtime.py** (285 lines) ✅ ENHANCED
**Responsibility:** Orchestrate pipeline execution with tracing

**Functions:**
- `run_engine(query, namespace, agents, git_repo)` - Entry point
  - Initializes state
  - Invokes graph
  - SAVES EXECUTION TRACE (automatic)
  - GENERATES MERMAID DIAGRAM (automatic)
  - Logs execution summary

- `build_runtime_graph()` - Construct LangGraph
  - Creates execution pipeline
  - Wraps nodes with tracing
  - Sets up edges and entry point

- `get_graph()` - Lazy-load LangGraph

- `build_runtime_graph()` helpers:
  - `traced_node(func, name)` - Wrapper that records timing

- `run_agents_parallel(state)` - Execute agents concurrently

- `persist_snapshot(state)` - Save cluster state

- `load_desired_state(state)` - Load Git desired state

- `get_persistence_manager()` - Manage snapshots

**New Integration:**
- ✨ Automatic runtime tracing
- ✨ Mermaid graph generation
- ✨ Execution timeline capture
- ✨ Performance metrics

---

### 4. **runtime_tracer.py** (165 lines) 🆕 NEW MODULE
**Responsibility:** Automatic execution tracing and visualization

**Classes:**
- `ExecutionTracer` - Records execution flow
  - `enter_node(name)` - Record node entry
  - `exit_node(name, summary)` - Record exit with timing
  - `log_state_change(key, value)` - Track mutations
  - `generate_mermaid_graph()` - Create Mermaid diagram
  - `save_trace()` - Save JSON trace file
  - `save_graph()` - Save Mermaid diagram file

**Functions:**
- `get_tracer()` - Get global tracer instance
- `reset_tracer()` - Clear tracer for new execution

**Output Files (automatic):**
- `runtime_traces/runtime_trace_TIMESTAMP.json` - Full execution timeline
- `runtime_traces/runtime_graph_TIMESTAMP.mmd` - Mermaid diagram

---

## Supporting Modules

### cluster.py (566 lines)
**Responsibility:** Scan Kubernetes cluster state

**Input:** Kubernetes API access  
**Output:** `cluster_snapshot` in state

---

### signals.py (666 lines)
**Responsibility:** Generate risk signals from cluster state

**Input:** `cluster_snapshot`  
**Output:** `signals` list in state

---

### graph_builder.py (410 lines)
**Responsibility:** Build cluster resource graph

**Input:** `cluster_snapshot`  
**Output:** `graph_summary` in state

---

### risk.py (339 lines)
**Responsibility:** Compute risk scores

**Input:** `signals`, `failure_findings`, `cost_findings`, `security_findings`  
**Output:** `risk_score` in state

---

### reporting.py (260 lines)
**Responsibility:** Generate final reports

**Input:** InfraState with all findings  
**Output:** `final_report` field

**Used by:** slack_bot.py formatting

---

### models.py (53 lines)
**Responsibility:** Type definitions

**Contains:** InfraState TypedDict, constants

---

### persistence.py (855 lines)
**Responsibility:** Snapshot storage and drift detection

**Used by:** `persist_snapshot()` node in runtime

---

### git_loader.py (217 lines)
**Responsibility:** Load desired state from Git

**Used by:** `load_desired_state()` node in runtime

---

### diagnostics/ (681 lines)
**Responsibility:** Utility functions for cluster diagnostics

**Used by:** cluster.py for pod logs etc.

---

### crd_discovery.py (339 lines)
**Responsibility:** Discover Custom Resource Definitions

**Used by:** cluster.py for CRD enumeration

---

### integrations/slack_bot.py (938 lines)
**Responsibility:** Slack Socket Mode integration

**Functions:**
- `main()` - Slack bot entry point
- `run_analysis(query)` - Trigger engine
- `format_summary(state)` - Format for Slack
- `safe_kubectl_command()` - Validate/execute kubectl
- Slack event handlers (mentions, messages)

---

## Execution Flow (Correct Design)

```
SLACK EVENT
  ↓
slack_bot.py::run_analysis()
  ↓
runtime.run_engine()
  ├─ Reset tracer
  ├─ Initialize state
  └─ Invoke LangGraph
      │
      ├─ [scan_cluster] → trace entry/exit
      ├─ [load_desired_state] → trace
      ├─ [build_graph] → trace
      ├─ [generate_signals] → trace
      ├─ [persist_snapshot] → trace
      ├─ [compute_risk] → trace
      ├─ [planner] → agents.py::planner_node() → trace
      ├─ [run_agents_parallel] → agents.py::* → trace
      │   ├─ failure_agent_node()
      │   ├─ cost_agent_node()
      │   └─ security_agent_node()
      └─ [synthesizer] → synthesizer.py::synthesizer_node() → trace
          ├─ ensure_remediation_field()
          └─ synthesize_strategic_summary()
      
      ↓ [TRACES COMPLETE]
      
      Auto-save: runtime_trace_*.json
      Auto-save: runtime_graph_*.mmd
      Auto-log: Execution summary with timings
      
      ↓
reporting.build_report()
  ↓
slack_bot.py::format_summary()
  ↓
Slack Reply + Action Buttons
  ↓
DONE
```

---

## Key Improvements

### ✅ Separation of Concerns
- **agents.py**: Only agent execution
- **synthesizer.py**: Only synthesis/post-processing
- **runtime.py**: Only orchestration
- **runtime_tracer.py**: Only execution tracking

### ✅ Clear Responsibilities
Each module has ONE clear job, not multiple overlapping concerns.

### ✅ Debuggability [NEW]
- Every execution generates a trace
- Mermaid diagrams show actual execution path
- JSON traces provide audit trail
- Timing data identifies bottlenecks

### ✅ Testability
- Modules can be tested independently
- Synthesis can be mocked (deterministic)
- Agents can be mocked
- Tracer doesn't interfere with logic

### ✅ Maintainability
- No circular dependencies
- Clear module boundaries
- Self contained functionality

---

## Module Graph

```
slack_bot
  └→ runtime
      └→ build_runtime_graph
          ├→ agents (planner, failure, cost, security)
          ├→ synthesizer [EXTRACTED]
          ├→ runtime_tracer [NEW]
          ├→ cluster
          ├→ signals
          ├→ graph_builder
          ├→ risk
          ├→ persistence
          └→ git_loader
          
reporting
  └→ synthesizer (for finding normalization)
```

---

## File Counts

| File | Lines | Functions |
|------|-------|-----------|
| agents.py | 1,218 | 15 |
| synthesizer.py | 335 | 3 |
| runtime.py | 285 | 7 |
| runtime_tracer.py | 165 | 6 |
| **Subtotal** | **2,003** | **31** |
| cluster.py | 566 | 11 |
| signals.py | 666 | 14 |
| graph_builder.py | 410 | 7 |
| risk.py | 339 | 4 |
| persistence.py | 855 | 37 |
| reporting.py | 260 | 8 |
| git_loader.py | 217 | 7 |
| crd_discovery.py | 339 | 5 |
| diagnostics/ | 681 | many |
| models.py | 53 | 0 |
| slack_bot.py | 938 | 13 |
| **TOTAL** | **8,724** | **100+** |

---

**Status:** ✅ Architecture refactored, modules extracted, tracing integrated, execution validated.
