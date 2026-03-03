"""Agent nodes - planner, specialized agents, and synthesizer."""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_agent

from .models import InfraState, MAX_FINDINGS
from .tools import make_tools

logger = logging.getLogger(__name__)

# Initialize LLM
LLM = ChatOllama(
    model="llama3.1:8b-instruct-q8_0",
    temperature=0
)

PROMPT_DIR = Path(__file__).parent / "prompts"

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
    try:
        findings = _run_agent(state, "failure_agent", "failure_agent.txt", "reliability")
        state["failure_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Failure agent error: {e}")
        state["failure_findings"] = []
    return state


def cost_agent_node(state: InfraState) -> InfraState:
    """Cost optimization agent - analyzes cost signals."""
    if "cost_agent" not in state.get("planner_decision", []):
        logger.info("Skipping cost_agent")
        state["cost_findings"] = []
        return state
    logger.info("Running cost_agent...")
    try:
        findings = _run_agent(state, "cost_agent", "cost_agent.txt", "cost")
        state["cost_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Cost agent error: {e}")
        state["cost_findings"] = []
    return state


def security_agent_node(state: InfraState) -> InfraState:
    """Security audit agent - analyzes security signals."""
    if "security_agent" not in state.get("planner_decision", []):
        logger.info("Skipping security_agent")
        state["security_findings"] = []
        return state
    logger.info("Running security_agent...")
    try:
        findings = _run_agent(state, "security_agent", "security_agent.txt", "security")
        state["security_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Security agent error: {e}")
        state["security_findings"] = []
    return state


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
