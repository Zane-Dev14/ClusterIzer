#!/usr/bin/env python3
"""
Analyze unused functions within runtime modules.

Find functions that are defined but never called within the runtime path.
"""

import ast
from pathlib import Path
from typing import Dict, Set

REPO_ROOT = Path("/Users/eric/IBM/Projects/courses/Deliverables/week-4")
KUBESENTINEL_ROOT = REPO_ROOT / "kubesentinel"

# Runtime modules to analyze
RUNTIME_MODULES = {
    "kubesentinel/agents.py",
    "kubesentinel/cluster.py",
    "kubesentinel/git_loader.py",
    "kubesentinel/graph_builder.py",
    "kubesentinel/integrations/slack_bot.py",
    "kubesentinel/models.py",
    "kubesentinel/persistence.py",
    "kubesentinel/reporting.py",
    "kubesentinel/risk.py",
    "kubesentinel/runtime.py",
    "kubesentinel/signals.py",
}


class FunctionUsageAnalyzer(ast.NodeVisitor):
    def __init__(self, module_path: Path):
        self.module_path = module_path
        self.functions_defined: Dict[str, int] = {}  # name -> line_number
        self.functions_called: Set[str] = set()
        self.classes_defined: Set[str] = set()
        self.class_methods_called: Dict[str, Set[str]] = {}  # class_name -> {methods}
        self.current_class = None
        self.in_docstring = False

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.functions_defined[node.name] = node.lineno
        prev_class = self.current_class
        self.current_class = None
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        self.classes_defined.add(node.name)
        prev_class = self.current_class
        self.current_class = node.name
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_name = f"{node.name}.{item.name}"
                self.functions_defined[func_name] = item.lineno
        self.generic_visit(node)
        self.current_class = prev_class

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            self.functions_called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                obj = node.func.value.id
                method = node.func.attr
                if obj not in self.class_methods_called:
                    self.class_methods_called[obj] = set()
                self.class_methods_called[obj].add(method)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        # Catch function references (not just calls)
        if isinstance(node.ctx, ast.Load):
            self.functions_called.add(node.id)
        self.generic_visit(node)


def analyze_module(path: Path) -> Dict:
    """Analyze function usage in a module."""
    try:
        with open(path) as f:
            source = f.read()
        tree = ast.parse(source)
        analyzer = FunctionUsageAnalyzer(path)
        analyzer.visit(tree)

        # Find unused functions
        unused = {}
        for func_name, line_no in analyzer.functions_defined.items():
            # Skip private functions and special methods
            if func_name.startswith("_"):
                continue
            if func_name.endswith("__"):
                continue
            # Check if called
            base_name = func_name.split(".")[-1]
            if base_name not in analyzer.functions_called:
                unused[func_name] = line_no

        return {
            "defined": analyzer.functions_defined,
            "called": analyzer.functions_called,
            "unused": unused,
            "classes": analyzer.classes_defined,
        }
    except Exception as e:
        print(f"Error analyzing {path}: {e}")
        return {"error": str(e)}


def main():
    print("=" * 80)
    print("UNUSED FUNCTION ANALYSIS - RUNTIME MODULES")
    print("=" * 80)

    total_unused = 0
    all_unused = {}

    for module_rel_path in sorted(RUNTIME_MODULES):
        module_path = REPO_ROOT / module_rel_path
        if not module_path.exists():
            print(f"\n⚠ {module_rel_path} - NOT FOUND")
            continue

        analysis = analyze_module(module_path)

        if "error" in analysis:
            print(f"\n✗ {module_rel_path} - ERROR: {analysis['error']}")
            continue

        unused = analysis["unused"]

        if unused:
            total_unused += len(unused)
            all_unused[module_rel_path] = unused
            print(f"\n{module_rel_path}")
            print(f"  Functions defined: {len(analysis['defined'])}")
            print(f"  Functions used: {len(analysis['called'])}")
            print(f"  ⚠ Unused functions: {len(unused)}")
            for func_name, line_no in sorted(unused.items()):
                print(f"    - {func_name:<40} (line {line_no})")
        else:
            print(f"\n✓ {module_rel_path}")
            print(f"  Functions defined: {len(analysis['defined'])}")
            print(f"  Functions used: {len(analysis['called'])}")
            print("  All functions appear to be used")

    print("\n" + "=" * 80)
    print(f"TOTAL UNUSED FUNCTIONS: {total_unused}")
    print("=" * 80)
    print("""
NOTE: This analysis is conservative. Functions may be:
  1. Called via string names (reflection)
  2. Used as callbacks/decorators
  3. Part of public API
  4. Called from slack_bot handlers dynamically

Always validate by running: uv run kubesentinel-slack
    """)


if __name__ == "__main__":
    main()
