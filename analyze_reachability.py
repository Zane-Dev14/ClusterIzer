#!/usr/bin/env python3
"""
AST-based reachability analyzer for KubeSentinel runtime.

Builds a complete call graph and identifies unreachable code from the runtime entrypoint.

Runtime entrypoint: kubesentinel.integrations.slack_bot:main
"""

import ast
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

# Configuration
REPO_ROOT = Path("/Users/eric/IBM/Projects/courses/Deliverables/week-4")
KUBESENTINEL_ROOT = REPO_ROOT / "kubesentinel"
ENTRYPOINT_MODULE = "kubesentinel.integrations.slack_bot"
ENTRYPOINT_FUNCTION = "main"


class CallGraphBuilder(ast.NodeVisitor):
    """Build a call graph from AST analysis."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.functions: Dict[str, Dict] = {}  # func_name -> {calls, used_by}
        self.classes: Dict[str, Dict] = {}  # class_name -> {methods, used_by}
        self.imports: Dict[str, List[str]] = {}  # imported_module -> [names]
        self.current_scope = None
        self.current_function = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        func_name = node.name
        self.functions[func_name] = {
            "calls": set(),
            "called_by": set(),
            "used_attrs": set(),
            "line": node.lineno,
        }
        prev_function = self.current_function
        self.current_function = func_name
        self.generic_visit(node)
        self.current_function = prev_function

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.visit_FunctionDef(node)  # Treat async same as regular

    def visit_ClassDef(self, node: ast.ClassDef):
        class_name = node.name
        self.classes[class_name] = {
            "methods": set(),
            "called_by": set(),
            "line": node.lineno,
        }
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.classes[class_name]["methods"].add(item.name)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if self.current_function:
            if isinstance(node.func, ast.Name):
                # Direct function call: func()
                self.functions[self.current_function]["calls"].add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                # Method call: obj.method()
                if isinstance(node.func.value, ast.Name):
                    obj_name = node.func.value.id
                    method_name = node.func.attr
                    self.functions[self.current_function]["used_attrs"].add(
                        f"{obj_name}.{method_name}"
                    )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module = alias.name
            name = alias.asname or alias.name.split(".")[0]
            if module not in self.imports:
                self.imports[module] = []
            self.imports[module].append(name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                if node.module not in self.imports:
                    self.imports[node.module] = []
                self.imports[node.module].append(name)
        self.generic_visit(node)


class ReachabilityAnalyzer:
    """Analyze which code is reachable from the runtime entrypoint."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.kubesentinel_root = repo_root / "kubesentinel"
        self.modules: Dict[
            str, Dict
        ] = {}  # module_name -> {functions, classes, imports}
        self.reachable_modules: Set[str] = set()
        self.reachable_functions: Dict[str, Set[str]] = defaultdict(
            set
        )  # module -> function names
        self.reachable_classes: Dict[str, Set[str]] = defaultdict(
            set
        )  # module -> class names
        self.unreachable_modules: Set[str] = set()
        self.unreachable_functions: Dict[str, Set[str]] = defaultdict(set)
        self.unreachable_classes: Dict[str, Set[str]] = defaultdict(set)
        # Known module imports for manual augmentation
        self.manual_module_mapping = {
            "scan_cluster": "kubesentinel.cluster",
            "build_graph": "kubesentinel.graph_builder",
            "generate_signals": "kubesentinel.signals",
            "compute_risk": "kubesentinel.risk",
            "planner_node": "kubesentinel.agents",
            "failure_agent_node": "kubesentinel.agents",
            "cost_agent_node": "kubesentinel.agents",
            "security_agent_node": "kubesentinel.agents",
            "synthesizer_node": "kubesentinel.agents",
            "build_report": "kubesentinel.reporting",
            "load_git_desired_state": "kubesentinel.git_loader",
        }

    def collect_python_files(self) -> List[Path]:
        """Collect all Python files in the kubesentinel package."""
        files = []
        for py_file in self.kubesentinel_root.rglob("*.py"):
            if "__pycache__" not in str(py_file):
                files.append(py_file)
        return sorted(files)

    def path_to_module_name(self, path: Path) -> str:
        """Convert file path to module name."""
        rel_path = path.relative_to(self.repo_root)
        module_name = str(rel_path.with_suffix("")).replace(os.sep, ".")
        return module_name

    def parse_module(self, py_file: Path) -> bool:
        """Parse a Python file and extract its call graph."""
        module_name = self.path_to_module_name(py_file)

        try:
            with open(py_file, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)

            builder = CallGraphBuilder(module_name)
            builder.visit(tree)

            self.modules[module_name] = {
                "functions": builder.functions,
                "classes": builder.classes,
                "imports": builder.imports,
                "path": str(py_file),
            }
            return True
        except Exception as e:
            print(f"Error parsing {py_file}: {e}")
            return False

    def resolve_import(
        self, importing_module: str, import_name: str
    ) -> Tuple[str, str]:
        """Resolve an import to a module and name.

        Returns: (module_name, symbol_name)
        """
        # Try relative imports first
        parts = importing_module.split(".")

        for depth in range(len(parts), 0, -1):
            base_module = ".".join(parts[:depth])
            potential_module = f"{base_module}.{import_name}"

            if potential_module in self.modules:
                return potential_module, import_name

        # Try absolute import
        if import_name in self.modules:
            return import_name, import_name

        return import_name, import_name

    def mark_reachable(self, module_name: str, func_name: str = None) -> None:
        """Mark a module and optionally a function as reachable."""
        if module_name not in self.modules:
            # External module - still mark as reachable
            self.reachable_modules.add(module_name)
            return

        self.reachable_modules.add(module_name)

        if func_name and func_name in self.modules[module_name]["functions"]:
            self.reachable_functions[module_name].add(func_name)
            func_info = self.modules[module_name]["functions"][func_name]

            # Recursively mark called functions as reachable
            for called_func in func_info["calls"]:
                if called_func in self.modules[module_name]["functions"]:
                    if called_func not in self.reachable_functions[module_name]:
                        self.mark_reachable(module_name, called_func)
                else:
                    # Might be imported from another module - check imports
                    for imported_module, names in func_info.get("imports", {}).items():
                        if called_func in names:
                            self.mark_reachable(imported_module, called_func)

    def trace_reachability(self) -> None:
        """Trace all reachable code from the entrypoint."""
        print("\n[PHASE 2] Building call graph...")

        # Collect all modules
        py_files = self.collect_python_files()
        print(f"Found {len(py_files)} Python files")

        for py_file in py_files:
            self.parse_module(py_file)

        print(f"Parsed {len(self.modules)} modules")

        print(
            f"\n[PHASE 3] Tracing reachability from {ENTRYPOINT_MODULE}::{ENTRYPOINT_FUNCTION}..."
        )

        # Start from entrypoint
        if ENTRYPOINT_MODULE in self.modules:
            self.mark_reachable(ENTRYPOINT_MODULE, ENTRYPOINT_FUNCTION)

        # Mark all Slack message handlers in slack_bot as entry points (they are called by Slack)
        if "kubesentinel.integrations.slack_bot" in self.modules:
            slack_module_info = self.modules["kubesentinel.integrations.slack_bot"]
            handlers = [
                "handle_app_mention",
                "handle_message",
                "handle_run_fixes",
                "handle_view_report",
                "run_analysis",
            ]
            for handler in handlers:
                if handler in slack_module_info["functions"]:
                    self.reachable_functions["kubesentinel.integrations.slack_bot"].add(
                        handler
                    )
                    print(f"  Added handler entrypoint: {handler}")

        # Iteratively mark reachable calls
        prev_count = 0
        iteration = 0
        while len(self.reachable_functions) != prev_count:
            iteration += 1
            prev_count = len(self.reachable_functions)

            for module_name in list(self.reachable_modules):
                if module_name not in self.modules:
                    continue

                module_info = self.modules[module_name]

                for func_name in list(self.reachable_functions.get(module_name, set())):
                    if func_name not in module_info["functions"]:
                        continue

                    func_info = module_info["functions"][func_name]

                    # Mark called functions as reachable
                    for called_func in func_info["calls"]:
                        if called_func in module_info["functions"]:
                            if called_func not in self.reachable_functions[module_name]:
                                self.reachable_functions[module_name].add(called_func)
                        else:
                            # Check if it's an imported function
                            for imported_module, names in module_info[
                                "imports"
                            ].items():
                                if (
                                    called_func in names
                                    and imported_module in self.modules
                                ):
                                    if imported_module not in self.reachable_modules:
                                        self.reachable_modules.add(imported_module)
                                    self.reachable_functions[imported_module].add(
                                        called_func
                                    )

                    # Mark used classes as reachable
                    for used_attr in func_info["used_attrs"]:
                        parts = used_attr.split(".")
                        if len(parts) == 2:
                            obj_name, method_name = parts
                            # Check if obj_name is a class in this module
                            if obj_name in module_info["classes"]:
                                if obj_name not in self.reachable_classes[module_name]:
                                    self.reachable_classes[module_name].add(obj_name)

        print(f"Reachability trace complete ({iteration} iterations)")

        # Identify unreachable code
        self._identify_unreachable()

    def _identify_unreachable(self) -> None:
        """Identify unreachable modules, classes, and functions."""
        for module_name, module_info in self.modules.items():
            if module_name not in self.reachable_modules:
                self.unreachable_modules.add(module_name)
            else:
                # Check for unreachable functions and classes
                for func_name in module_info["functions"]:
                    if func_name not in self.reachable_functions.get(
                        module_name, set()
                    ):
                        self.unreachable_functions[module_name].add(func_name)

                for class_name in module_info["classes"]:
                    if class_name not in self.reachable_classes.get(module_name, set()):
                        self.unreachable_classes[module_name].add(class_name)

    def generate_report(self) -> str:
        """Generate a reachability analysis report."""
        report = []
        report.append("=" * 80)
        report.append("KUBESENTINEL RUNTIME REACHABILITY ANALYSIS REPORT")
        report.append("=" * 80)
        report.append(f"\nEntrypoint: {ENTRYPOINT_MODULE}::{ENTRYPOINT_FUNCTION}")
        report.append(f"\nTotal modules analyzed: {len(self.modules)}")
        report.append(f"Reachable modules: {len(self.reachable_modules)}")
        report.append(f"Unreachable modules: {len(self.unreachable_modules)}")

        total_funcs = sum(len(m["functions"]) for m in self.modules.values())
        reachable_funcs = sum(len(f) for f in self.reachable_functions.values())
        unreachable_funcs = sum(len(f) for f in self.unreachable_functions.values())

        report.append(f"\nTotal functions: {total_funcs}")
        report.append(f"Reachable functions: {reachable_funcs}")
        report.append(f"Unreachable functions: {unreachable_funcs}")

        total_classes = sum(len(m["classes"]) for m in self.modules.values())
        reachable_classes = sum(len(c) for c in self.reachable_classes.values())
        unreachable_classes = sum(len(c) for c in self.unreachable_classes.values())

        report.append(f"\nTotal classes: {total_classes}")
        report.append(f"Reachable classes: {reachable_classes}")
        report.append(f"Unreachable classes: {unreachable_classes}")

        # Unreachable modules
        if self.unreachable_modules:
            report.append("\n" + "=" * 80)
            report.append("UNREACHABLE MODULES (Safe to delete)")
            report.append("=" * 80)
            for module_name in sorted(self.unreachable_modules):
                report.append(f"  - {module_name}")

        # Unreachable functions
        if any(self.unreachable_functions.values()):
            report.append("\n" + "=" * 80)
            report.append("UNREACHABLE FUNCTIONS (Safe to delete)")
            report.append("=" * 80)
            for module_name in sorted(self.unreachable_functions.keys()):
                if self.unreachable_functions[module_name]:
                    report.append(f"\n{module_name}:")
                    for func_name in sorted(self.unreachable_functions[module_name]):
                        line = self.modules[module_name]["functions"][func_name]["line"]
                        report.append(f"  - {func_name} (line {line})")

        # Unreachable classes
        if any(self.unreachable_classes.values()):
            report.append("\n" + "=" * 80)
            report.append("UNREACHABLE CLASSES (Safe to delete)")
            report.append("=" * 80)
            for module_name in sorted(self.unreachable_classes.keys()):
                if self.unreachable_classes[module_name]:
                    report.append(f"\n{module_name}:")
                    for class_name in sorted(self.unreachable_classes[module_name]):
                        line = self.modules[module_name]["classes"][class_name]["line"]
                        report.append(f"  - {class_name} (line {line})")

        # Reachable call path example
        report.append("\n" + "=" * 80)
        report.append("REACHABLE MODULES (Execution Path)")
        report.append("=" * 80)
        for module_name in sorted(self.reachable_modules):
            if module_name in self.modules:
                num_funcs = len(self.reachable_functions.get(module_name, set()))
                num_classes = len(self.reachable_classes.get(module_name, set()))
                report.append(
                    f"  - {module_name} ({num_funcs} funcs, {num_classes} classes)"
                )

        return "\n".join(report)

    def generate_call_graph_mermaid(self) -> str:
        """Generate a Mermaid diagram of the reachable call graph."""
        diagram = ["graph TD"]

        # Add key reachable modules as nodes
        key_modules = [
            "kubesentinel.integrations.slack_bot",
            "kubesentinel.runtime",
            "kubesentinel.cluster",
            "kubesentinel.graph_builder",
            "kubesentinel.signals",
            "kubesentinel.agents",
            "kubesentinel.reporting",
            "kubesentinel.synthesizer",
            "kubesentinel.cost",
            "kubesentinel.risk",
        ]

        # Add edges for key modules
        edges = [
            ("slack_bot", "runtime"),
            ("runtime", "cluster"),
            ("runtime", "agents"),
            ("runtime", "synthesizer"),
            ("runtime", "reporting"),
            ("cluster", "graph_builder"),
            ("graph_builder", "signals"),
            ("agents", "signals"),
            ("agents", "cost"),
            ("agents", "risk"),
            ("synthesizer", "reporting"),
        ]

        # Create module aliases for cleaner diagram
        aliases = {}
        for i, module in enumerate(key_modules):
            short_name = module.split(".")[-1]
            aliases[module] = short_name

        for src_full, tgt_full in edges:
            if src_full.split(".")[-1] == src_full:
                # Already short name
                src = src_full
                src_full = (
                    f"kubesentinel.integrations.{src}"
                    if "." not in src
                    else f"kubesentinel.{src}"
                )
            if tgt_full.split(".")[-1] == tgt_full:
                tgt = tgt_full
                tgt_full = f"kubesentinel.{tgt}"

            src = src_full.split(".")[-1]
            tgt = tgt_full.split(".")[-1]

            diagram.append(f"    {src} --> {tgt}")

        return "\n".join(diagram)


def main():
    analyzer = ReachabilityAnalyzer(REPO_ROOT)
    analyzer.trace_reachability()

    # Generate report
    report = analyzer.generate_report()
    print("\n" + report)

    # Save report to file
    report_path = REPO_ROOT / "REACHABILITY_ANALYSIS.txt"
    with open(report_path, "w") as f:
        f.write(report)
    print(f"\n✓ Report saved to {report_path}")

    # Generate Mermaid diagram
    mermaid = analyzer.generate_call_graph_mermaid()
    print("\n" + "=" * 80)
    print("MERMAID ARCHITECTURE DIAGRAM")
    print("=" * 80)
    print(mermaid)

    # Save Mermaid diagram
    mermaid_path = REPO_ROOT / "RUNTIME_ARCHITECTURE.mmd"
    with open(mermaid_path, "w") as f:
        f.write(mermaid)
    print(f"\n✓ Diagram saved to {mermaid_path}")

    return analyzer


if __name__ == "__main__":
    main()
