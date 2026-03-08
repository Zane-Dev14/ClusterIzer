#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

db_path = Path.home() / ".kubesentinel" / "kubesentinel.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT full_state FROM snapshots ORDER BY created_at DESC LIMIT 1")
row = cursor.fetchone()

if row:
    snapshot = json.loads(row["full_state"])
    signals = snapshot.get("signals", [])

    # Find crashloop signal
    for sig in signals:
        if sig.get("signal_id") == "crashloop_pod":
            print("Found crashloop signal:")
            print(f"  severity: {sig.get('severity')}")
            diagnosis = sig.get("diagnosis")
            if diagnosis:
                print(f"  diagnosis keys: {list(diagnosis.keys())}")
                print(f"  recommended_fix: {diagnosis.get('recommended_fix')}")
                print(f"  type: {diagnosis.get('type')}")
            else:
                print("  NO DIAGNOSIS!")
            break
