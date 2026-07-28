#!/usr/bin/env python3
"""Verify runtime dependencies without connecting to or moving a robot."""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    failures: list[str] = []
    print(f"Python: {platform.python_version()} ({platform.machine()}, {platform.system()})")
    for distribution, import_name in (
        ("pyaubo-sdk", "pyaubo_sdk"),
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("openai", "openai"),
    ):
        try:
            module = __import__(import_name)
            print(f"{distribution}: {importlib.metadata.version(distribution)} ({module.__file__})")
        except Exception as exc:
            failures.append(f"{distribution}: {exc}")
    from robot_game.config import load_study_config
    config = load_study_config(ROOT / "config" / "study.example.json")
    print(f"Study config: {config.version}, mode={config.robot_mode}, hash={config.config_hash}")
    print("Robot connection test: SKIPPED (read-only install verification)")
    if failures:
        print("Failures:", *failures, sep="\n  - ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
