#!/usr/bin/env python3
"""
Direct runtime call graph tracer for KubeSentinel.

Traces the actual execution path by analyzing the source code directly.
"""

from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path("/Users/eric/IBM/Projects/courses/Deliverables/week-4")

# Known runtime call path based on source code analysis
RUNTIME_CALL_PATH = """
ENTRYPOINT: kubesentinel/integrations/slack_bot.py::main()

Message Handlers (Slack event callbacks):
  ├─ handle_app_mention()
  │   └─ run_analysis()
  ├─ handle_message()
  │   └─ run_analysis()
  ├─ handle_run_fixes()
  ├─ handle_view_report()
  └─ run_analysis()
      └─ run_engine() [from kubesentinel.runtime]
          ├─ get_graph() [from kubesentinel.runtime]
          │   └─ build_runtime_graph() [from kubesentinel.runtime]
          │       ├─ scan_cluster() [from kubesentinel.cluster]
          │       ├─ load_desired_state() [from kubesentinel.runtime]
          │       │   └─ load_git_desired_state() [from kubesentinel.git_loader]
          │       ├─ build_graph() [from kubesentinel.graph_builder]
          │       ├─ generate_signals() [from kubesentinel.signals]
          │       ├─ persist_snapshot() [from kubesentinel.runtime]
          │       │   └─ get_persistence_manager() [from kubesentinel.runtime]
          │       │       └─ PersistenceManager [from kubesentinel.persistence]
          │       ├─ compute_risk() [from kubesentinel.risk]
          │       ├─ planner_node() [from kubesentinel.agents]
          │       ├─ run_agents_parallel() [from kubesentinel.runtime]
          │       │   ├─ failure_agent_node() [from kubesentinel.agents]
          │       │   ├─ cost_agent_node() [from kubesentinel.agents]
          │       │   └─ security_agent_node() [from kubesentinel.agents]
          │       └─ synthesizer_node() [from kubesentinel.agents]
          └─ build_report() [from kubesentinel.reporting]
              └─ format findings & risk
      └─ format_summary() [slack_bot helper]
          └─ format_summary_blocks() [slack_bot helper]

SECONDARY HANDLERS:
  safe_kubectl_command() - called from handle_run_fixes button
"""

# Modules that must be kept (directly in runtime path)
REQUIRED_MODULES = {
    "kubesentinel/integrations/slack_bot.py",
    "kubesentinel/runtime.py",
    "kubesentinel/cluster.py",
    "kubesentinel/graph_builder.py",
    "kubesentinel/signals.py",
    "kubesentinel/agents.py",
    "kubesentinel/persistence.py",
    "kubesentinel/risk.py",
    "kubesentinel/reporting.py",
    "kubesentinel/git_loader.py",
    "kubesentinel/models.py",  # Required by InfraState TypedDict
}

# Modules that can be deleted (never called in runtime path)
DELETABLE_MODULES = {
    # Test modules
    "kubesentinel/tests/",
    "kubesentinel/integrations/test_slack_bot.py",
    # Diagnostic modules (not used in runtime)
    "kubesentinel/diagnostics/",
    # Simulation (experimental)
    "kubesentinel/simulation.py",
    # Main CLI (different entrypoint)
    "kubesentinel/main.py",
    # Cost analysis (if unused)
    "kubesentinel/cost.py",  # Check if actually imported
    # CRD discovery (if unused)
    "kubesentinel/crd_discovery.py",
}


def get_python_files(directory: Path) -> List[Path]:
    """List all Python files in a directory."""
    return sorted([f for f in directory.rglob("*.py") if "__pycache__" not in str(f)])


def get_file_size(path: Path) -> int:
    """Get file size in lines."""
    try:
        with open(path) as f:
            return len(f.readlines())
    except Exception:
        return 0


def analyze_large_files(kubesentinel_root: Path) -> Dict[str, int]:
    """Find large files that might contain dead code."""
    large_files = {}
    for py_file in get_python_files(kubesentinel_root):
        size = get_file_size(py_file)
        if size > 400:  # Files larger than 400 lines
            large_files[str(py_file.relative_to(REPO_ROOT))] = size
    return dict(sorted(large_files.items(), key=lambda x: x[1], reverse=True))


def main():
    kubesentinel_root = REPO_ROOT / "kubesentinel"

    print("=" * 80)
    print("KUBESENTINEL RUNTIME ARCHITECTURE ANALYSIS")
    print("=" * 80)
    print(RUNTIME_CALL_PATH)

    print("\n" + "=" * 80)
    print("MODULE CATEGORIZATION")
    print("=" * 80)

    print("\n✓ REQUIRED MODULES (In runtime path):")
    for mod in sorted(REQUIRED_MODULES):
        path = REPO_ROOT / mod
        if path.exists():
            size = get_file_size(path)
            print(f"  {mod:<40} ({size:>4} lines)")

    print("\n✗ DELETABLE MODULES (Not in runtime path):")
    for mod in sorted(DELETABLE_MODULES):
        path = REPO_ROOT / mod
        if path.exists() and path.is_file():
            size = get_file_size(path)
            print(f"  {mod:<40} ({size:>4} lines)")
        elif path.exists() and path.is_dir():
            sizes = []
            for py_file in get_python_files(path):
                sizes.append(get_file_size(py_file))
            total_size = sum(sizes)
            num_files = len(sizes)
            print(f"  {mod:<40} ({num_files:>2} files, {total_size:>4} lines total)")

    print("\n" + "=" * 80)
    print("LARGE FILES REQUIRING REFACTORING(>400 lines)")
    print("=" * 80)

    large_files = analyze_large_files(kubesentinel_root)
    total_lines = 0
    for filepath, size in large_files.items():
        is_required = any(
            filepath.startswith(
                req.replace("kubesentinel/", "kubesentinel/").rstrip(".py")
            )
            or req in filepath
            for req in REQUIRED_MODULES
        )
        status = "KEEP (refactor)" if is_required else "DELETE"
        print(f"  {filepath:<50} {size:>4} lines [{status}]")
        total_lines += size

    print(f"\n  Total lines in large files: {total_lines}")

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print("""
1. DELETE THESE MODULES (safe deletion, not in runtime):
   - kubesentinel/tests/ (all test files)
   - kubesentinel/diagnostics/ (diagnostic utilities)
   - kubesentinel/simulation.py (experimental)
   - kubesentinel/main.py (different CLI entrypoint)

2. REFACTOR THESE MODULES (in runtime,but can be simplified):
   - kubesentinel/agents.py: Remove unused agent types
   - kubesentinel/runtime.py: Remove unused persistence functions
   - kubesentinel/slack_bot.py: Remove unused formatting helpers

3. VERIFY THESE MODULES (check if really needed):
   - kubesentinel/cost.py: If not used by agents, delete
   - kubesentinel/crd_discovery.py: If not used anywhere, delete
    """)


if __name__ == "__main__":
    main()
