from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional, Sequence, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .sdk_client import JOINT_NAMES


POSITIVE_EMOTIONS = {"joy", "excitement", "relief"}
NEGATIVE_EMOTIONS = {"disappointment", "dejection"}

NEUTRAL_EMOTION_STATE = {
    "emotion_name": "neutral",
    "valence": 0.0,
    "arousal": 0.0,
    "intensity": 0.0,
    "persistence": 0.0,
    "unexpectedness": 0.0,
}


class EmotionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_name: str = Field(min_length=1, max_length=64)
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    intensity: float = Field(ge=0.0, le=1.0)
    persistence: float = Field(ge=0.0, le=1.0)
    unexpectedness: float = Field(ge=0.0, le=1.0)


TransitionType = Literal[
    "continuation",
    "accumulation",
    "reversal",
    "fluctuation",
]


def normalize_result(text: str) -> Optional[str]:
    value = text.strip().lower()
    if value == "1":
        return "correct"
    if value == "0":
        return "incorrect"
    return None


def expected_transition_type(result_history: Sequence[str]) -> str:
    history = list(result_history)
    if not history or any(value not in {"correct", "incorrect"} for value in history):
        raise ValueError("result_history必须是非空correct/incorrect列表")
    if len(history) >= 3:
        recent = history[-3:]
        if recent[0] != recent[1] and recent[1] != recent[2]:
            return "fluctuation"
    if len(history) == 1:
        return "continuation"
    if history[-1] != history[-2]:
        return "reversal"
    return "accumulation"


def has_at_most_two_decimals(value: float) -> bool:
    return Decimal(str(value)).as_tuple().exponent >= -2


def validate_common_plan(
    plan: BaseModel,
    *,
    current_result: str,
    result_history: Sequence[str],
) -> None:
    if not result_history or result_history[-1] != current_result:
        raise ValueError("result_history最后一项必须与current_result一致")
    transition = getattr(plan, "transition_type")
    expected = expected_transition_type(result_history)
    if transition != expected:
        raise ValueError(
            f"transition_type应为{expected}，实际为{transition}"
        )
    emotion: EmotionState = getattr(plan, "emotion_state")
    allowed = POSITIVE_EMOTIONS if current_result == "correct" else NEGATIVE_EMOTIONS
    if emotion.emotion_name not in allowed:
        raise ValueError(
            f"emotion_name={emotion.emotion_name}与{current_result}不一致"
        )
    if current_result == "correct" and emotion.valence <= 0:
        raise ValueError("猜对时valence必须大于0")
    if current_result == "incorrect" and emotion.valence >= 0:
        raise ValueError("猜错时valence必须小于0")
    if emotion.emotion_name in {"excitement", "dejection"} and transition != "accumulation":
        raise ValueError(f"{emotion.emotion_name}只能用于accumulation")
    if emotion.emotion_name == "relief":
        if len(result_history) < 2 or result_history[-2] != "incorrect":
            raise ValueError("relief只能用于猜错后转为猜对")
    reason = str(getattr(plan, "brief_reason"))
    if "\n" in reason or "\r" in reason:
        raise ValueError("brief_reason必须是单行")
    if not any("\u4e00" <= char <= "\u9fff" for char in reason):
        raise ValueError("brief_reason必须包含中文")

    for name, value in iter_float_fields(plan):
        if not math.isfinite(value):
            raise ValueError(f"{name}必须是有限数值")
        if not has_at_most_two_decimals(value):
            raise ValueError(f"{name}超过两位小数")


def iter_float_fields(value: Any, prefix: str = ""):
    if isinstance(value, BaseModel):
        yield from iter_float_fields(value.model_dump(), prefix)
    elif isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_float_fields(item, name)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_float_fields(item, f"{prefix}[{index}]")
    elif isinstance(value, float):
        yield prefix, value


PlanT = TypeVar("PlanT", bound=BaseModel)


@dataclass
class CompiledMotion:
    scheme: str
    keyframes: list[list[float]]
    velocity_rad_s: float
    acceleration_rad_s2: float
    holds_s: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    requires_cartesian_validation: bool = False

    def __post_init__(self) -> None:
        if not self.holds_s:
            self.holds_s = [0.0] * len(self.keyframes)
        if len(self.holds_s) != len(self.keyframes):
            raise ValueError("holds_s长度必须与keyframes一致")


def call_structured_llm(
    *,
    plan_type: type[PlanT],
    model: str,
    system_prompt: str,
    round_input: dict[str, Any],
    max_retries: int,
    validator: Any,
    temperature: float = 0.2,
) -> PlanT:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少openai库，请安装requirements.txt") from exc

    import os

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置DEEPSEEK_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = (
        system_prompt
        + "\n\n只输出一个JSON对象。JSON必须符合以下Schema：\n"
        + json.dumps(plan_type.model_json_schema(), ensure_ascii=False)
    )
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(round_input, ensure_ascii=False),
        },
    ]
    last_error = "未知错误"
    for _attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=float(temperature),
                max_tokens=4000,
                stream=False,
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("LLM返回空内容")
            plan = plan_type.model_validate_json(content)
            validator(plan, round_input)
            return plan
        except Exception as exc:
            last_error = str(exc)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"上一个输出未通过本地校验：{last_error}。"
                        "请只重新输出合法JSON，不要解释。"
                    ),
                }
            )
    raise RuntimeError(
        f"连续{max_retries + 1}次未获得有效计划：{last_error}"
    )


def load_joint_pose(pose_data: dict[str, Any], name: str) -> list[float]:
    try:
        values = pose_data["poses"][name]["joint_positions_rad"]
        return [float(values[joint]) for joint in JOINT_NAMES]
    except KeyError as exc:
        raise KeyError(f"姿态{name}缺少关节数据：{exc}") from exc


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
