#!/usr/bin/env python3
"""
Debug script to check kubectl command extraction from findings.
Run this to see what commands are being extracted from the analysis results.
"""

from pathlib import Path
from kubesentinel.integrations.slack_bot import extract_kubectl_commands


def debug_recommendations():
    """Load the cached state from the last analysis and check recommendations."""

    # Try to load the report to see what's in it
    report_path = Path("report.md")
    if not report_path.exists():
        print("❌ No report.md found. Run analysis first: @KubeSentinel <query>")
        return

    report_content = report_path.read_text()

    # Print a sample of the report
    print("=" * 80)
    print("REPORT PREVIEW (first 2000 chars)")
    print("=" * 80)
    print(report_content[:2000])
    print("\n")

    # Extract all lines that look like recommendations
    lines = report_content.split("\n")
    recommendations = []

    for i, line in enumerate(lines):
        if (
            "kubectl" in line.lower()
            or "RECOMMENDED FIX" in line
            or "- First fix:" in line
        ):
            recommendations.append((i, line))

    if recommendations:
        print("=" * 80)
        print("LINES CONTAINING 'kubectl' OR RECOMMENDATIONS")
        print("=" * 80)
        for line_num, line in recommendations:
            print(f"Line {line_num}: {line}")
        print("\n")

    # Now test extraction on some sample recommendations
    print("=" * 80)
    print("TESTING COMMAND EXTRACTION")
    print("=" * 80)

    test_recommendations = [
        "kubectl describe deployment coredns -n kube-system",
        "kubectl get pods -n kube-system",
        "kubectl rollout restart deployment media-frontend -n social-network",
        "kubectl get deployment -n kube-system -o wide | awk '$2==1'",
        "Increase replica count to 3+ for production workloads",
        "kubectl scale deployment coredns --replicas=3 -n kube-system",
        "First fix: kubectl logs pod/media-frontend-123 -n social-network",
        "1. kubectl describe deployment; 2. kubectl get pods",
    ]

    for rec in test_recommendations:
        commands = extract_kubectl_commands(rec)
        status = "✅" if commands else "❌"
        print(f"\n{status} Input: {rec}")
        if commands:
            for cmd in commands:
                print(f"   → Extracted: kubectl {cmd}")
        else:
            print("   → No commands extracted")

    print("\n")
    print("=" * 80)
    print("HOW TO CHECK IF COMMANDS RAN")
    print("=" * 80)
    print("""
To verify kubectl commands actually executed:
1. Click the "Run Fixes" button in Slack
2. You should see output like:
   ✅ kubectl Commands Executed:
   `kubectl get deployment -n kube-system -o wide`
   ✅ Success:
   [actual output from kubectl]

3. If you see "📋 Recommended Commands:" instead:
   - The extraction didn't find any executable kubectl commands
   - The recommendations might be text-only advice
   - Check this debug output above to see what was/wasn't extracted

4. To manually check what kubectl would return:
   kubectl get deployment -n kube-system -o wide
    """)


if __name__ == "__main__":
    debug_recommendations()
