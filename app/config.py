"""ClusterGPT configuration — constants, defaults, price maps.

All tunables live here so agents stay free of magic numbers.
Override at runtime via environment variables or CLI flags.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Cost defaults (USD)
# ---------------------------------------------------------------------------

CPU_HOUR_COST: float = float(os.getenv("CLUSTERGPT_CPU_HOUR_COST", "0.03"))
RAM_GB_HOUR_COST: float = float(os.getenv("CLUSTERGPT_RAM_GB_HOUR_COST", "0.004"))
HOURS_PER_MONTH: int = 730  # ~365.25 * 24 / 12

# Simple node-type → hourly cost mapping (instance type prefix → $/hr).
# Users can override via --price-cpu / --price-ram or a JSON config file.
NODE_PRICE_MAP: dict[str, float] = {
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "c5.large": 0.085,
    "r5.large": 0.126,
    # GKE / AKS rough equivalents
    "e2-medium": 0.034,
    "e2-standard-2": 0.067,
    "e2-standard-4": 0.134,
    "Standard_B2s": 0.042,
    "Standard_D2s_v3": 0.096,
}

# ---------------------------------------------------------------------------
# Severity weights for risk score (0-100)
# ---------------------------------------------------------------------------

SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 25,
    "high": 15,
    "medium": 8,
    "low": 3,
}

# ---------------------------------------------------------------------------
# OpenAI / LLM settings
# ---------------------------------------------------------------------------

OPENAI_MODEL: str = os.getenv("CLUSTERGPT_OPENAI_MODEL", "gpt-4o-mini")
OPENAI_MAX_TOKENS: int = int(os.getenv("CLUSTERGPT_OPENAI_MAX_TOKENS", "2000"))
OPENAI_TEMPERATURE: float = 0.0  # deterministic

# ---------------------------------------------------------------------------
# Snapshot defaults
# ---------------------------------------------------------------------------

SNAPSHOT_DIR: str = "snapshots"
BACKUP_DIR: str = "backups"
DEFAULT_KUBECONFIG: str = os.path.expanduser("~/.kube/config")
DEFAULT_OUTPUT: str = "report.md"

# ---------------------------------------------------------------------------
# Event recency window (seconds) — only pull events from last N seconds
# ---------------------------------------------------------------------------

EVENT_WINDOW_SECONDS: int = int(os.getenv("CLUSTERGPT_EVENT_WINDOW", "86400"))  # 24 h
