import json
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime
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
LLM = ChatOllama(model="qwen3:30b", temperature=0)

PROMPT_DIR = Path(__file__).parent / "prompts"

# Agent configuration constants
AGENT_TIMEOUT_SECONDS = 60
AGENT_MAX_ITERATIONS = 8
AGENT_TOOL_SIGNAL_LIMIT = 30
VERBOSE = os.getenv("KUBESENTINEL_VERBOSE_AGENTS") == "1"

# Expected JSON schema for agent findings
AGENT_FINDING_SCHEMA = ["resource", "severity", "analysis", "recommendation"]

# Kubectl safe verbs (read-only operations)
KUBECTL_SAFE_VERBS = {
    "get",
    "describe",
    "logs",
    "top",
    "explain",
    "api-resources",
    "api-versions",
    "diff",
}

# Kubectl write verbs (require 2-step approval)
KUBECTL_WRITE_VERBS = {
    "apply",
    "create",
    "delete",
    "patch",
    "replace",
    "scale",
    "set",
    "rollout",
    "exec",
    "port-forward",
    "attach",
    "cp",
    "label",
    "annotate",
}


class AgentTimeoutError(Exception):
    """Raised when agent exceeds timeout."""

    pass


def _sanitize_for_json(text: str) -> str:
    """Remove control characters from text for JSON safety."""
    if not isinstance(text, str):
        return str(text)
    # Remove control chars (ord < 32) except \n, \r, \t
    return "".join(c if ord(c) >= 32 or c in "\n\r\t" else " " for c in text)


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
        nodes, deployments, pods, services = (
            snap.get("nodes", []),
            snap.get("deployments", []),
            snap.get("pods", []),
            snap.get("services", []),
        )
        ns = set()
        for dep in deployments + pods + services:
            ns.add(dep.get("namespace", "default"))
        return json.dumps(
            {
                "nodes": len(nodes),
                "deployments": len(deployments),
                "pods": len(pods),
                "services": len(services),
                "namespaces": sorted(ns),
            }
        )

    @tool
    def get_graph_summary() -> str:
        """Get dependency graph analysis results.

        Returns: JSON with orphan_services (no backend), single_replica deployments (no redundancy),
        and service count. Use to identify architectural risks like missing backends or single points of failure.
        """
        g = state.get("graph_summary", {})
        return json.dumps(
            {
                "orphan_services": g.get("orphan_services", []),
                "single_replica": g.get("single_replica_deployments", []),
                "services": len(g.get("service_to_deployment", {})),
            }
        )

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

    @tool
    def get_pod_logs(pod_name: str, namespace: str, tail_lines: int = 50) -> str:
        """Get recent logs from a pod to diagnose failures.

        Args:
            pod_name: Name of the pod (must exist in cluster data)
            namespace: Namespace of the pod
            tail_lines: Number of recent log lines to retrieve (default 50, max 200)

        Returns: Log lines as string, or error message if logs cannot be retrieved.
        Use this to see what's actually failing in crashloop pods.
        """
        tail_lines = min(tail_lines, 200)  # Safety limit
        try:
            # Try to get logs from previous (crashed) container first
            result = subprocess.run(
                ["kubectl", "logs", f"{pod_name}", "-n", namespace, "--previous", f"--tail={tail_lines}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout or "No logs available"
            
            # Fallback: get logs from current container
            result = subprocess.run(
                ["kubectl", "logs", f"{pod_name}", "-n", namespace, f"--tail={tail_lines}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Log fetch timed out after 10 seconds"
        except Exception as e:
            return f"Error fetching logs: {str(e)}"

    @tool
    def get_resource_yaml(resource_type: str, name: str, namespace: str = "") -> str:
        """Get YAML definition of a Kubernetes resource to inspect configuration.

        Args:
            resource_type: Resource type (deployment, service, pod, configmap, etc.)
            name: Resource name (must exist in cluster data)
            namespace: Namespace (required for namespaced resources)

        Returns: YAML definition as string, or error message.
        Use this to inspect resource configuration, labels, annotations, env vars, volume mounts, etc.
        """
        if not resource_type or not name:
            return "Error: resource_type and name are required"
        
        cmd = ["kubectl", "get", resource_type, name, "-o", "yaml"]
        if namespace:
            cmd.extend(["-n", namespace])
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Get resource timed out after 10 seconds"
        except Exception as e:
            return f"Error: {str(e)}"

    @tool
    def kubectl_safe(command_args: str) -> str:
        """Execute safe (read-only) kubectl command to gather evidence.

        Args:
            command_args: kubectl command arguments WITHOUT 'kubectl' prefix.
                         Example: "describe pod myapp-123 -n production"
                         Only safe verbs allowed: get, describe, logs, top, explain

        Returns: Command output as string, or error message if command is unsafe.
        Use this to gather diagnostic evidence. DO NOT use for write operations.
        """
        if not command_args or not command_args.strip():
            return "Error: command_args cannot be empty"
        
        # Parse command safely
        try:
            args = shlex.split(command_args.strip())
        except ValueError as e:
            return f"Error: Invalid command syntax: {str(e)}"
        
        if not args:
            return "Error: No command provided"
        
        # Validate verb is safe
        verb = args[0].lower()
        if verb not in KUBECTL_SAFE_VERBS:
            return (
                f"Error: Verb '{verb}' not allowed. "
                f"Only safe read-only verbs permitted: {', '.join(sorted(KUBECTL_SAFE_VERBS))}"
            )
        
        # Reject shell metacharacters for safety
        dangerous_chars = ["|", "&", ";", "$", "`", ">", "<", "\n", "\\"]
        if any(char in command_args for char in dangerous_chars):
            return "Error: Shell metacharacters not allowed in kubectl commands"
        
        # Execute kubectl command
        try:
            result = subprocess.run(
                ["kubectl"] + args,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return result.stdout or "Command completed successfully (no output)"
            else:
                return f"kubectl error (exit {result.returncode}): {result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 15 seconds"
        except Exception as e:
            return f"Error executing kubectl: {str(e)}"

    return [
        get_cluster_summary,
        get_graph_summary,
        get_signals,
        get_risk_score,
        get_pod_logs,
        get_resource_yaml,
        kubectl_safe,
    ]


def planner_node(state: InfraState) -> InfraState:
    """Deterministic planner that decides which agents to run based on query keywords."""
    logger.info("Planning agent execution...")

    # Check for CLI override first
    if state.get("planner_decision"):
        logger.info(f"Planner using CLI override: {state.get('planner_decision')}")
        return state

    query = state.get("user_query", "").lower()

    # Extract tokens (words >= 3 chars)
    tokens = set(re.findall(r"\b[a-z]{3,}\b", query))

    # Expand common synonyms so high-level prompts route correctly.
    synonyms = {
        "risks": {"risk", "reliability"},
        "risk": {"reliability"},
        "pending": {"capacity", "pressure", "reliability"},
        "production": {"architecture", "security", "reliability", "cost"},
    }
    expanded_tokens = set(tokens)
    for token in list(tokens):
        expanded_tokens.update(synonyms.get(token, set()))

    if VERBOSE:
        logger.debug(f"Planner tokens: {tokens}")
        logger.debug(f"Planner expanded tokens: {expanded_tokens}")

    phrase_routes = {
        r"top\s*\d*\s*risks?": ["failure_agent", "cost_agent", "security_agent"],
        r"fix\s+first": ["failure_agent", "cost_agent", "security_agent"],
        r"production\s+risk": ["failure_agent", "cost_agent", "security_agent"],
        r"pods?\s+pending": ["failure_agent", "cost_agent"],
    }
    phrase_agents = []
    for pattern, route in phrase_routes.items():
        if re.search(pattern, query):
            phrase_agents.extend(route)

    # Architecture queries explicitly request all agents
    architecture_keywords = {
        "full",
        "all",
        "complete",
        "architecture",
        "deep",
        "comprehensive",
    }
    if any(w in expanded_tokens for w in architecture_keywords):
        agents = ["failure_agent", "cost_agent", "security_agent"]
        logger.info(f"Planner selected agents: {agents} (architecture query)")
        state["planner_decision"] = agents
        state["planner_metadata"] = {
            "tokens": sorted(tokens),
            "expanded_tokens": sorted(expanded_tokens),
            "scores": {"failure_agent": 1, "cost_agent": 1, "security_agent": 1},
            "confidence": "high",
            "reason": "architecture_query",
        }
        return state

    # Score-based routing
    cost_keywords = {
        "cost",
        "costs",
        "spend",
        "spending",
        "bill",
        "billing",
        "price",
        "pricing",
        "budget",
        "optimization",
        "optimize",
        "reduce",
        "save",
        "saving",
        "savings",
        "waste",
    }
    security_keywords = {
        "security",
        "secure",
        "vuln",
        "cve",
        "cis",
        "privilege",
        "audit",
        "exposure",
        "compliance",
    }

    reliability_keywords = {
        "reliability",
        "failure",
        "fail",
        "outage",
        "replica",
        "redundancy",
        "health",
        "pressure",
        "risk",
        "capacity",
        "pending",
        "scheduling",
    }

    node_keywords = {"node", "memory", "disk", "pressure", "capacity"}

    scores = {
        "failure_agent": len(expanded_tokens & reliability_keywords)
        + len(expanded_tokens & node_keywords),
        "cost_agent": len(expanded_tokens & cost_keywords),
        "security_agent": len(expanded_tokens & security_keywords),
    }

    for agent in phrase_agents:
        scores[agent] += 2

    # Select top 2 agents by score for efficiency (not all 3)
    # Sort by score descending, take top 2, but require minimum score > 0
    sorted_agents = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    agents = [agent for agent, score in sorted_agents[:2] if score > 0]

    # If no specific routing matched, default to failure_agent for generic queries
    if not agents:
        logger.warning(
            f"No specific agent routing for query: '{query}' - defaulting to failure_agent"
        )
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
    state["planner_metadata"] = {
        "tokens": sorted(tokens),
        "expanded_tokens": sorted(expanded_tokens),
        "scores": scores,
        "confidence": "high"
        if max(scores.values()) >= 3
        else "medium"
        if max(scores.values()) >= 1
        else "low",
        "reason": "scored_routing",
    }
    return state


def _verify_findings_with_evidence(
    findings: List[Dict[str, Any]], state: InfraState, max_verifications: int = 3
) -> List[Dict[str, Any]]:
    """Verify LLM findings with actual cluster evidence (ReAct verification loop).

    For each finding, attempts to:
    1. Extract resource and validate it exists
    2. Gather evidence: pod logs, resource YAML, describe output
    3. Try pattern matching against error signatures
    4. Add evidence to finding or mark as unverified

    Args:
        findings: LLM-generated findings (hypotheses)
        state: Current infrastructure state
        max_verifications: Max number of findings to verify (20s timeout constraint)

    Returns:
        Enhanced findings with evidence annotations
    """
    if not findings:
        return []

    verified_findings = []
    snapshot = state.get("cluster_snapshot", {})
    all_pods = {p["name"]: p for p in snapshot.get("pods", [])}

    # Track verification attempts for timeout management
    verifications_done = 0
    max_verifications = min(max_verifications, len(findings))

    for finding in findings[:max_verifications]:
        verifications_done += 1
        try:
            # Parse resource from finding
            resource = finding.get("resource", "")
            if not resource:
                finding["verified"] = False
                finding["evidence"] = "No resource specified"
                verified_findings.append(finding)
                continue

            # Extract namespace and resource name
            parts = resource.split("/")
            if len(parts) < 2:
                finding["verified"] = False
                finding["evidence"] = f"Invalid resource format: {resource}"
                verified_findings.append(finding)
                continue

            # Try to validate resource exists in snapshot
            namespace = parts[0] if len(parts) >= 2 else "default"
            resource_name = parts[-1]

            # Check if it's a pod
            if resource_name in all_pods:
                pod = all_pods[resource_name]
                evidence = []

                # Try to get logs from the pod
                if pod.get("crash_loop_backoff"):
                    try:
                        result = subprocess.run(
                            [
                                "kubectl",
                                "logs",
                                resource_name,
                                "-n",
                                namespace,
                                "--previous",
                                "--tail=30",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0 and result.stdout:
                            evidence.append(
                                f"Pod logs (last 30 lines): {result.stdout[:500]}"
                            )
                            # Try error signature matching
                            from .diagnostics.error_signatures import (
                                diagnose_crash_logs,
                            )

                            diagnosis = diagnose_crash_logs(result.stdout)
                            if diagnosis:
                                evidence.append(
                                    f"Root cause: {diagnosis.root_cause} (confidence: {diagnosis.confidence})"
                                )
                    except Exception as e:
                        if VERBOSE:
                            logger.debug(f"Failed to get logs for {resource_name}: {e}")

                # Add evidence to finding
                if evidence:
                    finding["verified"] = True
                    finding["evidence"] = " | ".join(evidence)
                else:
                    finding["verified"] = False
                    finding["evidence"] = "Pod exists but no logs available"

            else:
                # Generic validation: resource exists in snapshot (any type)
                found = False
                for resource_list in [
                    "deployments",
                    "statefulsets",
                    "daemonsets",
                    "services",
                    "configmaps",
                ]:
                    resources = snapshot.get(resource_list, [])
                    if any(r.get("name") == resource_name for r in resources):
                        found = True
                        break

                finding["verified"] = found
                finding["evidence"] = (
                    "Found in cluster snapshot" if found else "Not found in snapshot"
                )

            verified_findings.append(finding)

        except Exception as e:
            logger.warning(f"Error verifying finding: {e}")
            finding["verified"] = False
            finding["evidence"] = f"Verification error: {str(e)[:100]}"
            verified_findings.append(finding)

    # Mark remaining findings as unverified (didn't run verification)
    for finding in findings[max_verifications:]:
        finding["verified"] = False
        finding["evidence"] = "Verification skipped (timeout constraint)"
        verified_findings.append(finding)

    return verified_findings


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
            return _run_agent(
                state, "failure_agent", "failure_agent.txt", "reliability"
            )

        findings = run_llm()
        
        # Verify findings with actual evidence (ReAct loop)
        if findings:
            findings = _verify_findings_with_evidence(findings, state, max_verifications=3)
        
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
        
        # Verify findings with actual evidence (ReAct loop)
        if findings:
            findings = _verify_findings_with_evidence(findings, state, max_verifications=3)
        
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
        
        # Verify findings with actual evidence (ReAct loop)
        if findings:
            findings = _verify_findings_with_evidence(findings, state, max_verifications=3)
        
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
    single_replicas = [
        s
        for s in signals
        if "replica" in s.get("message", "").lower() and "1" in s.get("message", "")
    ]
    if single_replicas:
        findings.append(
            {
                "resource": "cluster/redundancy",
                "severity": "high",
                "analysis": f"Found {len(single_replicas)} deployments with only 1 replica",
                "recommendation": "Increase replica count to 3+ for production workloads",
            }
        )

    # Rule: CrashLoopBackOff is critical
    crash_signals = [s for s in signals if "CrashLoopBackOff" in s.get("message", "")]
    if crash_signals:
        findings.append(
            {
                "resource": "cluster/health",
                "severity": "critical",
                "analysis": f"{len(crash_signals)} pods in CrashLoopBackOff state",
                "recommendation": "Investigate pod logs and deployment specifications immediately",
            }
        )

    # Rule: High risk score requires immediate action
    if risk.get("score", 0) > 80:
        findings.append(
            {
                "resource": "cluster/risk",
                "severity": "critical",
                "analysis": f"Cluster risk score is {risk.get('score', 0)}/100 ({risk.get('grade', 'F')})",
                "recommendation": "Address critical and high-severity signals immediately",
            }
        )

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
        findings.append(
            {
                "resource": "cluster/deployments",
                "severity": "medium",
                "analysis": f"{len(single_replicas)} deployments run with single replica (inefficient resource usage)",
                "recommendation": "Consolidate single-replica workloads or enable horizontal pod autoscaling to improve node utilization",
            }
        )

    # Rule 2: Nodes under 30% utilization → cost waste
    nodes = snapshot.get("nodes", [])
    pods = snapshot.get("pods", [])
    underutilized_nodes = []
    for node in nodes:
        node_name = node.get("name")
        node_pods = [p for p in pods if p.get("node_name") == node_name]

        # Estimate utilization from requested resources
        node_cpu_str = node.get("cpu", "0")
        node_cpu = (
            float(node_cpu_str.rstrip("m"))
            if "m" in node_cpu_str
            else float(node_cpu_str) * 1000
        )

        total_requested_cpu = 0.0
        for pod in node_pods:
            for container in pod.get("containers", []):
                cpu_req = (
                    container.get("resources", {}).get("requests", {}).get("cpu", "0")
                )
                if cpu_req:
                    cpu_val = (
                        float(cpu_req.rstrip("m"))
                        if "m" in cpu_req
                        else float(cpu_req) * 1000
                    )
                    total_requested_cpu += cpu_val

        if node_cpu > 0:
            utilization = (total_requested_cpu / node_cpu) * 100
            if (
                utilization < 30 and len(node_pods) > 0
            ):  # Only flag if node has workloads
                underutilized_nodes.append(
                    {"name": node_name, "utilization": utilization}
                )

    if underutilized_nodes:
        findings.append(
            {
                "resource": "cluster/nodes",
                "severity": "high",
                "analysis": f"{len(underutilized_nodes)} nodes are under 30% CPU utilization (wasted capacity)",
                "recommendation": "Consider draining and removing underutilized nodes, or consolidating workloads to fewer nodes",
            }
        )

    # Rule 3: Workloads without HPA → scaling inefficiency
    deployments = snapshot.get("deployments", [])
    # Note: HPA detection requires HPA resources in cluster scan (future enhancement)
    # For now, detect fixed replica counts > 1 as potential HPA candidates
    hpa_candidates = [
        d for d in deployments if d.get("replicas", 0) > 1 and d.get("replicas", 0) < 10
    ]
    if len(hpa_candidates) > 5:
        findings.append(
            {
                "resource": "cluster/autoscaling",
                "severity": "low",
                "analysis": f"{len(hpa_candidates)} deployments with fixed replica counts could benefit from autoscaling",
                "recommendation": "Enable HorizontalPodAutoscaler (HPA) for workloads with variable load patterns",
            }
        )

    # Rule 4: Over-requested CPU (detected via signals)
    over_requested = [
        s
        for s in signals
        if "over-requested" in s.get("message", "").lower()
        or "over-provisioned" in s.get("message", "").lower()
    ]
    if over_requested:
        findings.append(
            {
                "resource": "cluster/resources",
                "severity": "medium",
                "analysis": f"{len(over_requested)} containers have resource requests significantly exceeding usage",
                "recommendation": "Right-size CPU/memory requests based on actual usage patterns (use VPA or monitoring data)",
            }
        )

    return findings


def _deterministic_security_check(state: InfraState) -> List[Dict[str, Any]]:
    """Deterministic security check (rules-based, no LLM)."""
    findings = []
    signals = state.get("signals", [])

    # Rule: Privileged containers are critical
    privileged = [
        s for s in signals if "privileged mode" in s.get("message", "").lower()
    ]
    if privileged:
        findings.append(
            {
                "resource": "cluster/containers",
                "severity": "critical",
                "analysis": f"{len(privileged)} containers run in privileged mode (CIS 5.2.1)",
                "recommendation": "Remove privileged mode; use specific capabilities if needed",
            }
        )

    # Rule: Latest image tags
    latest_images = [
        s
        for s in signals
        if "latest" in s.get("message", "").lower()
        or "untagged" in s.get("message", "").lower()
    ]
    if latest_images:
        findings.append(
            {
                "resource": "cluster/images",
                "severity": "high",
                "analysis": f"{len(latest_images)} containers use :latest or untagged images (CIS 5.4.1)",
                "recommendation": "Pin all containers to specific immutable image tags",
            }
        )

    # Rule: Missing resource limits
    no_limits = [
        s for s in signals if "no resource limits" in s.get("message", "").lower()
    ]
    if no_limits:
        findings.append(
            {
                "resource": "cluster/resources",
                "severity": "medium",
                "analysis": f"{len(no_limits)} containers lack resource limits (CIS 5.2.12)",
                "recommendation": "Define CPU and memory requests/limits for all containers",
            }
        )

    return findings


def _run_agent(
    state: InfraState, agent_name: str, prompt_file: str, category: str
) -> List[Dict[str, Any]]:
    """Run agent with tools and parse JSON findings.

    Uses max_iterations to prevent infinite loops and sends compact prompt to reduce tokens.
    """
    system_prompt = (PROMPT_DIR / prompt_file).read_text()

    tools = make_tools(state)
    agent = create_agent(LLM, tools, system_prompt=system_prompt)

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
    findings = _extract_json_findings(result, agent_name)

    if VERBOSE:
        logger.debug(
            f"{agent_name} returned {len(findings)} findings from {len(category_signals)} signals"
        )
    else:
        logger.info(f"{agent_name} produced {len(findings)} findings")

    return findings


def _extract_json_findings(
    result: Dict[str, Any] | None, agent_name: str = "unknown"
) -> List[Dict[str, Any]]:
    """Extract JSON findings array from agent output with robust 7-step extraction.

    Steps:
    1. Extract content from result dict
    2. Sanitize control characters
    3. Handle markdown code fences
    4. Try direct JSON parse
    5. Fallback: extract content between [ and ]
    6. Validate schema for each finding
    7. Log parse failures to persistence

    Args:
        result: Agent output dict with "output" key
        agent_name: Name of agent for logging (e.g., "failure_agent")

    Returns:
        List of validated finding dicts
    """
    if not result:
        logger.warning(f"{agent_name}: No result dict provided")
        return []

    # Step 1: Extract content from result dict
    content = result.get("output", "")
    if not content:
        logger.warning(f"{agent_name}: Empty output in result dict")
        return []

    content = str(content) if not isinstance(content, str) else content

    # Step 2: Sanitize control characters
    content = _sanitize_for_json(content)

    # Step 3: Handle markdown code fences (```json ... ``` or ``` ... ```)
    if "```" in content:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
        if match:
            content = match.group(1).strip()
            if VERBOSE:
                logger.debug(f"{agent_name}: Extracted content from markdown fence")

    # Step 4: Try direct JSON parse
    try:
        findings = json.loads(content)
        if isinstance(findings, list):
            valid = _validate_findings(findings, agent_name)
            if valid:
                return valid
    except json.JSONDecodeError as e:
        if VERBOSE:
            logger.debug(f"{agent_name}: Direct JSON parse failed: {str(e)[:100]}")

    # Step 5: Fallback - extract content between [ and ]
    start, end = content.find("["), content.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            findings = json.loads(content[start : end + 1])
            if isinstance(findings, list):
                valid = _validate_findings(findings, agent_name)
                if valid:
                    if VERBOSE:
                        logger.debug(
                            f"{agent_name}: Extracted from bracketed JSON fallback"
                        )
                    return valid
        except json.JSONDecodeError as e:
            logger.warning(
                f"{agent_name}: Bracketed JSON extraction failed: {str(e)[:100]}"
            )

    # Step 6 & 7: Log parse failure
    logger.error(
        f"{agent_name}: Failed to extract valid JSON findings. "
        f"Output preview: {content[:200]}"
    )

    # Log to persistence for debugging
    try:
        from .runtime import get_persistence_manager

        persistence = get_persistence_manager()
        persistence.log_agent_output(agent_name, content, error="JSON extraction failed")
    except Exception as e:
        logger.warning(f"Failed to log agent output to persistence: {e}")

    return []


def _validate_findings(
    findings: List[Any], agent_name: str = "unknown"
) -> List[Dict[str, Any]]:
    """Validate findings against AGENT_FINDING_SCHEMA.

    Args:
        findings: List of candidate finding dicts
        agent_name: Name of agent for logging

    Returns:
        List of valid findings (empty if none valid)
    """
    if not isinstance(findings, list):
        logger.warning(f"{agent_name}: Findings is not a list: {type(findings)}")
        return []

    valid = []
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            logger.warning(
                f"{agent_name}: Finding {idx} is not a dict: {type(finding)}"
            )
            continue

        missing = [k for k in AGENT_FINDING_SCHEMA if k not in finding]
        if missing:
            logger.warning(
                f"{agent_name}: Finding {idx} missing required fields: {missing}"
            )
            continue

        valid.append(finding)

    if valid and VERBOSE:
        logger.debug(f"{agent_name}: Validated {len(valid)}/{len(findings)} findings")

    return valid


def _synthesize_strategic_summary(state: InfraState) -> str:
    """Generate deterministic strategic summary from findings (no LLM).

    Produces structured output based on verified findings and risk assessment.
    
    Args:
        state: Current infrastructure state with findings
        
    Returns:
        Strategic summary string
    """
    failure = state.get("failure_findings", [])
    cost = state.get("cost_findings", [])
    security = state.get("security_findings", [])
    risk = state.get("risk_score", {})
    snapshot = state.get("cluster_snapshot", {})

    lines = []
    lines.append(f"# Strategic Summary")
    lines.append("")
    
    # Risk assessment header
    risk_score = risk.get("score", 0)
    risk_grade = risk.get("grade", "N/A")
    lines.append(f"## Risk Assessment: {risk_score}/100 ({risk_grade})")
    lines.append(f"- Cluster Size: {len(snapshot.get('nodes', []))} nodes, {len(snapshot.get('pods', []))} pods")
    lines.append(f"- Total Signals: {risk.get('signal_count', 0)}")
    lines.append(f"- Agents Executed: failure_agent, cost_agent (top-2 selection)")
    lines.append("")

    # Critical findings
    critical_findings = []
    for finding_list in [failure, cost, security]:
        critical_findings.extend([f for f in finding_list if f.get("severity") == "critical"])

    if critical_findings:
        lines.append(f"## Critical Issues ({len(critical_findings)} found)")
        for finding in critical_findings[:5]:  # Top 5 critical
            lines.append(f"- **{finding.get('resource')}**: {finding.get('analysis')}")
            if finding.get("verified"):
                lines.append(f"  Evidence: {finding.get('evidence', 'N/A')[:100]}")
            lines.append(f"  Action: {finding.get('recommendation')}")
            lines.append("")
    else:
        lines.append("## No Critical Issues Detected")
        lines.append("")

    # Category breakdown
    lines.append("## Findings by Category")
    lines.append(f"- **Reliability**: {len(failure)} findings")
    if failure:
        high_reliability = [f for f in failure if f.get("severity") in ("critical", "high")]
        if high_reliability:
            lines.append(f"  - {len(high_reliability)} high-severity findings require immediate attention")

    lines.append(f"- **Cost**: {len(cost)} findings")
    if cost:
        savings_potential = len([f for f in cost if f.get("severity") in ("high", "critical")])
        if savings_potential:
            lines.append(f"  - {savings_potential} optimization opportunities identified")

    lines.append(f"- **Security**: {len(security)} findings")
    if security:
        security_critical = [f for f in security if f.get("severity") == "critical"]
        if security_critical:
            lines.append(f"  - {len(security_critical)} critical vulnerabilities need remediation")

    lines.append("")

    # Recommendations
    if failure or cost or security:
        lines.append("## Recommended Actions (Prioritized)")
        idx = 1
        
        # Critical findings first
        for finding in critical_findings[:3]:
            lines.append(f"{idx}. **{finding.get('resource', 'Cluster')}** ({finding.get('severity')})")
            lines.append(f"   - Issue: {finding.get('analysis')}")
            lines.append(f"   - Action: {finding.get('recommendation')}")
            if finding.get("verified") and finding.get("evidence"):
                lines.append(f"   - Evidence: {finding.get('evidence')[:100]}")
            idx += 1
        
        # High-severity findings
        high_findings = []
        for finding_list in [failure, cost, security]:
            high_findings.extend([f for f in finding_list if f.get("severity") == "high"])
        
        for finding in high_findings[:2]:
            lines.append(f"{idx}. **{finding.get('resource', 'Cluster')}** ({finding.get('severity')})")
            lines.append(f"   - Issue: {finding.get('analysis')}")
            lines.append(f"   - Action: {finding.get('recommendation')}")
            idx += 1
    
    lines.append("")
    lines.append("## Verification Status")
    verified_count = sum(1 for f in failure + cost + security if f.get("verified"))
    total_count = len(failure) + len(cost) + len(security)
    lines.append(f"- {verified_count}/{total_count} findings verified with cluster evidence")
    
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by KubeSentinel at {datetime.utcnow().isoformat()}*")

    return "\n".join(lines)


def synthesizer_node(state: InfraState) -> InfraState:
    """Strategic synthesis agent - produces executive summary."""
    logger.info("Running synthesizer...")
    
    # Use deterministic synthesis instead of LLM
    try:
        # First try deterministic summary (no LLM, faster, more reliable)
        summary = _synthesize_strategic_summary(state)
        
        # Optionally, enhance with LLM if available (for richer formatting)
        # but don't fail if LLM unavailable
        try:
            system_prompt = (PROMPT_DIR / "synthesizer.txt").read_text()
            context = f"Create a strategic summary based on this analysis:\n\n{summary}"
            
            response = LLM.invoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=context)]
            )
            llm_summary = response.content if hasattr(response, "content") else str(response)
            llm_summary = str(llm_summary) if not isinstance(llm_summary, str) else llm_summary
            
            # Check for placeholders (indicates hallucination)
            placeholder_pattern = r"<[a-z\-_]+>"
            placeholders_found = re.findall(placeholder_pattern, llm_summary, re.IGNORECASE)
            if placeholders_found:
                logger.warning(
                    f"LLM output contains placeholders: {set(placeholders_found)} - using deterministic summary instead"
                )
                summary = summary  # Use deterministic version
            else:
                # LLM enhanced successfully
                summary = llm_summary if llm_summary else summary
                logger.debug("LLM enhanced strategic summary")
        except Exception as e:
            logger.debug(f"LLM enhancement skipped: {e} - using deterministic summary")
            # Fallback to deterministic summary
            pass
        
        state["strategic_summary"] = (
            summary[:8000] + "\n[Summary truncated]" if len(summary) > 8000 else summary
        )
        logger.info("Synthesizer complete")
    except Exception as e:
        logger.error(f"Synthesizer error: {e}")
        state["strategic_summary"] = "Error generating strategic summary."
    
    return state
