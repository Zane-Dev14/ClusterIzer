"""Shared Pydantic models for ClusterGPT.

Every agent and module imports from here to keep Finding, Evidence,
and schema definitions DRY.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """Finding severity levels (descending priority)."""
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class Category(str, Enum):
    """Finding categories matching the four audit pillars."""
    reliability = "reliability"
    security = "security"
    cost = "cost"
    architecture = "architecture"


class Confidence(str, Enum):
    """Confidence tag for explainer / diagnosis outputs."""
    high = "high"
    medium = "medium"
    low = "low"


# ---------------------------------------------------------------------------
# Evidence & Remediation (sub-models)
# ---------------------------------------------------------------------------

class Evidence(BaseModel):
    """Pointer to a concrete Kubernetes object or event."""
    kind: str = Field(..., description="Pod | Deployment | Node | Event | HPA | RBAC")
    namespace: str = Field(default="", description="Empty for cluster-scoped objects")
    name: str
    timestamp: str = Field(default="", description="ISO-8601 or empty")
    pointer: str = Field(default="", description="kubectl command or manifest UID")


class RemediationDetail(BaseModel):
    """Actionable fix for a single finding."""
    description: str
    kubectl: list[str] = Field(default_factory=list)
    patch_yaml: str = Field(default="", description="YAML snippet or empty")


# ---------------------------------------------------------------------------
# Finding — the universal output of every rule / agent
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """A single audit finding with evidence and remediation."""
    id: str = Field(..., description="Unique ID: {rule_id}:{ns}/{name}")
    category: Category
    severity: Severity
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    remediation: RemediationDetail = Field(
        default_factory=lambda: RemediationDetail(description="No remediation available.")
    )


# ---------------------------------------------------------------------------
# Cost models
# ---------------------------------------------------------------------------

class DeploymentCost(BaseModel):
    """Per-deployment monthly cost estimate."""
    namespace: str
    name: str
    replicas: int = 0
    cpu_requests: float = 0.0
    mem_requests_gb: float = 0.0
    monthly_cost_usd: float = 0.0


class CostEstimate(BaseModel):
    """Cluster-wide cost summary."""
    deployments: list[DeploymentCost] = Field(default_factory=list)
    namespace_totals: dict[str, float] = Field(default_factory=dict)
    monthly_total_usd: float = 0.0
    waste_pct: float = 0.0


# ---------------------------------------------------------------------------
# Explainer output models (strict JSON contract)
# ---------------------------------------------------------------------------

class SREAction(BaseModel):
    """A single prioritised action for the SRE team."""
    priority: int
    title: str
    steps: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.medium


class PRText(BaseModel):
    """Auto-generated pull-request description."""
    title: str = ""
    description: str = ""
    changes: list[str] = Field(default_factory=list)


class ExplainerOutput(BaseModel):
    """The LLM (or template) must return exactly this shape."""
    exec_summary: str = ""
    sre_actions: list[SREAction] = Field(default_factory=list)
    pr_text: PRText = Field(default_factory=PRText)
    confidence_overall: Confidence = Confidence.medium


# ---------------------------------------------------------------------------
# Cluster metadata (lightweight)
# ---------------------------------------------------------------------------

class ClusterMeta(BaseModel):
    """Basic metadata about the analysed cluster."""
    cluster_name: str = "unknown"
    context_name: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    node_count: int = 0
    namespace_count: int = 0
    pod_count: int = 0
