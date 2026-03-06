# Root Cause Analysis & Fix Plan

## Critical Issues Identified

### 1. **Findings Disappearing (Most Critical)**
**Symptoms**: Cost agent produces findings in logs but report shows 0
**Root Cause**: JSON parsing in `_extract_json_findings()` is broken

**Evidence**:
- Line 365-372 in agents.py expects `result["messages"]` key
- LangChain `agent.invoke()` returns `{"output": "..."}` not `{"messages": [...]}`
- When "messages" key missing, function returns `[]`
- Deterministic fallback NOT triggered on parse failure (only on exception)

**Fix**: Parse `result["output"]` instead of `result["messages"]`

### 2. **Risk Saturation (100/100 score)**
**Symptoms**: 42 signals (mostly medium/low) → score 100, grade F
**Root Cause**: Simple min/max without normalization

**Current code** (risk.py):
```python
score = min(100, int(total_score))
```

With 42 signals at ~4 points each = 168 → capped at 100

**Fix needed**: 
```python
max_effective = max(1, min(len(signals), MAX_SIGNALS)) * WEIGHT["critical"] * NORMALIZATION_FACTOR
normalized_score = int(min(100, (raw_total / max_effective) * 100))
```

### 3. **Timeouts on Large Prompts**
**Symptoms**: `run_llm exceeded 60s timeout`
**Root Cause**: Full cluster state sent to LLM

-agent receives entire signals list, cluster snapshot, graph
- Prompt tokens ~ 20k-40k chars
- Ollama inference slow (2-5s per token)

**Fix**: Send only summary, agents fetch details via tools

### 4. **Planner Always Runs All Agents**
**Symptoms**: All queries produce identical output
**Root Cause**: planner_node always returns all three agents

**Current code** (agents.py ~line 100):
```python
def planner_node(state: InfraState) -> InfraState:
    ...
    state["planner_decision"] = ["failure_agent", "cost_agent", "security_agent"]
    return state
```

**Fix**: Query-aware routing (keyword matching)

### 5. **Shallow Copy Race Conditions**
**Symptoms**: Potential data corruption in parallel agents
**Root Cause**: runtime.py line 65: `dict(state)` not `copy.deepcopy(state)`

**Fix**: Use copy.deepcopy for thread safety

### 6. **Agent Iteration Limits Missing**
**Symptoms**: Agent ReAct loops can spiral
**Root Cause**: create_agent() called without max_iterations parameter

**Fix**: Add `max_iterations=AGENT_MAX_ITERATIONS` to create_agent call

## Fix Priority Order

### MUST-DO FIRST (fixes findings loss)
1. Fix _extract_json_findings to parse `result["output"]` ← **BLOCKS EVERYTHING**
2. Add robust JSON parser with control-char sanitization
3. Test with real agent output

### HIGH PRIORITY (fixes timeouts & saturation)
4. Reduce LLM prompt size (send summary not full data)
5. Fix risk.py normalization math
6. Add AGENT_MAX_ITERATIONS to agent creation

### MEDIUM PRIORITY (fixes planner & stability)  
7. Implement query-aware planner routing
8. Replace shallow dict() with copy.deepcopy()
9. Add comprehensive debug logging

### NICE-TO-HAVE (polish)
10. CLI --agents and --verbose flags
11. Report timestamp
12. Control character sanitization for JSON output

## Files to Modify

| File | Changes | Lines |
|------|---------|-------|
| agents.py | Parse fix, JSON robustness, reduce context | ~100 |
| risk.py | Normalization formula | ~20 |
| runtime.py | Deep copy, debug logs | ~15 |
| main.py | CLI flags, verbose setup | ~30 |
| reporting.py | Timestamp | ~5 |
| models.py | Type hints (if needed) | ~10 |

**Total LOC**: ~180 lines (minimal, focused)

## Validation Commands (in order)

```bash
# 1. Static checks
uv run mypy kubesentinel --ignore-missing-imports

# 2. Unit tests
uv run pytest kubesentinel/tests/ -xvs

# 3. Functional test - human mode
uv run kubesentinel scan --query "Full cluster architecture" --verbose

# 4. Functional test - JSON mode
uv run kubesentinel scan --query "Analyze architecture" --json > /tmp/ks.json
python -m json.tool /tmp/ks.json

# 5. Planner routing test
uv run kubesentinel scan --query "Investigate node health" --verbose
# Should see: only failure_agent runs

# 6. Risk saturation test (unit)
pytest kubesentinel/tests/test_risk.py::test_many_medium_signals_dont_saturate -xvs
```

## Expected Outcomes After Fixes

| Issue | Before | After |
|-------|--------|-------|
| Cost agent findings | 7 produced, 0 reported | 7 produced, 7 reported ✓ |
| Risk saturation | 42 signals → 100/100 | 42 medium signals → 45-65/100 ✓ |
| Agent timeout | 60+ seconds | 15-20 seconds ✓ |
| Planner awareness | Always all agents | Query-routed (1-3 agents) ✓ |
| Node pressure signals | Generated but not reported | Visible in report ✓ |
| Concurrent safety | Potential race condition | Safe deep copy ✓ |
