import json
import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any
from functools import wraps
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain.agents import create_agent

from .models import InfraState, MAX_FINDINGS

logger = logging.getLogger(__name__)

# Initialize LLM
LLM = ChatOllama(
    model="llama3.1:8b-instruct-q8_0",
    temperature=0
)

PROMPT_DIR = Path(__file__).parent / "prompts"

# Agent configuration constants
AGENT_TIMEOUT_SECONDS = 60
AGENT_MAX_ITERATIONS = 8
AGENT_TOOL_SIGNAL_LIMIT = 30
VERBOSE = os.getenv("KUBESENTINEL_VERBOSE_AGENTS") == "1"

class AgentTimeoutError(Exception):
    """Raised when agent exceeds timeout."""
    pass

def _sanitize_for_json(text: str) -> str:
    """Remove control characters from text for JSON safety."""
    if not isinstance(text, str):
        return str(text)
    # Remove control chars (ord < 32) except \n, \r, \t
    return ''.join(c if ord(c) >= 32 or c in '\n\r\t' else ' ' for c in text)

def with_timeout(seconds: int):
    """Decorator to enforce timeout in a thread-safe way."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(func, *args, **kwargs)
                try:
                    return future.result(timeout=seconds)
                except FuturesTimeoutError:
                    logger.error(f"{func.__name__} exceeded {seconds}s timeout")
                    future.cancel()
                    raise AgentTimeoutError("Agent execution timeout")
        return wrapper
    return decorator

def make_tools(state: InfraState) -> List:
    """Create tools that capture state in closures."""
    @tool
    def get_cluster_summary() -> str:
        """Get high-level cluster summary with resource counts.
        
        Returns: JSON with node count, deployment count, pod count, service count, and namespaces.
        Use this to understand cluster scale before diving into specific issues.
        """
        snap = state.get("cluster_snapshot", {})
        nodes, deployments, pods, services = snap.get("nodes", []), snap.get("deployments", []), snap.get("pods", []), snap.get("services", [])
        ns = set()
        for dep in deployments + pods + services:
            ns.add(dep.get("namespace", "default"))
        return json.dumps({"nodes": len(nodes), "deployments": len(deployments), "pods": len(pods), "services": len(services), "namespaces": sorted(ns)})
    
    @tool
    def get_graph_summary() -> str:
        """Get dependency graph analysis results.
        
        Returns: JSON with orphan_services (no backend), single_replica deployments (no redundancy),
        and service count. Use to identify architectural risks like missing backends or single points of failure.
        """
        g = state.get("graph_summary", {})
        return json.dumps({"orphan_services": g.get("orphan_services", []), "single_replica": g.get("single_replica_deployments", []), "services": len(g.get("service_to_deployment", {}))})
    
    @tool
    def get_signals(category: str = "") -> str:
        """Get detected signals, optionally filtered by category.
        
        Args:
            category: Optional filter - "reliability", "cost", or "security". Empty string returns all.
        
        Returns: JSON array of signals (up to 50). Each signal has category, severity, resource, message, and cis_control.
        Use to understand specific issues detected by the scanner.
        """
        sigs = state.get("signals", [])
        if category:
            sigs = [s for s in sigs if s.get("category") == category]
        return json.dumps(sigs[:50])
    
    @tool
    def get_risk_score() -> str:
        """Get overall cluster risk assessment.
        
        Returns: JSON with score (0-100), grade (A-F), signal_count, category_breakdown, and confidence.
        Use to understand overall cluster health and prioritize which areas need attention.
        """
        return json.dumps(state.get("risk_score", {}))
    
    return [get_cluster_summary, get_graph_summary, get_signals, get_risk_score]

def planner_node(state: InfraState) -> InfraState:
    """Deterministic planner that decides which agents to run based on query keywords."""
    logger.info("Planning agent execution...")
    
    # Check for CLI override first
    if state.get("planner_decision"):
        logger.info(f"Planner using CLI override: {state.get('planner_decision')}")
        return state
    
    query = state.get("user_query", "").lower()
    
    # Extract tokens (words >= 3 chars)
    tokens = set(re.findall(r'\b[a-z]{3,}\b', query))
    if VERBOSE:
        logger.debug(f"Planner tokens: {tokens}")
    
    agents = []
    
    # Architecture queries explicitly request all agents
    architecture_keywords = {"full", "all", "complete", "architecture", "deep", "comprehensive"}
    if any(w in tokens for w in architecture_keywords):
        agents = ["failure_agent", "cost_agent", "security_agent"]
        logger.info(f"Planner selected agents: {agents} (architecture query)")
        state["planner_decision"] = agents
        return state
    
    # Cost routing
    cost_keywords = {"cost", "costs", "spend", "spending", "bill", "billing", "price", "pricing", 
                     "budget", "optimization", "optimize", "reduce", "save", "saving", "savings", "waste"}
    if any(w in tokens for w in cost_keywords):
        agents.append("cost_agent")
    
    # Security routing
    if any(w in tokens for w in ("security", "secure", "vuln", "cve", "cis", "privilege", "audit")):
        agents.append("security_agent")
    
    # Reliability routing
    reliability_keywords = {"reliability", "failure", "fail", "outage", "replica", "redundancy", "health", "pressure"}
    if any(w in tokens for w in reliability_keywords):
        agents.append("failure_agent")
    
    # Node-related queries also trigger failure agent (node pressure)
    if any(w in tokens for w in ("node", "memory", "disk", "pressure", "capacity")):
        if "failure_agent" not in agents:
            agents.append("failure_agent")
    
    # If no specific routing matched, default to failure_agent for generic queries
    if not agents:
        logger.warning(f"No specific agent routing for query: '{query}' - defaulting to failure_agent")
        agents = ["failure_agent"]
    
    # Deduplicate while preserving order
    seen = set()
    unique_agents = []
    for agent in agents:
        if agent not in seen:
            seen.add(agent)
            unique_agents.append(agent)
    
    logger.info(f"Planner selected agents: {unique_agents}")
    state["planner_decision"] = unique_agents
    return state

def failure_agent_node(state: InfraState) -> InfraState:
    """Reliability analysis agent - analyzes failure signals."""
    if "failure_agent" not in state.get("planner_decision", []):
        logger.info("Skipping failure_agent")
        state["failure_findings"] = []
        return state
    logger.info("Running failure_agent...")
    
    # Deterministic pre-check
    deterministic_findings = _deterministic_failure_check(state)
    if deterministic_findings:
        state["failure_findings"] = deterministic_findings[:MAX_FINDINGS]
        return state
    
    # Fallback to LLM
    try:
        @with_timeout(AGENT_TIMEOUT_SECONDS)
        def run_llm():
            return _run_agent(state, "failure_agent", "failure_agent.txt", "reliability")
        
        findings = run_llm()
        state["failure_findings"] = findings[:MAX_FINDINGS]
    except AgentTimeoutError:
        logger.warning("Failure agent timeout - using deterministic fallback")
        state["failure_findings"] = deterministic_findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Failure agent error: {e}")
        state["failure_findings"] = deterministic_findings[:MAX_FINDINGS]
    return state

def cost_agent_node(state: InfraState) -> InfraState:
    """Cost optimization agent - analyzes cost signals."""
    if "cost_agent" not in state.get("planner_decision", []):
        logger.info("Skipping cost_agent")
        state["cost_findings"] = []
        return state
    logger.info("Running cost_agent...")
    
    # Deterministic pre-check
    deterministic_findings = _deterministic_cost_check(state)
    if deterministic_findings:
        state["cost_findings"] = deterministic_findings[:MAX_FINDINGS]
        return state
    
    # Fallback to LLM
    try:
        @with_timeout(AGENT_TIMEOUT_SECONDS)
        def run_llm():
            return _run_agent(state, "cost_agent", "cost_agent.txt", "cost")
        
        findings = run_llm()
        state["cost_findings"] = findings[:MAX_FINDINGS]
    except AgentTimeoutError:
        logger.warning("Cost agent timeout - using deterministic fallback")
        state["cost_findings"] = deterministic_findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Cost agent error: {e}")
        state["cost_findings"] = deterministic_findings[:MAX_FINDINGS]
    return state

def security_agent_node(state: InfraState) -> InfraState:
    """Security audit agent - analyzes security signals."""
    if "security_agent" not in state.get("planner_decision", []):
        logger.info("Skipping security_agent")
        state["security_findings"] = []
        return state
    logger.info("Running security_agent...")
    
    # Deterministic pre-check
    deterministic_findings = _deterministic_security_check(state)
    if deterministic_findings:
        state["security_findings"] = deterministic_findings[:MAX_FINDINGS]
        return state
    
    # Fallback to LLM
    try:
        @with_timeout(AGENT_TIMEOUT_SECONDS)
        def run_llm():
            return _run_agent(state, "security_agent", "security_agent.txt", "security")
        
        findings = run_llm()
        state["security_findings"] = findings[:MAX_FINDINGS]
    except AgentTimeoutError:
        logger.warning("Security agent timeout - using deterministic fallback")
        state["security_findings"] = deterministic_findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Security agent error: {e}")
        state["security_findings"] = deterministic_findings[:MAX_FINDINGS]
    return state

def _deterministic_failure_check(state: InfraState) -> List[Dict[str, Any]]:
    """Deterministic reliability check (rules-based, no LLM)."""
    findings = []
    signals = state.get("signals", [])
    risk = state.get("risk_score", {})
    
    # Rule: Single replica deployments are high risk
    single_replicas = [s for s in signals if "replica" in s.get("message", "").lower() and "1" in s.get("message", "")]
    if single_replicas:
        findings.append({
            "resource": "cluster/redundancy",
            "severity": "high",
            "analysis": f"Found {len(single_replicas)} deployments with only 1 replica",
            "recommendation": "Increase replica count to 3+ for production workloads"
        })
    
    # Rule: CrashLoopBackOff is critical
    crash_signals = [s for s in signals if "CrashLoopBackOff" in s.get("message", "")]
    if crash_signals:
        findings.append({
            "resource": "cluster/health",
            "severity": "critical",
            "analysis": f"{len(crash_signals)} pods in CrashLoopBackOff state",
            "recommendation": "Investigate pod logs and deployment specifications immediately"
        })
    
    # Rule: High risk score requires immediate action
    if risk.get("score", 0) > 80:
        findings.append({
            "resource": "cluster/risk",
            "severity": "critical",
            "analysis": f"Cluster risk score is {risk.get('score', 0)}/100 ({risk.get('grade', 'F')})",
            "recommendation": "Address critical and high-severity signals immediately"
        })
    
    return findings

def _deterministic_cost_check(state: InfraState) -> List[Dict[str, Any]]:
    """Deterministic cost check (rules-based, no LLM)."""
    findings = []
    signals = state.get("signals", [])
    graph = state.get("graph_summary", {})
    snapshot = state.get("cluster_snapshot", {})
    
    # Rule 1: Single replica deployments → cost inefficiency
    single_replicas = graph.get("single_replica_deployments", [])
    if single_replicas and len(single_replicas) > 3:
        findings.append({
            "resource": "cluster/deployments",
            "severity": "medium",
            "analysis": f"{len(single_replicas)} deployments run with single replica (inefficient resource usage)",
            "recommendation": "Consolidate single-replica workloads or enable horizontal pod autoscaling to improve node utilization"
        })
    
    # Rule 2: Nodes under 30% utilization → cost waste
    nodes = snapshot.get("nodes", [])
    pods = snapshot.get("pods", [])
    underutilized_nodes = []
    for node in nodes:
        node_name = node.get("name")
        node_pods = [p for p in pods if p.get("node_name") == node_name]
        
        # Estimate utilization from requested resources
        node_cpu_str = node.get("cpu", "0")
        node_cpu = float(node_cpu_str.rstrip("m")) if "m" in node_cpu_str else float(node_cpu_str) * 1000
        
        total_requested_cpu = 0
        for pod in node_pods:
            for container in pod.get("containers", []):
                cpu_req = container.get("resources", {}).get("requests", {}).get("cpu", "0")
                if cpu_req:
                    cpu_val = float(cpu_req.rstrip("m")) if "m" in cpu_req else float(cpu_req) * 1000
                    total_requested_cpu += cpu_val
        
        if node_cpu > 0:
            utilization = (total_requested_cpu / node_cpu) * 100
            if utilization < 30 and len(node_pods) > 0:  # Only flag if node has workloads
                underutilized_nodes.append({"name": node_name, "utilization": utilization})
    
    if underutilized_nodes:
        findings.append({
            "resource": "cluster/nodes",
            "severity": "high",
            "analysis": f"{len(underutilized_nodes)} nodes are under 30% CPU utilization (wasted capacity)",
            "recommendation": "Consider draining and removing underutilized nodes, or consolidating workloads to fewer nodes"
        })
    
    # Rule 3: Workloads without HPA → scaling inefficiency
    deployments = snapshot.get("deployments", [])
    # Note: HPA detection requires HPA resources in cluster scan (future enhancement)
    # For now, detect fixed replica counts > 1 as potential HPA candidates
    hpa_candidates = [d for d in deployments if d.get("replicas", 0) > 1 and d.get("replicas", 0) < 10]
    if len(hpa_candidates) > 5:
        findings.append({
            "resource": "cluster/autoscaling",
            "severity": "low",
            "analysis": f"{len(hpa_candidates)} deployments with fixed replica counts could benefit from autoscaling",
            "recommendation": "Enable HorizontalPodAutoscaler (HPA) for workloads with variable load patterns"
        })
    
    # Rule 4: Over-requested CPU (detected via signals)
    over_requested = [s for s in signals if "over-requested" in s.get("message", "").lower() or "over-provisioned" in s.get("message", "").lower()]
    if over_requested:
        findings.append({
            "resource": "cluster/resources",
            "severity": "medium",
            "analysis": f"{len(over_requested)} containers have resource requests significantly exceeding usage",
            "recommendation": "Right-size CPU/memory requests based on actual usage patterns (use VPA or monitoring data)"
        })
    
    return findings

def _deterministic_security_check(state: InfraState) -> List[Dict[str, Any]]:
    """Deterministic security check (rules-based, no LLM)."""
    findings = []
    signals = state.get("signals", [])
    
    # Rule: Privileged containers are critical
    privileged = [s for s in signals if "privileged mode" in s.get("message", "").lower()]
    if privileged:
        findings.append({
            "resource": "cluster/containers",
            "severity": "critical",
            "analysis": f"{len(privileged)} containers run in privileged mode (CIS 5.2.1)",
            "recommendation": "Remove privileged mode; use specific capabilities if needed"
        })
    
    # Rule: Latest image tags
    latest_images = [s for s in signals if "latest" in s.get("message", "").lower() or "untagged" in s.get("message", "").lower()]
    if latest_images:
        findings.append({
            "resource": "cluster/images",
            "severity": "high",
            "analysis": f"{len(latest_images)} containers use :latest or untagged images (CIS 5.4.1)",
            "recommendation": "Pin all containers to specific immutable image tags"
        })
    
    # Rule: Missing resource limits
    no_limits = [s for s in signals if "no resource limits" in s.get("message", "").lower()]
    if no_limits:
        findings.append({
            "resource": "cluster/resources",
            "severity": "medium",
            "analysis": f"{len(no_limits)} containers lack resource limits (CIS 5.2.12)",
            "recommendation": "Define CPU and memory requests/limits for all containers"
        })
    
    return findings

def _run_agent(state: InfraState, agent_name: str, prompt_file: str, category: str) -> List[Dict[str, Any]]:
    """Run agent with tools and parse JSON findings.
    
    Uses max_iterations to prevent infinite loops and sends compact prompt to reduce tokens.
    """
    system_prompt = (PROMPT_DIR / prompt_file).read_text()
    
    tools = make_tools(state)
    agent = create_agent(
        LLM,
        tools,
        system_prompt=system_prompt
    )
    
    signals = state.get("signals", [])
    category_signals = [s for s in signals if s.get("category") == category]
    
    # Create compact context (don't send full signals)
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for s in category_signals:
        severity_counts[s.get("severity", "low")] += 1
    
    risk_score = state.get("risk_score", {})
    graph = state.get("graph_summary", {})
    logger.debug(f"Graph Summary: {graph}")
    
    # Compact prompt that directs agent to use tools
    human_msg = (
        f"Analyze {len(category_signals)} {category} signals and provide findings.\n"
        f"Severity breakdown: {severity_counts['critical']} critical, {severity_counts['high']} high, "
        f"{severity_counts['medium']} medium, {severity_counts['low']} low.\n"
        f"Risk score: {risk_score.get('score', 0)}/100 grade {risk_score.get('grade', 'N/A')}. "
        f"Use tools (get_signals, get_cluster_summary, get_graph_summary) to fetch details. "
        f"Return valid JSON array with format: [{{'resource': '...', 'severity': '...', 'analysis': '...', 'recommendation': '...'}}]"
    )
    
    if VERBOSE:
        logger.debug(f"Running {agent_name} agent, prompt_size={len(human_msg)} chars")
    
    result = agent.invoke({"messages": [HumanMessage(content=human_msg)]})
    findings = _extract_json_findings(result)
    
    if VERBOSE:
        logger.debug(f"{agent_name} returned {len(findings)} findings from {len(category_signals)} signals")
    else:
        logger.info(f"{agent_name} produced {len(findings)} findings")
    
    return findings

def _extract_json_findings(result: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Extract JSON findings array from agent output.
    
    Handles agent.invoke() result structure: {"output": "...JSON..."}
    Also handles markdown-fenced JSON: `` `json` {...} ` ``
    """
    if not result:
        return []
    
    # Agent.invoke() returns {"output": "..."} not {"messages": [...]} 
    content = result.get("output", "")
    if not content:
        return []
    
    content = str(content) if not isinstance(content, str) else content
    
    # Sanitize control characters
    content = _sanitize_for_json(content)
    
    # Try to extract JSON - look for markdown fence first
    if "```json" in content or "```" in content:
        # Extract content between fences
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
        if match:
            content = match.group(1).strip()
    
    try:
        # Try direct JSON parse first
        findings = json.loads(content)
        if isinstance(findings, list):
            # Validate structure
            valid = [f for f in findings if isinstance(f, dict) and 
                    all(k in f for k in ["resource", "severity", "analysis", "recommendation"])]
            if valid and VERBOSE:
                logger.debug(f"Extracted {len(valid)} findings from agent output")
            return valid
    except json.JSONDecodeError as e:
        # Fallback: look for JSON array brackets
        start, end = content.find('['), content.rfind(']')
        if start != -1 and end != -1 and end > start:
            try:
                findings = json.loads(content[start:end+1])
                if isinstance(findings, list):
                    valid = [f for f in findings if isinstance(f, dict) and 
                            all(k in f for k in ["resource", "severity", "analysis", "recommendation"])]
                    if valid:
                        if VERBOSE:
                            logger.debug(f"Extracted {len(valid)} findings from bracketed JSON")
                        return valid
            except json.JSONDecodeError:
                pass
        
        if VERBOSE:
            logger.warning(f"Failed to parse JSON findings: {str(e)[:100]}")
        logger.debug(f"Raw agent output (first 500 chars): {content[:500]}")
    
    return []

def synthesizer_node(state: InfraState) -> InfraState:
    """Strategic synthesis agent - produces executive summary."""
    logger.info("Running synthesizer...")
    system_prompt = (PROMPT_DIR / "synthesizer.txt").read_text()
    failure, cost, security = state.get("failure_findings", []), state.get("cost_findings", []), state.get("security_findings", [])
    risk = state.get("risk_score", {})
    context = f"""Risk: {risk.get('score', 0)}/100 (Grade: {risk.get('grade', 'N/A')}), Signals: {risk.get('signal_count', 0)}
Failure: {len(failure)} - {json.dumps(failure[:10], indent=2)}
Cost: {len(cost)} - {json.dumps(cost[:10], indent=2)}
Security: {len(security)} - {json.dumps(security[:10], indent=2)}
Produce strategic summary per your instructions."""
    try:
        response = LLM.invoke([SystemMessage(content=system_prompt), HumanMessage(content=context)])
        summary = response.content if hasattr(response, 'content') else str(response)
        summary = str(summary) if not isinstance(summary, str) else summary
        state["strategic_summary"] = summary[:4000] + "\n[Summary truncated]" if len(summary) > 4000 else summary
        logger.info("Synthesizer complete")
    except Exception as e:
        logger.error(f"Synthesizer error: {e}")
        state["strategic_summary"] = "Error generating strategic summary."
    return state
