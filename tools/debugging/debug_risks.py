#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

db_path = Path.home() / ".kubesentinel" / "kubesentinel.db"
if db_path.exists():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT id, data FROM snapshots ORDER BY created_at DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        snapshot_data = json.loads(row["data"])
        risk_analysis = snapshot_data.get("_risk_analysis", {})
        top_risks = risk_analysis.get("top_risks", [])

        print(f"Total top risks returned: {len(top_risks)}\n")
        for i, risk in enumerate(top_risks, 1):
            print(f"{i}. {risk.get('title')}")
            print(f"   Severity: {risk.get('severity')}")
            print(f"   Category: {risk.get('category')}")
            print(f"   Affected: {risk.get('affected_count')}")
            print(f"   Impact Score: {risk.get('impact_score')}")
            print(f"   Has diagnosis: {bool(risk.get('diagnosis'))}")
            if risk.get("diagnosis"):
                print(f"   Diagnosis type: {risk.get('diagnosis', {}).get('type')}")
            print()

        signals = snapshot_data.get("signals", [])
        crashloop_signals = [
            s for s in signals if s.get("signal_id") == "crashloop_pod"
        ]
        print(f"\nFound {len(crashloop_signals)} crashloop signals")
        if crashloop_signals:
            sig = crashloop_signals[0]
            print(f"Severity: {sig.get('severity')}")
            print(f"Category: {sig.get('category')}")
            print(f"Has diagnosis: {bool(sig.get('diagnosis'))}")
