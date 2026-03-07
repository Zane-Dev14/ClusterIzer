import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .signals import (
    DRIFT_CONFIG_CHANGE,
    DRIFT_EXTRA_RESOURCE,
    DRIFT_MISSING_RESOURCE,
)

logger = logging.getLogger(__name__)

DESIRED_RESOURCE_KEYS = [
    "deployments",
    "statefulsets",
    "daemonsets",
    "services",
    "pods",
    "configmaps",
    "secrets",
    "ingresses",
    "crds",
]

LIVE_KIND_BY_BUCKET = {
    "deployments": "deployment",
    "statefulsets": "statefulset",
    "daemonsets": "daemonset",
    "services": "service",
    "pods": "pod",
    "configmaps": "configmap",
    "secrets": "secret",
    "ingresses": "ingress",
    "crds": "customresourcedefinition",
}


@dataclass
class Snapshot:
    """Cluster snapshot metadata."""

    timestamp: str
    cluster_name: str
    node_count: int
    pod_count: int
    signal_count: int
    risk_score: float
    risk_grade: str
    cluster_state_hash: str
    signal_state_hash: str


@dataclass
class Drift:
    """Detected changes between snapshots."""

    timestamp: str
    drift_type: str
    severity: str
    resource_type: str
    resource_key: str
    old_value: str
    new_value: str
    description: str


class PersistenceManager:
    """SQLite-backed persistence for snapshots and drift."""

    def __init__(self, db_path: str = "~/.kubesentinel/kubesentinel.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info(f"Persistence initialized: {self.db_path}")

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
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
            """
            )

            self.conn.execute(
                """
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
            """
            )

            self.conn.execute(
                """
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
            """
            )

            self.conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
            ON snapshots(timestamp DESC)"""
            )
            self.conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_drifts_resource
            ON drifts(resource_key, drift_type)"""
            )

    def save_snapshot(self, state: Dict[str, Any]) -> str:
        """Save cluster snapshot with hash-based deduplication."""
        timestamp = datetime.utcnow().isoformat()
        snapshot = state.get("cluster_snapshot", {})
        risk = state.get("risk_score", {})

        cluster_hash = self._hash_dict(snapshot)
        signal_hash = self._hash_list(state.get("signals", []))

        try:
            with self.conn:
                self.conn.execute(
                    """
                INSERT INTO snapshots
                (timestamp, cluster_name, node_count, pod_count, signal_count,
                 risk_score, risk_grade, cluster_state_hash, signal_state_hash, full_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        timestamp,
                        "default",
                        len(snapshot.get("nodes", [])),
                        len(snapshot.get("pods", [])),
                        len(state.get("signals", [])),
                        risk.get("score", 0),
                        risk.get("grade", "A"),
                        cluster_hash,
                        signal_hash,
                        json.dumps(state, default=str),
                    ),
                )
            logger.info(f"Snapshot saved: {timestamp}")
            return timestamp
        except sqlite3.IntegrityError:
            logger.info("Snapshot identical to previous - skipping")
            return timestamp

    def detect_drift(
        self, new_state: Dict[str, Any], compare_to: Optional[str] = None
    ) -> List[Drift]:
        """Detect drifts between new state and previous snapshot."""
        if compare_to is None:
            row = self.conn.execute(
                """SELECT timestamp, full_state FROM snapshots
                                      ORDER BY timestamp DESC LIMIT 1 OFFSET 1"""
            ).fetchone()
            if not row:
                logger.info("No previous snapshot for drift detection")
                return []
            old_state = json.loads(row["full_state"])
        else:
            row = self.conn.execute(
                """SELECT full_state FROM snapshots WHERE timestamp = ?""",
                (compare_to,),
            ).fetchone()
            if not row:
                return []
            old_state = json.loads(row["full_state"])

        drifts: List[Drift] = []
        timestamp = datetime.utcnow().isoformat()

        old_pods = {
            f"{p['namespace']}/{p['name']}": p
            for p in old_state.get("cluster_snapshot", {}).get("pods", [])
        }
        new_pods = {
            f"{p['namespace']}/{p['name']}": p
            for p in new_state.get("cluster_snapshot", {}).get("pods", [])
        }

        for key in sorted(old_pods):
            if key not in new_pods:
                drifts.append(
                    Drift(
                        timestamp=timestamp,
                        drift_type="resource_change",
                        severity="critical",
                        resource_type="pod",
                        resource_key=key,
                        old_value="present",
                        new_value="absent",
                        description=f"Pod {key} deleted",
                    )
                )

        for key in sorted(new_pods):
            if key in old_pods:
                old_status = old_pods[key].get("status", "Unknown")
                new_status = new_pods[key].get("status", "Unknown")
                if old_status != new_status:
                    drifts.append(
                        Drift(
                            timestamp=timestamp,
                            drift_type="resource_change",
                            severity="high"
                            if new_status in ["CrashLoopBackOff", "Failed"]
                            else "low",
                            resource_type="pod",
                            resource_key=key,
                            old_value=old_status,
                            new_value=new_status,
                            description=f"Pod {key} status changed: {old_status} -> {new_status}",
                        )
                    )

        old_risk = old_state.get("risk_score", {}).get("score", 0)
        new_risk = new_state.get("risk_score", {}).get("score", 0)
        if abs(new_risk - old_risk) > 5:
            drifts.append(
                Drift(
                    timestamp=timestamp,
                    drift_type="risk_shift",
                    severity="high" if new_risk > old_risk else "low",
                    resource_type="cluster",
                    resource_key="risk",
                    old_value=str(old_risk),
                    new_value=str(new_risk),
                    description=f"Risk score changed: {old_risk} -> {new_risk}",
                )
            )

        _persist_drifts(self.conn, drifts)
        logger.info(f"Detected {len(drifts)} drifts")
        return drifts

    def get_snapshots(self, limit: int = 10) -> List[Snapshot]:
        rows = self.conn.execute(
            """
        SELECT timestamp, cluster_name, node_count, pod_count, signal_count,
               risk_score, risk_grade, cluster_state_hash, signal_state_hash
        FROM snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """,
            (limit,),
        ).fetchall()
        return [Snapshot(**dict(row)) for row in rows]

    def get_drifts(
        self, resource_key: Optional[str] = None, limit: int = 50
    ) -> List[Drift]:
        if resource_key:
            rows = self.conn.execute(
                """
            SELECT timestamp, drift_type, severity, resource_type, resource_key,
                   old_value, new_value, description
            FROM drifts
            WHERE resource_key = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
                (resource_key, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
            SELECT timestamp, drift_type, severity, resource_type, resource_key,
                   old_value, new_value, description
            FROM drifts
            ORDER BY timestamp DESC
            LIMIT ?
            """,
                (limit,),
            ).fetchall()
        return [Drift(**dict(row)) for row in rows]

    def get_trend(
        self, metric: str = "risk_score", window: int = 10
    ) -> List[Tuple[str, float]]:
        column = "risk_score" if metric == "risk_score" else "signal_count"
        rows = self.conn.execute(
            f"""
        SELECT timestamp, {column}
        FROM snapshots
        ORDER BY timestamp DESC
        LIMIT ?
        """,
            (window,),
        ).fetchall()
        return [(row["timestamp"], row[column]) for row in rows]

    def get_drift_severity_trend(self, window: int = 5) -> Dict[str, int]:
        rows = self.conn.execute(
            """
        SELECT severity, COUNT(*) as count
        FROM drifts
        WHERE timestamp IN (
            SELECT timestamp FROM snapshots ORDER BY timestamp DESC LIMIT ?
        )
        GROUP BY severity
        """,
            (window,),
        ).fetchall()
        return {row["severity"]: row["count"] for row in rows}

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _hash_dict(d: Dict[str, Any]) -> str:
        s = json.dumps(d, sort_keys=True, default=str)
        return hashlib.sha256(s.encode()).hexdigest()

    @staticmethod
    def _hash_list(lst: List[Any]) -> str:
        s = json.dumps(lst, sort_keys=True, default=str)
        return hashlib.sha256(s.encode()).hexdigest()

    def analyze_drift(
        self, new_state: Dict[str, Any], compare_to: Optional[str] = None
    ) -> Dict[str, Any]:
        """Comprehensive drift analysis with classification and grading."""
        drifts = self.detect_drift(new_state, compare_to)
        desired_drift: Dict[str, List[Dict[str, Any]]] = {
            "missing": [],
            "extra": [],
            "changed": [],
        }

        desired_snapshot = new_state.get("_desired_state_snapshot")
        if desired_snapshot:
            desired_drift = compare_live_vs_desired(
                new_state.get("cluster_snapshot", {}), desired_snapshot
            )
            desired_records = _desired_drift_to_records(desired_drift)
            drifts.extend(desired_records)
            _persist_drifts(self.conn, desired_records)

        critical_lost: List[Drift] = []
        critical_risky: List[Drift] = []
        warnings: List[Drift] = []
        info: List[Drift] = []

        for drift in drifts:
            if drift.drift_type in {"resource_change", "missing_resource"}:
                if drift.resource_type in {"pod", "deployment", "statefulset"}:
                    if drift.new_value == "absent" or drift.old_value == "absent":
                        critical_lost.append(drift)
                    elif drift.new_value in ["CrashLoopBackOff", "Failed"]:
                        critical_risky.append(drift)
                    else:
                        (warnings if drift.severity == "high" else info).append(drift)
                else:
                    (warnings if drift.severity in ["high", "critical"] else info).append(
                        drift
                    )
            elif drift.drift_type in {"spec_drift", "label_drift", "replica_drift"}:
                warnings.append(drift)
            elif drift.drift_type == "extra_resource":
                info.append(drift)
            elif drift.drift_type == "signal_delta" and drift.old_value == "not_detected":
                (critical_risky if drift.severity == "critical" else warnings).append(
                    drift
                )
            elif drift.drift_type == "risk_shift":
                (
                    critical_risky
                    if float(drift.new_value) > float(drift.old_value)
                    else info
                ).append(drift)

        trend = _grade_trend(
            len(critical_lost), len(critical_risky), len(warnings), len(info)
        )
        grade = _grade_drift(len(critical_lost), len(critical_risky), len(warnings))

        return {
            "drifts": [_drift_to_dict(d) for d in drifts],
            "desired_drift": desired_drift,
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
                    "info": [_drift_to_dict(d) for d in info],
                },
            },
        }


def compare_live_vs_desired(
    live: Dict[str, Any], desired: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Compare live snapshot against desired state and return deterministic drift report."""
    live_index = _index_live_resources(live)
    desired_index = _index_desired_resources(desired)

    missing: List[Dict[str, Any]] = []
    extra: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []

    all_keys = sorted(set(live_index) | set(desired_index))
    for key in all_keys:
        live_resource = live_index.get(key)
        desired_resource = desired_index.get(key)
        resource_key = f"{key[0]}/{key[1]}/{key[2]}"

        if desired_resource and not live_resource:
            missing.append(
                {
                    "drift_type": "missing_resource",
                    "severity": "high",
                    "resource_key": resource_key,
                    "kind": key[0],
                    "namespace": key[1],
                    "name": key[2],
                    "description": f"Desired resource missing from cluster: {resource_key}",
                }
            )
            continue

        if live_resource and not desired_resource:
            extra.append(
                {
                    "drift_type": "extra_resource",
                    "severity": "low",
                    "resource_key": resource_key,
                    "kind": key[0],
                    "namespace": key[1],
                    "name": key[2],
                    "description": f"Live resource not found in desired state: {resource_key}",
                }
            )
            continue

        assert live_resource is not None
        assert desired_resource is not None

        desired_spec = desired_resource.get("spec", {})
        live_spec = _extract_live_spec(live_resource, key[0])
        if live_spec != desired_spec:
            changed.append(
                {
                    "drift_type": "spec_drift",
                    "severity": "medium",
                    "resource_key": resource_key,
                    "kind": key[0],
                    "namespace": key[1],
                    "name": key[2],
                    "old_value": live_spec,
                    "new_value": desired_spec,
                    "description": f"Spec drift detected for {resource_key}",
                }
            )

        live_labels = live_resource.get("labels", {})
        desired_labels = desired_resource.get("labels", {})
        if live_labels != desired_labels:
            changed.append(
                {
                    "drift_type": "label_drift",
                    "severity": "medium",
                    "resource_key": resource_key,
                    "kind": key[0],
                    "namespace": key[1],
                    "name": key[2],
                    "old_value": live_labels,
                    "new_value": desired_labels,
                    "description": f"Label drift detected for {resource_key}",
                }
            )

        live_replicas = _extract_replicas(live_resource, key[0], source="live")
        desired_replicas = _extract_replicas(desired_resource, key[0], source="desired")
        if (
            live_replicas is not None
            and desired_replicas is not None
            and live_replicas != desired_replicas
        ):
            changed.append(
                {
                    "drift_type": "replica_drift",
                    "severity": "medium",
                    "resource_key": resource_key,
                    "kind": key[0],
                    "namespace": key[1],
                    "name": key[2],
                    "old_value": live_replicas,
                    "new_value": desired_replicas,
                    "description": (
                        f"Replica drift detected for {resource_key}: "
                        f"{live_replicas} -> {desired_replicas}"
                    ),
                }
            )

    missing.sort(key=lambda d: d["resource_key"])
    extra.sort(key=lambda d: d["resource_key"])
    changed.sort(key=lambda d: (d["resource_key"], d["drift_type"]))
    return {"missing": missing, "extra": extra, "changed": changed}


def drift_to_signals(
    drift_analysis: Dict[str, Any], signals: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Convert drift analysis results into deduplicated signals."""
    new_signals = signals.copy()
    seen = {(s.get("signal_id"), s.get("resource")) for s in new_signals}
    summary = drift_analysis.get("summary", {})

    for drift in summary.get("categorized_drifts", {}).get("critical_lost", []):
        signal = {
            "category": "reliability",
            "severity": "critical",
            "resource": f"drift/{drift['resource_key']}",
            "message": drift["description"],
            "signal_id": "pod_loss",
            "cis_control": None,
        }
        key = (signal["signal_id"], signal["resource"])
        if key not in seen:
            seen.add(key)
            new_signals.append(signal)

    for drift in summary.get("categorized_drifts", {}).get("critical_risky", []):
        if drift["drift_type"] == "risk_shift":
            signal = {
                "category": "reliability",
                "severity": "high",
                "resource": "cluster",
                "message": drift["description"],
                "signal_id": "risk_shift",
                "cis_control": None,
            }
            key = (signal["signal_id"], signal["resource"])
            if key not in seen:
                seen.add(key)
                new_signals.append(signal)

    for drift in drift_analysis.get("desired_drift", {}).get("missing", []):
        signal = {
            "category": "reliability",
            "severity": "high",
            "resource": f"drift/{drift['resource_key']}",
            "message": drift["description"],
            "signal_id": DRIFT_MISSING_RESOURCE,
            "cis_control": None,
        }
        key = (signal["signal_id"], signal["resource"])
        if key not in seen:
            seen.add(key)
            new_signals.append(signal)

    for drift in drift_analysis.get("desired_drift", {}).get("extra", []):
        signal = {
            "category": "reliability",
            "severity": "low",
            "resource": f"drift/{drift['resource_key']}",
            "message": drift["description"],
            "signal_id": DRIFT_EXTRA_RESOURCE,
            "cis_control": None,
        }
        key = (signal["signal_id"], signal["resource"])
        if key not in seen:
            seen.add(key)
            new_signals.append(signal)

    for drift in drift_analysis.get("desired_drift", {}).get("changed", []):
        signal = {
            "category": "reliability",
            "severity": "medium",
            "resource": f"drift/{drift['resource_key']}",
            "message": drift["description"],
            "signal_id": DRIFT_CONFIG_CHANGE,
            "cis_control": None,
        }
        key = (signal["signal_id"], signal["resource"])
        if key not in seen:
            seen.add(key)
            new_signals.append(signal)

    return new_signals


def _persist_drifts(conn: sqlite3.Connection, drifts: List[Drift]) -> None:
    for drift in drifts:
        with conn:
            conn.execute(
                """
            INSERT INTO drifts
            (timestamp, drift_type, severity, resource_type, resource_key, old_value, new_value, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    drift.timestamp,
                    drift.drift_type,
                    drift.severity,
                    drift.resource_type,
                    drift.resource_key,
                    drift.old_value,
                    drift.new_value,
                    drift.description,
                ),
            )


def _index_live_resources(
    live: Dict[str, Any],
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    indexed: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for bucket, kind in LIVE_KIND_BY_BUCKET.items():
        for item in live.get(bucket, []):
            name = str(item.get("name", ""))
            if not name:
                continue
            namespace = str(item.get("namespace") or "_cluster")
            key = (kind, namespace, name)
            indexed[key] = item
    return indexed


def _index_desired_resources(
    desired: Dict[str, List[Dict[str, Any]]],
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    indexed: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for bucket in DESIRED_RESOURCE_KEYS:
        for item in desired.get(bucket, []):
            kind = str(item.get("kind", "")).lower()
            name = str(item.get("name", ""))
            if not kind or not name:
                continue
            namespace = str(item.get("namespace") or "_cluster")
            key = (kind, namespace, name)
            indexed[key] = item
    return indexed


def _extract_live_spec(resource: Dict[str, Any], kind: str) -> Dict[str, Any]:
    if isinstance(resource.get("spec"), dict):
        return resource.get("spec", {})
    if kind in {"deployment", "statefulset"}:
        return {
            "replicas": resource.get("replicas", 1),
            "selector": resource.get("selector", {}),
            "template": {
                "metadata": {"labels": resource.get("pod_labels", {})},
                "spec": {"containers": resource.get("containers", [])},
            },
        }
    if kind == "daemonset":
        return {
            "selector": resource.get("selector", {}),
            "updateStrategy": {"type": resource.get("update_strategy", "RollingUpdate")},
            "template": {
                "metadata": {"labels": resource.get("pod_labels", {})},
                "spec": {"containers": resource.get("containers", [])},
            },
        }
    if kind == "service":
        return {
            "type": resource.get("type", "ClusterIP"),
            "selector": resource.get("selector", {}),
        }
    return {}


def _extract_replicas(
    resource: Dict[str, Any], kind: str, source: str
) -> Optional[int]:
    if kind not in {"deployment", "statefulset"}:
        return None
    if source == "live":
        return int(resource.get("replicas", 1))
    spec = resource.get("spec") if isinstance(resource.get("spec"), dict) else {}
    return int(spec.get("replicas", 1))


def _desired_drift_to_records(
    desired_drift: Dict[str, List[Dict[str, Any]]],
) -> List[Drift]:
    timestamp = datetime.utcnow().isoformat()
    out: List[Drift] = []

    for item in desired_drift.get("missing", []):
        out.append(
            Drift(
                timestamp=timestamp,
                drift_type="missing_resource",
                severity="high",
                resource_type=item.get("kind", "resource"),
                resource_key=item["resource_key"],
                old_value="absent",
                new_value="present",
                description=item["description"],
            )
        )
    for item in desired_drift.get("extra", []):
        out.append(
            Drift(
                timestamp=timestamp,
                drift_type="extra_resource",
                severity="low",
                resource_type=item.get("kind", "resource"),
                resource_key=item["resource_key"],
                old_value="present",
                new_value="absent",
                description=item["description"],
            )
        )
    for item in desired_drift.get("changed", []):
        out.append(
            Drift(
                timestamp=timestamp,
                drift_type=item.get("drift_type", "config_drift"),
                severity="medium",
                resource_type=item.get("kind", "resource"),
                resource_key=item["resource_key"],
                old_value=json.dumps(item.get("old_value"), sort_keys=True, default=str),
                new_value=json.dumps(item.get("new_value"), sort_keys=True, default=str),
                description=item["description"],
            )
        )

    out.sort(key=lambda d: (d.resource_key, d.drift_type))
    return out


def _grade_drift(critical_lost: int, critical_risky: int, warnings: int) -> str:
    if critical_lost > 2 or critical_risky > 5:
        return "F"
    if critical_lost == 1 or critical_risky >= 3:
        return "D"
    if critical_lost == 0 and critical_risky > 0:
        return "C"
    if warnings > 10:
        return "B"
    return "A"


def _grade_trend(lost: int, risky: int, warnings: int, info: int) -> str:
    if lost > 0 or risky > 5:
        return "degrading"
    if risky > 0 or warnings > 5:
        return "stable"
    return "improving" if info > 0 else "stable"


def _drift_to_dict(drift: Drift) -> Dict[str, Any]:
    return {
        "timestamp": drift.timestamp,
        "drift_type": drift.drift_type,
        "severity": drift.severity,
        "resource_type": drift.resource_type,
        "resource_key": drift.resource_key,
        "old_value": drift.old_value,
        "new_value": drift.new_value,
        "description": drift.description,
    }
