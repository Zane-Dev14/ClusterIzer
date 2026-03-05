import sqlite3
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class Snapshot:
    """Cluster snapshot metadata."""
    timestamp: str  # ISO 8601
    cluster_name: str
    node_count: int
    pod_count: int
    signal_count: int
    risk_score: float
    risk_grade: str
    cluster_state_hash: str  # SHA256 of cluster snapshot
    signal_state_hash: str   # SHA256 of signals

@dataclass
class Drift:
    """Detected changes between snapshots."""
    timestamp: str
    drift_type: str  # "resource_change", "signal_delta", "cost_delta", "risk_shift"
    severity: str  # "critical", "high", "medium", "low"
    resource_type: str  # "pod", "deployment", "service", "node", "signal"
    resource_key: str  # "namespace/name"
    old_value: str
    new_value: str
    description: str

class PersistenceManager:
    """SQLite-backed persistence for snapshots and drift."""
    
    def __init__(self, db_path: str = "~/.kubesentinel/kubesentinel.db"):
        """Initialize SQLite database."""
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info(f"Persistence initialized: {self.db_path}")
    
    def _init_schema(self):
        """Create tables if not exists."""
        with self.conn:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL UNIQUE,
                cluster_name TEXT NOT NULL,
                node_count INTEGER,
                pod_count INTEGER,
                signal_count INTEGER,
                risk_score REAL,
                risk_grade TEXT,
                cluster_state_hash TEXT UNIQUE,
                signal_state_hash TEXT,
                full_state BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
            
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS drifts (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                drift_type TEXT NOT NULL,
                severity TEXT,
                resource_type TEXT,
                resource_key TEXT,
                old_value TEXT,
                new_value TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (timestamp) REFERENCES snapshots(timestamp)
            )
            """)
            
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS resource_defs (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                resource_type TEXT,
                spec_hash TEXT NOT NULL UNIQUE,
                spec_json BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (timestamp) REFERENCES snapshots(timestamp)
            )
            """)
            
            self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp 
            ON snapshots(timestamp DESC)""")
            self.conn.execute("""CREATE INDEX IF NOT EXISTS idx_drifts_resource 
            ON drifts(resource_key, drift_type)""")
    
    def save_snapshot(self, state: Dict[str, Any]) -> str:
        """Save cluster snapshot with hash-based deduplication."""
        timestamp = datetime.utcnow().isoformat()
        snapshot = state.get("cluster_snapshot", {})
        risk = state.get("risk_score", {})
        
        cluster_hash = self._hash_dict(snapshot)
        signal_hash = self._hash_list(state.get("signals", []))
        
        try:
            with self.conn:
                self.conn.execute("""
                INSERT INTO snapshots
                (timestamp, cluster_name, node_count, pod_count, signal_count, 
                 risk_score, risk_grade, cluster_state_hash, signal_state_hash, full_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    "default",
                    len(snapshot.get("nodes", [])),
                    len(snapshot.get("pods", [])),
                    len(state.get("signals", [])),
                    risk.get("score", 0),
                    risk.get("grade", "A"),
                    cluster_hash,
                    signal_hash,
                    json.dumps(state, default=str)
                ))
            logger.info(f"Snapshot saved: {timestamp}")
            return timestamp
        except sqlite3.IntegrityError:
            logger.info("Snapshot identical to previous - skipping")
            return timestamp
    
    def detect_drift(self, new_state: Dict[str, Any], compare_to: Optional[str] = None) -> List[Drift]:
        """Detect drifts between new state and previous snapshot."""
        if compare_to is None:
            row = self.conn.execute("""SELECT timestamp, full_state FROM snapshots 
                                      ORDER BY timestamp DESC LIMIT 1 OFFSET 1""").fetchone()
            if not row:
                logger.info("No previous snapshot for drift detection")
                return []
            compare_to = row["timestamp"]
            old_state = json.loads(row["full_state"])
        else:
            row = self.conn.execute("""SELECT full_state FROM snapshots WHERE timestamp = ?""", 
                                   (compare_to,)).fetchone()
            if not row:
                return []
            old_state = json.loads(row["full_state"])
        
        drifts = []
        timestamp = datetime.utcnow().isoformat()
        
        old_pods = {f"{p['namespace']}/{p['name']}": p for p in old_state.get("cluster_snapshot", {}).get("pods", [])}
        new_pods = {f"{p['namespace']}/{p['name']}": p for p in new_state.get("cluster_snapshot", {}).get("pods", [])}
        
        for key in old_pods:
            if key not in new_pods:
                drifts.append(Drift(
                    timestamp=timestamp,
                    drift_type="resource_change",
                    severity="critical",
                    resource_type="pod",
                    resource_key=key,
                    old_value="present",
                    new_value="absent",
                    description=f"Pod {key} deleted"
                ))
        
        for key in new_pods:
            if key in old_pods:
                old_status = old_pods[key].get("status", "Unknown")
                new_status = new_pods[key].get("status", "Unknown")
                if old_status != new_status:
                    drifts.append(Drift(
                        timestamp=timestamp,
                        drift_type="resource_change",
                        severity="high" if new_status in ["CrashLoopBackOff", "Failed"] else "low",
                        resource_type="pod",
                        resource_key=key,
                        old_value=old_status,
                        new_value=new_status,
                        description=f"Pod {key} status changed: {old_status} → {new_status}"
                    ))
        
        old_risk = old_state.get("risk_score", {}).get("score", 0)
        new_risk = new_state.get("risk_score", {}).get("score", 0)
        if abs(new_risk - old_risk) > 5:
            drifts.append(Drift(
                timestamp=timestamp,
                drift_type="risk_shift",
                severity="high" if new_risk > old_risk else "low",
                resource_type="cluster",
                resource_key="risk",
                old_value=str(old_risk),
                new_value=str(new_risk),
                description=f"Risk score changed: {old_risk} → {new_risk}"
            ))
        
        for drift in drifts:
            with self.conn:
                self.conn.execute("""
                INSERT INTO drifts
                (timestamp, drift_type, severity, resource_type, resource_key, old_value, new_value, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (drift.timestamp, drift.drift_type, drift.severity, drift.resource_type,
                      drift.resource_key, drift.old_value, drift.new_value, drift.description))
        
        logger.info(f"Detected {len(drifts)} drifts")
        return drifts
    
    def get_snapshots(self, limit: int = 10) -> List[Snapshot]:
        """Get recent snapshots."""
        rows = self.conn.execute("""
        SELECT timestamp, cluster_name, node_count, pod_count, signal_count, 
               risk_score, risk_grade, cluster_state_hash, signal_state_hash
        FROM snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """, (limit,)).fetchall()
        return [Snapshot(**dict(row)) for row in rows]
    
    def get_drifts(self, resource_key: Optional[str] = None, limit: int = 50) -> List[Drift]:
        """Get recent drifts, optionally filtered by resource."""
        if resource_key:
            rows = self.conn.execute("""
            SELECT timestamp, drift_type, severity, resource_type, resource_key, 
                   old_value, new_value, description
            FROM drifts
            WHERE resource_key = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """, (resource_key, limit)).fetchall()
        else:
            rows = self.conn.execute("""
            SELECT timestamp, drift_type, severity, resource_type, resource_key,
                   old_value, new_value, description
            FROM drifts
            ORDER BY timestamp DESC
            LIMIT ?
            """, (limit,)).fetchall()
        return [Drift(**dict(row)) for row in rows]
    
    def get_trend(self, metric: str = "risk_score", window: int = 10) -> List[Tuple[str, float]]:
        """Get metric trend over time."""
        column = "risk_score" if metric == "risk_score" else "signal_count"
        rows = self.conn.execute(f"""
        SELECT timestamp, {column}
        FROM snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """, (window,)).fetchall()
        return [(row["timestamp"], row[column]) for row in rows]
    
    def get_drift_severity_trend(self, window: int = 5) -> Dict[str, int]:
        """Get drift severity breakdown over recent snapshots."""
        rows = self.conn.execute("""
        SELECT severity, COUNT(*) as count
        FROM drifts
        WHERE timestamp IN (
            SELECT timestamp FROM snapshots ORDER BY timestamp DESC LIMIT ?
        )
        GROUP BY severity
        """, (window,)).fetchall()
        return {row["severity"]: row["count"] for row in rows}
    
    def close(self):
        """Close database connection."""
        self.conn.close()
    
    @staticmethod
    def _hash_dict(d: Dict[str, Any]) -> str:
        """SHA256 hash of dictionary (for deduplication)."""
        s = json.dumps(d, sort_keys=True, default=str)
        return hashlib.sha256(s.encode()).hexdigest()
    
    @staticmethod
    def _hash_list(lst: List[Any]) -> str:
        """SHA256 hash of list (for deduplication)."""
        s = json.dumps(lst, sort_keys=True, default=str)
        return hashlib.sha256(s.encode()).hexdigest()
    
    def analyze_drift(self, new_state: Dict[str, Any], compare_to: Optional[str] = None) -> Dict[str, Any]:
        """Comprehensive drift analysis with classification and grading."""
        drifts = self.detect_drift(new_state, compare_to)
        critical_lost, critical_risky, warnings, info = [], [], [], []
        
        for d in drifts:
            if d.drift_type == "resource_change":
                if d.resource_type == "pod":
                    if d.new_value == "absent":
                        critical_lost.append(d)
                    elif d.new_value in ["CrashLoopBackOff", "Failed"]:
                        critical_risky.append(d)
                    else:
                        (warnings if d.severity == "high" else info).append(d)
                elif d.resource_type == "node":
                    (critical_lost if d.new_value == "absent" else warnings).append(d)
                else:
                    (warnings if d.severity in ["high", "critical"] else info).append(d)
            elif d.drift_type == "signal_delta" and d.old_value == "not_detected":
                (critical_risky if d.severity == "critical" else warnings).append(d)
            elif d.drift_type == "risk_shift":
                (critical_risky if float(d.new_value) > float(d.old_value) else info).append(d)
        
        trend = _grade_trend(len(critical_lost), len(critical_risky), len(warnings), len(info))
        grade = _grade_drift(len(critical_lost), len(critical_risky), len(warnings))
        
        return {
            "drifts": [_drift_to_dict(d) for d in drifts],
            "summary": {
                "total_changes": len(drifts),
                "critical_lost_count": len(critical_lost),
                "critical_risky_count": len(critical_risky),
                "warning_count": len(warnings),
                "info_count": len(info),
                "trend": trend,
                "drift_severity_grade": grade,
                "categorized_drifts": {
                    "critical_lost": [_drift_to_dict(d) for d in critical_lost],
                    "critical_risky": [_drift_to_dict(d) for d in critical_risky],
                    "warnings": [_drift_to_dict(d) for d in warnings],
                    "info": [_drift_to_dict(d) for d in info]
                }
            }
        }

def drift_to_signals(drift_analysis: Dict[str, Any], signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert critical drifts to signals."""
    new_signals = signals.copy()
    summary = drift_analysis.get("summary", {})
    
    for drift in summary.get("categorized_drifts", {}).get("critical_lost", []):
        new_signals.append({
            "category": "reliability",
            "severity": "critical",
            "resource": f"drift/{drift['resource_key']}",
            "message": drift["description"],
            "signal_id": "pod_loss",
            "cis_control": None
        })
    
    for drift in summary.get("categorized_drifts", {}).get("critical_risky", []):
        if drift["drift_type"] == "risk_shift":
            new_signals.append({
                "category": "reliability",
                "severity": "high",
                "resource": "cluster",
                "message": drift["description"],
                "signal_id": "risk_shift",
                "cis_control": None
            })
    
    return new_signals

def _grade_drift(critical_lost: int, critical_risky: int, warnings: int) -> str:
    """Grade drift severity: A (stable) to F (chaotic)."""
    if critical_lost > 2 or critical_risky > 5:
        return "F"
    elif critical_lost == 1 or critical_risky >= 3:
        return "D"
    elif critical_lost == 0 and critical_risky > 0:
        return "C"
    elif warnings > 10:
        return "B"
    else:
        return "A"

def _grade_trend(lost: int, risky: int, warnings: int, info: int) -> str:
    """Determine cluster trend."""
    if lost > 0 or risky > 5:
        return "degrading"
    elif risky > 0 or warnings > 5:
        return "stable"
    else:
        return "improving" if info > 0 else "stable"

def _drift_to_dict(d: Drift) -> Dict[str, Any]:
    """Convert Drift to dict."""
    return {
        "timestamp": d.timestamp,
        "drift_type": d.drift_type,
        "severity": d.severity,
        "resource_type": d.resource_type,
        "resource_key": d.resource_key,
        "old_value": d.old_value,
        "new_value": d.new_value,
        "description": d.description
    }

