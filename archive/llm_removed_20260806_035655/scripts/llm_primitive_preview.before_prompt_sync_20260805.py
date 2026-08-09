#!/usr/bin/env python3
"""调用LLM生成动作原语编排；本脚本永远不连接或控制机械臂。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "config" / "primitive_choreography_prompt.txt"

POSITIVE_PRIMITIVES = {"rise", "arm_pulse"}
NEGATIVE_PRIMITIVES = {"turn_away", "contract", "breathe"}


class EmotionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_name: str = Field(min_length=1, max_length=64)
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    intensity: float = Field(ge=0.0, le=1.0)
    persistence: float = Field(ge=0.0, le=1.0)
    unexpectedness: float = Field(ge=0.0, le=1.0)


class PrimitiveStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primitive: Literal[
        "rise",
        "arm_pulse",
        "turn_away",
        "contract",
        "breathe",
    ]
    amplitude_scale: float = Field(ge=0.50, le=1.00)
    speed_scale: float = Field(ge=0.60, le=1.20)
    repeat: int = Field(ge=1, le=3)
    hold_after_seconds: float = Field(ge=0.0, le=0.8)
    transition_to_next: Literal["continuous", "soft", "accented"]


class ChoreographyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: Literal[
        "continuation",
        "accumulation",
        "reversal",
        "fluctuation",
    ]
    brief_reason: str = Field(min_length=1, max_length=300)
    global_tempo: float = Field(ge=0.70, le=1.30)
    steps: list[PrimitiveStep] = Field(min_length=1, max_length=6)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只调用LLM生成五个动作原语的编排JSON；机械臂不会运动"
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        help="OpenAI模型名；默认读取OPENAI_MODEL，否则使用gpt-5-mini",
    )
    parser.add_argument(
        "--prompt-file",
        default=str(DEFAULT_PROMPT),
        help="动作编排系统提示词文件",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="0表示不限回合数",
    )
    return parser


def normalize_result(text: str) -> str | None:
    value = text.strip().lower()
    if value == "t":
        return "correct"
    if value == "f":
        return "wrong"
    return None


def validate_plan_for_result(
    plan: ChoreographyPlan,
    current_result: str,
) -> None:
    allowed = (
        POSITIVE_PRIMITIVES
        if current_result == "correct"
        else NEGATIVE_PRIMITIVES
    )
    used = {step.primitive for step in plan.steps}
    invalid = sorted(used - allowed)
    if invalid:
        raise ValueError(
            f"本轮{current_result}出现跨效价原语：{', '.join(invalid)}"
        )

    valence = plan.emotion_state.valence
    if current_result == "correct" and valence < 0:
        raise ValueError("猜对时valence不能为负数")
    if current_result == "wrong" and valence > 0:
        raise ValueError("猜错时valence不能为正数")


def call_openai(
    model: str,
    system_prompt: str,
    round_input: dict[str, Any],
) -> ChoreographyPlan:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少openai库。请先运行：python3 -m pip install --user -r requirements.txt"
        ) from exc

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "未设置OPENAI_API_KEY。请先在终端设置密钥，密钥不要写进代码。"
        )

    client = OpenAI()
    validation_feedback = ""

    for attempt in range(1, 3):
        request_data = dict(round_input)
        if validation_feedback:
            request_data["validation_feedback"] = validation_feedback

        response = client.responses.parse(
            model=model,
            instructions=system_prompt,
            input=json.dumps(request_data, ensure_ascii=False),
            text_format=ChoreographyPlan,
        )
        plan = response.output_parsed
        if plan is None:
            raise RuntimeError("LLM没有返回可解析的动作编排。")

        try:
            validate_plan_for_result(plan, str(round_input["current_result"]))
            return plan
        except ValueError as exc:
            validation_feedback = (
                "上一次输出未通过本地校验，请修正并重新输出完整结果："
                + str(exc)
            )
            if attempt == 2:
                raise RuntimeError(
                    f"LLM连续两次输出未通过校验：{exc}"
                ) from exc

    raise RuntimeError("LLM调用未返回可用编排。")


def append_log(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    args = build_parser().parse_args()
    if args.max_rounds < 0:
        print("max-rounds不能小于0。", file=sys.stderr)
        return 2

    try:
        system_prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"读取提示词失败：{exc}", file=sys.stderr)
        return 2

    print("模式：LLM动作原语编排预览（本脚本不会连接机械臂）")
    print(f"模型：{args.model}")
    print("每轮输入：t=猜对，f=猜错，e=结束")

    history: list[str] = []
    previous_plan: dict[str, Any] | None = None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "logs" / f"llm_primitive_preview_{timestamp}.jsonl"

    while True:
        if args.max_rounds and len(history) >= args.max_rounds:
            print(f"已达到{args.max_rounds}轮。")
            break

        text = input(f"\n第{len(history) + 1}轮结果 [t/f/e]：").strip()
        if text.lower() == "e":
            break

        current_result = normalize_result(text)
        if current_result is None:
            print("输入无效，请输入t、f或e。")
            continue

        candidate_history = history + [current_result]
        round_input = {
            "round": len(candidate_history),
            "current_result": current_result,
            "result_history": candidate_history,
            "previous_plan": previous_plan,
        }

        print("正在调用LLM生成动作编排……", flush=True)
        try:
            plan = call_openai(args.model, system_prompt, round_input)
        except Exception as exc:
            print(f"本轮调用失败：{exc}", file=sys.stderr)
            print("本轮不会写入历史，可以检查后重新输入。")
            continue

        plan_dict = plan.model_dump(mode="json")
        print(json.dumps(plan_dict, ensure_ascii=False, indent=2))

        append_log(
            log_path,
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "input": round_input,
                "choreography": plan_dict,
                "robot_motion": False,
            },
        )
        history = candidate_history
        previous_plan = plan_dict

    print(f"\n结束，共完成{len(history)}轮。")
    if history:
        print(f"记录文件：{log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
