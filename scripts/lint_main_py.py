#!/usr/bin/env python3
"""
lint_main_py.py — enforce main.py line count upper bound
=========================================================
Invariant #8: ``main.py`` < 200 lines.  Exceeding = CI failure.

Usage::

    python scripts/lint_main_py.py              # default: backend/main.py, limit 200
    python scripts/lint_main_py.py --limit 150  # custom limit
    python scripts/lint_main_py.py --file path/to/main.py

Exit codes:
    0  — pass (line count within limit)
    1  — fail (line count exceeds limit)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "backend" / "main.py"
DEFAULT_LIMIT = 200


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check that main.py stays under the line-count limit (Invariant #8)."
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help=f"Path to main.py (default: {DEFAULT_FILE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum allowed lines (default: {DEFAULT_LIMIT})",
    )
    args = parser.parse_args()

    target: Path = args.file
    limit: int = args.limit

    if not target.exists():
        print(f"ERROR: {target} not found")
        return 1

    line_count = len(target.read_text(encoding="utf-8").splitlines())

    if line_count >= limit:
        print(
            f"FAIL: {target.name} has {line_count} lines (limit: {limit}).\n"
            f"  Invariant #8: main.py must stay under {limit} lines.\n"
            f"  Extract routes to api/*.py or refactor."
        )
        return 1

    print(f"OK: {target.name} has {line_count} lines (limit: {limit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
