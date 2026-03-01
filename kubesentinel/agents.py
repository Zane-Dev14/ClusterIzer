"""
Agent nodes - planner, specialized agents, and synthesizer.

All agents in this file. Uses create_react_agent from langgraph.prebuilt
for the sub-agents. Planner is deterministic.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from langchain_community.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from .models import InfraState, MAX_FINDINGS
from .tools import make_tools

logger = logging.getLogger(__name__)

# Initialize LLM
LLM = ChatOllama(
    model="llama3.1:8b-instruct-q8_0",
    temperature=0
)

# Prompt directory
PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load prompt from file."""
    path = PROMPT_DIR / filename
    return path.read_text()


def planner_node(state: InfraState) -> InfraState:
    """
    Deterministic planner that decides which agents to run.
    
    Inspects user_query and signals to determine relevant agents.
    This is NOT an LLM call - it's keyword-based routing.
    
    Args:
        state: InfraState with user_query and signals
        
    Returns:
        Updated state with planner_decision set
    """
    logger.info("Planning agent execution...")
    
    query = state["user_query"].lower()
    
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
    """
    Reliability and failure analysis agent.
    
    Uses create_react_agent to analyze reliability signals with tools.
    Skips execution if not selected by planner.
    
    Args:
        state: InfraState with deterministic data populated
        
    Returns:
        Updated state with failure_findings
    """
    # Check if this agent should run
    if "failure_agent" not in state.get("planner_decision", []):
        logger.info("Skipping failure_agent (not selected by planner)")
        state["failure_findings"] = []
        return state
    
    logger.info("Running failure_agent...")
    
    try:
        findings = _run_agent(
            state=state,
            agent_name="failure_agent",
            prompt_file="failure_agent.txt",
            category="reliability"
        )
        state["failure_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Failure agent error: {e}", exc_info=True)
        state["failure_findings"] = []
    
    return state


def cost_agent_node(state: InfraState) -> InfraState:
    """
    Cost optimization analysis agent.
    
    Uses create_react_agent to analyze cost signals with tools.
    Skips execution if not selected by planner.
    
    Args:
        state: InfraState with deterministic data populated
        
    Returns:
        Updated state with cost_findings
    """
    # Check if this agent should run
    if "cost_agent" not in state.get("planner_decision", []):
        logger.info("Skipping cost_agent (not selected by planner)")
        state["cost_findings"] = []
        return state
    
    logger.info("Running cost_agent...")
    
    try:
        findings = _run_agent(
            state=state,
            agent_name="cost_agent",
            prompt_file="cost_agent.txt",
            category="cost"
        )
        state["cost_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Cost agent error: {e}", exc_info=True)
        state["cost_findings"] = []
    
    return state


def security_agent_node(state: InfraState) -> InfraState:
    """
    Security audit analysis agent.
    
    Uses create_react_agent to analyze security signals with tools.
    Skips execution if not selected by planner.
    
    Args:
        state: InfraState with deterministic data populated
        
    Returns:
        Updated state with security_findings
    """
    # Check if this agent should run
    if "security_agent" not in state.get("planner_decision", []):
        logger.info("Skipping security_agent (not selected by planner)")
        state["security_findings"] = []
        return state
    
    logger.info("Running security_agent...")
    
    try:
        findings = _run_agent(
            state=state,
            agent_name="security_agent",
            prompt_file="security_agent.txt",
            category="security"
        )
        state["security_findings"] = findings[:MAX_FINDINGS]
    except Exception as e:
        logger.error(f"Security agent error: {e}", exc_info=True)
        state["security_findings"] = []
    
    return state


def _run_agent(
    state: InfraState,
    agent_name: str,
    prompt_file: str,
    category: str
) -> List[Dict[str, Any]]:
    """
    Run a ReAct agent with tools and parse JSON findings.
    
    Uses create_react_agent from langgraph.prebuilt.
    
    Args:
        state: Current InfraState
        agent_name: Name for logging
        prompt_file: Prompt file to load
        category: Signal category to summarize
        
    Returns:
        List of finding dicts
    """
    # Load system prompt
    system_prompt = _load_prompt(prompt_file)
    
    # Create tools with state closure
    tools = make_tools(state)
    
    # Create ReAct agent
    agent = create_react_agent(
        LLM,
        tools,
        prompt=system_prompt
    )
    
    # Build human message summarizing signals
    signals = state["signals"]
    category_signals = [s for s in signals if s.get("category") == category]
    
    human_msg = f"""Analyze the {category} signals and provide findings.

Signal count: {len(category_signals)}

Use the provided tools to gather additional context:
- get_signals(category="{category}")
- get_graph_summary()
- get_cluster_summary()
- get_risk_score()

Return your findings as a JSON array as specified in your instructions.
"""
    
    # Invoke agent
    result = agent.invoke({
        "messages": [HumanMessage(content=human_msg)]
    })
    
    # Extract findings from messages
    findings = _extract_json_findings(result)
    
    logger.info(f"{agent_name} produced {len(findings)} findings")
    return findings


def _extract_json_findings(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract JSON findings array from agent messages.
    
    Looks for JSON in the last message content.
    """
    messages = result.get("messages", [])
    if not messages:
        return []
    
    # Get last message content
    last_msg = messages[-1]
    content = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
    
    # Try to find JSON array in content
    try:
        # Look for JSON array markers
        start = content.find('[')
        end = content.rfind(']')
        
        if start != -1 and end != -1 and end > start:
            json_str = content[start:end+1]
            findings = json.loads(json_str)
            
            if isinstance(findings, list):
                # Validate each finding has required keys
                validated = []
                for f in findings:
                    if isinstance(f, dict) and all(k in f for k in ["resource", "severity", "analysis", "recommendation"]):
                        validated.append(f)
                return validated
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON findings: {e}")
    
    return []


def synthesizer_node(state: InfraState) -> InfraState:
    """
    Strategic synthesis agent.
    
    Produces executive summary integrating all findings and risk score.
    Uses LLM directly (no tools, no ReAct).
    
    Args:
        state: InfraState with all agent findings
        
    Returns:
        Updated state with strategic_summary
    """
    logger.info("Running synthesizer...")
    
    # Load system prompt
    system_prompt = _load_prompt("synthesizer.txt")
    
    # Build context from findings
    failure_findings = state.get("failure_findings", [])
    cost_findings = state.get("cost_findings", [])
    security_findings = state.get("security_findings", [])
    risk_score = state.get("risk_score", {})
    
    context = f"""
Risk Score: {risk_score.get('score', 0)}/100 (Grade: {risk_score.get('grade', 'N/A')})
Signals: {risk_score.get('signal_count', 0)}

Failure Findings: {len(failure_findings)}
{json.dumps(failure_findings[:10], indent=2)}

Cost Findings: {len(cost_findings)}
{json.dumps(cost_findings[:10], indent=2)}

Security Findings: {len(security_findings)}
{json.dumps(security_findings[:10], indent=2)}

Produce your strategic summary following the format in your instructions.
"""
    
    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=context)
        ]
        
        response = LLM.invoke(messages)
        summary = response.content if hasattr(response, 'content') else str(response)
        
        # Cap at ~1000 tokens (rough estimate: 4 chars per token)
        if len(summary) > 4000:
            summary = summary[:4000] + "\n\n[Summary truncated]"
        
        state["strategic_summary"] = summary
        logger.info("Synthesizer complete")
        
    except Exception as e:
        logger.error(f"Synthesizer error: {e}", exc_info=True)
        state["strategic_summary"] = "Error generating strategic summary."
    
    return state
