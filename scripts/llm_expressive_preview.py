#!/usr/bin/env python3
"""Scheme-specific LLM expressive planner preview; never connects to robot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions.expressive_common import (
    NEUTRAL_EMOTION_STATE,
    append_jsonl,
    call_structured_llm,
    expected_transition_type,
    normalize_result,
)

try:
    from scripts import llm_expressive_scheme as scheme
except ImportError as exc:
    raise SystemExit(
        "当前分支没有scripts/llm_expressive_scheme.py；"
        "请切换到一个方案分支"
    ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{scheme.SCHEME_NAME}：仅生成和校验计划，不连接机械臂"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    )
    parser.add_argument("--prompt-file", default=str(scheme.DEFAULT_PROMPT))
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--max-retries", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_rounds < 0 or args.max_retries < 0:
        print("max-rounds和max-retries不能小于0", file=sys.stderr)
        return 2
    try:
        system_prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"读取Prompt失败：{exc}", file=sys.stderr)
        return 2

    print(f"模式：{scheme.SCHEME_NAME}（安全预览，不连接机械臂）")
    print("每轮输入：1=猜对，0=猜错，e=结束")
    history: list[str] = []
    previous_emotion_state: dict[str, Any] = dict(NEUTRAL_EMOTION_STATE)
    previous_plan: dict[str, Any] | None = None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "logs" / f"{scheme.SCHEME_ID}_preview_{timestamp}.jsonl"

    while not args.max_rounds or len(history) < args.max_rounds:
        text = input(f"\n第{len(history) + 1}轮结果 [1/0/e]：").strip()
        if text.lower() == "e":
            break
        current_result = normalize_result(text)
        if current_result is None:
            print("输入无效，请输入1、0或e。")
            continue
        candidate_history = history + [current_result]
        base_input = {
            "current_result": current_result,
            "result_history": candidate_history,
            "transition_type": expected_transition_type(candidate_history),
            "previous_emotion_state": previous_emotion_state,
            "previous_plan": previous_plan,
        }
        round_input = scheme.build_round_input(base_input)
        print("正在调用LLM生成候选计划……", flush=True)
        try:
            plan = call_structured_llm(
                plan_type=scheme.PlanModel,
                model=args.model,
                system_prompt=system_prompt,
                round_input=round_input,
                max_retries=args.max_retries,
                validator=scheme.validate_plan,
                temperature=scheme.TEMPERATURE,
            )
        except Exception as exc:
            print(f"本轮LLM调用或本地校验失败：{exc}", file=sys.stderr)
            print("本轮不会写入历史。")
            continue

        plan_dict = plan.model_dump(mode="json")
        preview = scheme.build_preview(plan, round_input)
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        append_jsonl(
            log_path,
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "scheme": scheme.SCHEME_ID,
                "input": round_input,
                "plan": plan_dict,
                "preview": preview,
                "robot_motion": False,
            },
        )
        history = candidate_history
        previous_emotion_state = dict(plan_dict["emotion_state"])
        previous_plan = plan_dict

    print(f"结束，共完成{len(history)}轮。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

