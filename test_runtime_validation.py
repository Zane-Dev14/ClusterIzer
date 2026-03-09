#!/usr/bin/env python3
"""Runtime validation for Phase N implementation."""

import logging
from kubesentinel.runtime import run_engine

# Enable detailed logging
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")

print("=" * 80)
print("RUNTIME VALIDATION: Phase N Implementation")
print("=" * 80)

# Run a test scan
print("\n1. Starting engine with security scan query...")
result = run_engine(user_query="security scan")

# Validate planner output
print("\n2. PLANNER VALIDATION:")
agents = result.get("planner_decision", [])
print(f"   ✓ Selected agents: {agents}")
metadata = result.get("planner_metadata", {})
print(f"   ✓ Metadata: {metadata}")

# Validate findings structure
print("\n3. FINDINGS STRUCTURE VALIDATION:")
failure_findings = result.get("failure_findings", [])
cost_findings = result.get("cost_findings", [])
security_findings = result.get("security_findings", [])

print(f"   - Failure findings: {len(failure_findings)}")
print(f"   - Cost findings: {len(cost_findings)}")
print(f"   - Security findings: {len(security_findings)}")

all_findings = failure_findings + cost_findings + security_findings
print(f"   → Total findings: {len(all_findings)}")

# Validate remediation field
print("\n4. REMEDIATION FIELD VALIDATION:")
findings_with_remediation = 0
findings_with_verification = 0
total_commands = 0

for finding in all_findings:
    if "remediation" in finding:
        findings_with_remediation += 1
        rem = finding["remediation"]
        if isinstance(rem.get("commands"), list):
            total_commands += len(rem["commands"])

    if "verification" in finding:
        findings_with_verification += 1

print(
    f"   ✓ Findings with remediation field: {findings_with_remediation}/{len(all_findings)}"
)
print(
    f"   ✓ Findings with verification field: {findings_with_verification}/{len(all_findings)}"
)
print(f"   ✓ Total remediation commands: {total_commands}")

# Validate synthesis
print("\n5. SYNTHESIZER VALIDATION:")
summary = result.get("strategic_summary", "")
print(f"   ✓ Strategic summary generated: {len(summary)} chars")

# Detailed finding inspection
print("\n6. SAMPLE FINDINGS INSPECTION:")
for i, finding in enumerate(all_findings[:2]):
    print(f"\n   Finding {i}:")
    print(f"     resource: {finding.get('resource')}")
    print(f"     severity: {finding.get('severity')}")
    print(f"     has remediation: {'remediation' in finding}")
    if "remediation" in finding:
        rem = finding["remediation"]
        cmds = rem.get("commands", [])
        print(f"       - commands: {len(cmds)} items")
        if cmds:
            for cmd in cmds[:2]:
                print(f"         • {cmd[:80]}...")
        print(f"       - automated: {rem.get('automated')}")
        print(f"       - risk_level: {rem.get('risk_level')}")
    if "verification" in finding:
        ver = finding["verification"]
        print(f"     has verification: {len(ver.get('commands', []))} items")
        print(f"       - automated: {ver.get('automated')}")

# Final status
print("\n" + "=" * 80)
print("VALIDATION SUMMARY")
print("=" * 80)

all_valid = True

if not agents:
    print("❌ No agents selected by planner")
    all_valid = False
else:
    print(f"✅ Planner selected {len(agents)} agents")

if findings_with_remediation != len(all_findings):
    print(
        f"❌ Only {findings_with_remediation}/{len(all_findings)} findings have remediation field"
    )
    all_valid = False
else:
    print(f"✅ All {len(all_findings)} findings have remediation field")

if total_commands > 0:
    print(f"✅ Generated {total_commands} remediation commands")
else:
    print("⚠️  No remediation commands generated")

print("\n" + "=" * 80)
if all_valid and len(all_findings) > 0:
    print("✅ PHASE N RUNTIME VALIDATION PASSED")
else:
    print(
        f"⚠️  VALIDATION INCOMPLETE (findings={len(all_findings)}, agents={len(agents)})"
    )
print("=" * 80)
