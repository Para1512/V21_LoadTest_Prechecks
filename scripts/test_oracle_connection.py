#!/usr/bin/env python3
"""CLI Oracle connection test — run on VDI after copying project and setting .env."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tasks.env_loader import load_project_env

load_project_env(create_from_example=True)

from tasks.oracle_client import test_connection


def main() -> int:
    print("Oracle connection test (backend / CLI)\n")
    result = test_connection()
    for line in result.get("messages") or []:
        print(line)
    print()
    if result.get("connected"):
        print("OK — backend can reach Oracle.")
        return 0
    print("FAIL —", result.get("message") or "connection failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
