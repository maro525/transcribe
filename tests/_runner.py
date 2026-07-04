"""Minimal test runner for environments without pytest.

Each test module stays pytest-compatible (plain ``test_*`` functions with
asserts) but can also be executed directly:

    python3 tests/test_keywords.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_module(module_globals: dict) -> None:
    tests = [
        (name, fn)
        for name, fn in sorted(module_globals.items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"--- {len(tests) - failures}/{len(tests)} passed ---")
    if failures:
        raise SystemExit(1)
