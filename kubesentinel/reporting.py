import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from .models import InfraState

logger = logging.getLogger(__name__)

def build_report(state: InfraState) -> str:
    """Build comprehensive markdown report from state."""
    logger.info("Building markdown report...")
    
    # Generate UTC timestamp
    report_timestamp = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    
    sections = []
    sections.append("# KubeSentinel Infrastructure Intelligence Report\n")
    sections.append(f"**Report generated at:** {report_timestamp} (UTC)\n")
    sections.append(f"**Analysis Query:** {state.get('user_query')}\n")
    sections.append("---\n")
    sections.append(_build_architecture_section(state))
    sections.append(_build_findings_section("💰 Cost Optimization Report", state.get("cost_findings", []), "cost optimization issues"))
    sections.append(_build_findings_section("🔐 Security Audit", state.get("security_findings", []), "security issues"))
    sections.append(_build_risk_section(state))
    sections.append(_build_strategic_section(state))
    report = "\n".join(sections)
    Path("report.md").write_text(report)
    logger.info(f"Report written to {Path('report.md').absolute()}")
    state["final_report"] = report
    return report

def _build_architecture_section(state: InfraState) -> str:
    """Build architecture overview section."""
    snapshot = state.get("cluster_snapshot", {})
    graph = state.get("graph_summary", {})
    
    nodes = snapshot.get("nodes", [])
    deployments = snapshot.get("deployments", [])
    pods = snapshot.get("pods", [])
    services = snapshot.get("services", [])
    
    orphan_services = graph.get("orphan_services", [])
    single_replica = graph.get("single_replica_deployments", [])
    node_fanout = graph.get("node_fanout_count", {})
    
    lines = [
        "## 📊 Architecture Report\n",
        "### Cluster Summary\n",
        f"- **Nodes:** {len(nodes)}",
        f"- **Deployments:** {len(deployments)}",
        f"- **Pods:** {len(pods)}",
        f"- **Services:** {len(services)}\n",
        "### Graph Metrics\n",
        f"- **Orphan Services:** {len(orphan_services)}",
        f"- **Single-Replica Deployments:** {len(single_replica)}\n",
    ]
    
    if orphan_services:
        lines.append("**Orphan Services** (no matching deployments):")
        for svc in orphan_services[:10]:
            lines.append(f"  - `{svc}`")
        if len(orphan_services) > 10:
            lines.append(f"  - _(... and {len(orphan_services) - 10} more)_")
        lines.append("")
    
    if single_replica:
        lines.append("**Single-Replica Deployments** (no redundancy):")
        for dep in single_replica[:10]:
            lines.append(f"  - `{dep}`")
        if len(single_replica) > 10:
            lines.append(f"  - _(... and {len(single_replica) - 10} more)_")
        lines.append("")
    
    if node_fanout:
        lines.append("**Node Distribution:**")
        for node, count in sorted(node_fanout.items(), key=lambda x: x[1], reverse=True)[:5]:
            lines.append(f"  - `{node}`: {count} pods")
        lines.append("")
    
    lines.append("---\n")
    return "\n".join(lines)

def _build_findings_section(title: str, findings: List[Dict[str, Any]], issue_type: str) -> str:
    """Build findings section (cost/security/failure)."""
    lines = [f"## {title}\n"]
    
    if not findings:
        lines.append(f"✅ **No {issue_type} detected.**\n")
    else:
        lines.append(f"**Total Findings:** {len(findings)}\n")
        by_severity = _group_by_severity(findings)
        
        for severity in ["critical", "high", "medium", "low"]:
            items = by_severity.get(severity, [])
            if items:
                icon = _severity_icon(severity)
                lines.append(f"### {icon} {severity.upper()} Priority\n")
                
                for finding in items[:5]:
                    lines.append(f"**{finding['resource']}**")
                    lines.append(f"- **Analysis:** {finding['analysis']}")
                    lines.append(f"- **Recommendation:** {finding['recommendation']}\n")
                
                if len(items) > 5:
                    lines.append(f"_(... and {len(items) - 5} more {severity} findings)_\n")
    
    lines.append("---\n")
    return "\n".join(lines)

def _build_risk_section(state: InfraState) -> str:
    """Build risk score section."""
    risk = state.get("risk_score", {})
    signals = state.get("signals", [])
    
    score = risk.get("score", 0)
    grade = risk.get("grade", "N/A")
    signal_count = risk.get("signal_count", 0)
    
    lines = [
        "## ⚠️ Reliability Risk Assessment\n",
        f"### Overall Risk Score: **{score}/100** (Grade: **{grade}**)\n",
        f"- **Total Signals:** {signal_count}\n",
    ]
    
    # Breakdown by category and severity
    by_category = _group_by_category(signals)
    
    if by_category:
        lines.append("### Signal Breakdown\n")
        
        for category in ["reliability", "security", "cost"]:
            cat_signals = by_category.get(category, [])
            if cat_signals:
                by_sev = _group_by_severity(cat_signals)
                lines.append(f"**{category.title()}:** {len(cat_signals)} signals")
                
                for severity in ["critical", "high", "medium", "low"]:
                    count = len(by_sev.get(severity, []))
                    if count > 0:
                        lines.append(f"  - {severity}: {count}")
                lines.append("")
    
    lines.append("---\n")
    return "\n".join(lines)

def _build_strategic_section(state: InfraState) -> str:
    """Build strategic AI explanation section."""
    summary = state.get("strategic_summary", "")
    
    lines = [
        "## 🤖 Strategic AI Analysis\n",
    ]
    
    if summary:
        lines.append(summary)
    else:
        lines.append("_No strategic summary generated._")
    
    lines.append("\n---\n")
    lines.append("*Report generated by KubeSentinel - Kubernetes Intelligence Engine*")
    
    return "\n".join(lines)

def _group_by_severity(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group items by severity."""
    result = {"critical": [], "high": [], "medium": [], "low": []}
    for item in items:
        severity = item.get("severity", "low")
        if severity in result:
            result[severity].append(item)
    return result

def _group_by_category(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group items by category."""
    result = {"reliability": [], "security": [], "cost": []}
    for item in items:
        category = item.get("category", "")
        if category in result:
            result[category].append(item)
    return result

def _severity_icon(severity: str) -> str:
    """Get emoji icon for severity."""
    icons = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }
    return icons.get(severity, "⚪")
