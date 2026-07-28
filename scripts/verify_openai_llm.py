#!/usr/bin/env python3
"""Run one paid OpenAI emotion-planning preflight without connecting to the robot."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot_game.config import load_study_config
from robot_game.event_logger import EventLogger
from robot_game.models import Condition, Outcome, RoundRecord, SessionState, Stage
from robot_game.safety_validator import SafetyValidator
from robot_game.strategies import build_strategy


async def run(config_path: Path) -> int:
    config = load_study_config(config_path)
    if config.llm.get("provider") != "openai":
        raise RuntimeError("Preflight requires llm.provider=openai")
    key_name = str(config.llm.get("api_key_env", "OPENAI_API_KEY"))
    if not os.getenv(key_name, ""):
        raise RuntimeError(f"Set {key_name} before running this paid API preflight")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"openai-preflight-{timestamp}"
    logger = EventLogger(config.log_root, session_id, {
        "participant_id": "OPENAI-PREFLIGHT",
        "session_id": session_id,
        "condition": "llm",
        "config_version": config.version,
    })
    try:
        state = SessionState("OPENAI-PREFLIGHT", session_id, Condition.LLM_BASED, False)
        state.round_id = 3
        state.history.extend([
            RoundRecord(1, 1, Outcome.LOSS, ("encouragement",)),
            RoundRecord(2, 2, Outcome.LOSS, ("hint",)),
        ])
        state.pending_outcome = Outcome.WIN
        state.pending_user_response = ("encouragement",)
        strategy = build_strategy(Condition.LLM_BASED, config, logger)
        plan = await strategy.plan(Stage.POST_RESULT_EXPRESSION, state)
        validated = SafetyValidator(config, logger).validate_plan(
            plan, Stage.POST_RESULT_EXPRESSION, state.round_id
        )
        logger.write_summary({
            "preflight": "passed",
            "provider": type(strategy.client).__name__,
            "model": getattr(strategy.client, "model", None),
            "validated_plan": validated.as_dict(),
        })
        print(json.dumps(validated.as_dict(), ensure_ascii=False, indent=2))
        print(f"PASS: OpenAI emotion plan validated; logs: {logger.directory}")
        return 0
    finally:
        logger.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call OpenAI once and validate an affective action without robot motion"
    )
    parser.add_argument(
        "--study-config", type=Path,
        default=ROOT / "config" / "study.example.json",
    )
    try:
        return asyncio.run(run(parser.parse_args().study_config))
    except Exception as exc:
        print(f"OpenAI preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
