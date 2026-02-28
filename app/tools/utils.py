"""Small shared utilities for ClusterGPT.

Helpers for JSON I/O, optional-rich printing, timestamp formatting.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("clustergpt")

# ---------------------------------------------------------------------------
# Optional Rich console (graceful fallback to plain print)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console

    console = Console(stderr=True)

    def rprint(msg: str, *, style: str = "") -> None:
        """Print with Rich styling if available."""
        console.print(msg, style=style)

except ImportError:  # pragma: no cover
    console = None  # type: ignore[assignment]

    def rprint(msg: str, *, style: str = "") -> None:
        print(msg)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def write_json(data: Any, path: str | Path) -> Path:
    """Write *data* as pretty-printed JSON and return the resolved path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    return p


def read_json(path: str | Path) -> Any:
    """Read JSON from *path* and return the parsed object."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Pandoc check
# ---------------------------------------------------------------------------

def pandoc_available() -> bool:
    """Return True if pandoc is on $PATH."""
    return shutil.which("pandoc") is not None
