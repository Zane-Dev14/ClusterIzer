#!/usr/bin/env python3
"""Safe LOC reduction: strip multiline docstrings and compress whitespace."""

from pathlib import Path


def compress_file(filepath: Path) -> int:
    """Compress a Python file by removing docstrings and excess whitespace."""
    with open(filepath) as f:
        lines = f.readlines()

    compressed = []
    in_docstring = False
    docstring_char = None
    prev_blank = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect docstring start/end
        if not in_docstring:
            if stripped.startswith('"""') or stripped.startswith("'''"):
                docstring_char = stripped[:3]
                # Check if docstring ends on same line
                if stripped.count(docstring_char) >= 2:
                    # One-line docstring - keep if it's a function/class docstring
                    if i > 0 and any(
                        lines[i - 1].strip().startswith(x)
                        for x in ["def ", "class ", "@"]
                    ):
                        compressed.append(line)
                    continue
                else:
                    in_docstring = True
                    continue
        else:
            if docstring_char in stripped:
                in_docstring = False
                docstring_char = None
            continue

        # Skip consecutive blank lines
        if not stripped:
            if not prev_blank:
                compressed.append(line)
                prev_blank = True
            continue

        prev_blank = False
        compressed.append(line)

    # Write compressed version
    with open(filepath, "w") as f:
        f.writelines(compressed)

    return len(lines) - len(compressed)


if __name__ == "__main__":
    src_dir = Path("kubesentinel")
    total_saved = 0

    for py_file in src_dir.rglob("*.py"):
        if "__pycache__" in str(py_file) or "tests" in str(py_file):
            continue

        saved = compress_file(py_file)
        if saved > 0:
            print(f"✓ {py_file.name}: saved {saved} lines")
            total_saved += saved

    print(f"\n✅ Total LOC saved: {total_saved}")
