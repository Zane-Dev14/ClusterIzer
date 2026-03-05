import json
import logging
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

# Agent timeout enforcement (30 seconds per agent)
AGENT_TIMEOUT_SECONDS = 60

class AgentTimeoutError(Exception):
    """Raised when agent exceeds timeout."""
    pass

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
    
    query = state.get("user_query", "").lower()
    
    # Deterministic keyword matching
    agents = []
    
    if "cost" in query:
        agents.append("cost_agent")
    if "security" in query or "secure" in query:
        agents.append("security_agent")
    if "reliability" in query or "failure" in query or "fail" in query:
        agents.append("failure_agent")
    
    # Default to all agents for full analysis
    if not agents or "full" in query or "all" in query or "complete" in query:
        agents = ["failure_agent", "cost_agent", "security_agent"]
    
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
    
    # Rule: Over-provisioned clusters
    over_prov = [s for s in signals if "over-provisioned" in s.get("message", "").lower()]
    if over_prov:
        findings.append({
            "resource": "cluster/sizing",
            "severity": "medium",
            "analysis": "Cluster appears to be over-provisioned with low utilization",
            "recommendation": "Right-size nodes or consolidate workloads to reduce costs"
        })
    
    # Rule: High replica count
    high_replicas = [s for s in signals if "replicas" in s.get("message", "").lower() and "may be over-provisioned" in s.get("message", "")]
    if high_replicas:
        findings.append({
            "resource": "cluster/workload",
            "severity": "low",
            "analysis": f"{len(high_replicas)} deployments may be over-replicated",
            "recommendation": "Review replica counts for cost optimization"
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
    """Run agent with tools and parse JSON findings."""
    system_prompt = (PROMPT_DIR / prompt_file).read_text()
    
    tools = make_tools(state)
    agent = create_agent(LLM, tools, system_prompt=system_prompt)
    signals = state.get("signals", [])
    category_signals = [s for s in signals if s.get("category") == category]
    human_msg = f"""Analyze the {category} signals and provide findings.
Signal count: {len(category_signals)}
Use tools: get_signals(category="{category}"), get_graph_summary(), get_cluster_summary(), get_risk_score()
Return findings as JSON array per your instructions."""
    result = agent.invoke({"messages": [HumanMessage(content=human_msg)]})
    findings = _extract_json_findings(result)
    logger.info(f"{agent_name} produced {len(findings)} findings")
    return findings

def _extract_json_findings(result: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Extract JSON findings array from agent messages."""
    if not result or not (messages := result.get("messages", [])):
        return []
    last_msg = messages[-1]
    content = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    content = str(content) if not isinstance(content, str) else content
    try:
        start, end = content.find('['), content.rfind(']')
        if start != -1 and end != -1 and end > start:
            findings = json.loads(content[start:end+1])
            if isinstance(findings, list):
                return [f for f in findings if isinstance(f, dict) and 
                       all(k in f for k in ["resource", "severity", "analysis", "recommendation"])]
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON: {e}")
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
