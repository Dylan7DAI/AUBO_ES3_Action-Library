#!/usr/bin/env python3
"""调用LLM生成跨回合情感判断；本脚本永远不连接或控制机械臂。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = ROOT / "config" / "primitive_choreography_prompt.txt"

POSITIVE_EMOTIONS = {"joy", "excitement", "relief"}
NEGATIVE_EMOTIONS = {"disappointment", "dejection"}

START_POSE = "curiosity_down"
END_POSE = "anticipation_look_down"

MOTION_BY_RESULT = {
    "correct": "joyshout",
    "incorrect": "disappointment",
}

REPEATED_PRIMITIVE_BY_RESULT = {
    "correct": "arm_pulse",
    "incorrect": "breathe",
}

FIXED_PRIMITIVES_BY_RESULT = {
    "correct": ["rise", "return_to_lookdown"],
    "incorrect": [
        "turn_away",
        "contract",
        "return_to_lookdown",
    ],
}

LEVEL_PARAMETERS = {
    0: {"speed_scale": 0.75, "repeat_count": 0},
    1: {"speed_scale": 0.82, "repeat_count": 1},
    2: {"speed_scale": 0.90, "repeat_count": 2},
    3: {"speed_scale": 0.95, "repeat_count": 3},
}

NEUTRAL_EMOTION_STATE = {
    "emotion_name": "neutral",
    "valence": 0.00,
    "arousal": 0.00,
    "intensity": 0.00,
    "persistence": 0.00,
    "unexpectedness": 0.00,
}


class EmotionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_name: str = Field(min_length=1, max_length=64)
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    intensity: float = Field(ge=0.0, le=1.0)
    persistence: float = Field(ge=0.0, le=1.0)
    unexpectedness: float = Field(ge=0.0, le=1.0)


class EmotionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    intensity_level: int = Field(ge=0, le=3)
    brief_reason: str = Field(min_length=1, max_length=100)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "调用LLM生成跨回合情感状态，"
            "并在本地映射为固定动作预览；机械臂不会运动"
        )
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "DEEPSEEK_MODEL",
            "deepseek-v4-flash",
        ),
        help=(
            "DeepSeek模型名；默认读取DEEPSEEK_MODEL，"
            "否则使用deepseek-v4-flash"
        ),
    )
    parser.add_argument(
        "--prompt-file",
        default=str(DEFAULT_PROMPT),
        help="情感判断系统提示词文件",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="0表示不限回合数",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="LLM输出无效时的重试次数；默认2次",
    )
    return parser


def normalize_result(text: str) -> Optional[str]:
    value = text.strip().lower()
    if value == "1":
        return "correct"
    if value == "0":
        return "incorrect"
    return None


def expected_transition_type(result_history: List[str]) -> str:
    if not result_history:
        raise ValueError("result_history不能为空")

    if any(
        item not in {"correct", "incorrect"}
        for item in result_history
    ):
        raise ValueError(
            "result_history只能包含correct或incorrect"
        )

    if len(result_history) >= 3:
        recent_three = result_history[-3:]
        if (
            recent_three[0] != recent_three[1]
            and recent_three[1] != recent_three[2]
        ):
            return "fluctuation"

    if len(result_history) == 1:
        return "continuation"

    if result_history[-1] != result_history[-2]:
        return "reversal"

    return "accumulation"


def intensity_level_for_value(intensity: float) -> int:
    if intensity < 0.25:
        return 0
    if intensity < 0.50:
        return 1
    if intensity < 0.75:
        return 2
    return 3


def has_at_most_two_decimal_places(value: float) -> bool:
    return Decimal(str(value)).as_tuple().exponent >= -2


def validate_decision_for_result(
    decision: EmotionDecision,
    current_result: str,
    result_history: List[str],
    previous_decision: Optional[Dict[str, Any]],
) -> None:
    if current_result not in {"correct", "incorrect"}:
        raise ValueError(
            "current_result只能是correct或incorrect"
        )

    if (
        not result_history
        or result_history[-1] != current_result
    ):
        raise ValueError(
            "result_history最后一项必须与current_result一致"
        )

    transition_type = expected_transition_type(result_history)
    emotion = decision.emotion_state

    allowed_emotions = (
        POSITIVE_EMOTIONS
        if current_result == "correct"
        else NEGATIVE_EMOTIONS
    )

    if emotion.emotion_name not in allowed_emotions:
        raise ValueError(
            f"本轮{current_result}不能使用"
            f"emotion_name={emotion.emotion_name}"
        )

    if current_result == "correct" and emotion.valence <= 0:
        raise ValueError("猜对时valence必须大于0")

    if current_result == "incorrect" and emotion.valence >= 0:
        raise ValueError("猜错时valence必须小于0")

    expected_level = intensity_level_for_value(
        emotion.intensity
    )
    if decision.intensity_level != expected_level:
        raise ValueError(
            "intensity_level与intensity不一致："
            f"intensity={emotion.intensity}时应为"
            f"{expected_level}级，实际为"
            f"{decision.intensity_level}级"
        )

    if (
        transition_type == "fluctuation"
        and decision.intensity_level > 1
    ):
        raise ValueError(
            "fluctuation情境的intensity_level不得超过1"
        )

    if (
        transition_type == "reversal"
        and decision.intensity_level > 2
    ):
        raise ValueError(
            "reversal情境的intensity_level不得超过2"
        )

    if (
        decision.intensity_level == 3
        and transition_type != "accumulation"
    ):
        raise ValueError(
            "intensity_level为3时必须具有accumulation依据"
        )

    if emotion.emotion_name == "excitement":
        if transition_type != "accumulation":
            raise ValueError(
                "excitement只能用于连续猜对的累积情境"
            )

    if emotion.emotion_name == "dejection":
        if transition_type != "accumulation":
            raise ValueError(
                "dejection只能用于连续猜错的累积情境"
            )

    if emotion.emotion_name == "relief":
        if (
            len(result_history) < 2
            or result_history[-2] != "incorrect"
        ):
            raise ValueError(
                "relief只能用于由猜错转为猜对的情境"
            )

    if (
        transition_type == "accumulation"
        and previous_decision is not None
    ):
        previous_level = previous_decision.get(
            "intensity_level"
        )
        if (
            isinstance(previous_level, int)
            and decision.intensity_level < previous_level
        ):
            raise ValueError(
                "同方向结果继续累积时，"
                "intensity_level不得低于上一轮"
            )

    if (
        "\n" in decision.brief_reason
        or "\r" in decision.brief_reason
    ):
        raise ValueError("brief_reason必须是单行短句")

    if not any(
        "\u4e00" <= char <= "\u9fff"
        for char in decision.brief_reason
    ):
        raise ValueError(
            "brief_reason必须使用简短中文"
        )

    decimal_values = {
        "valence": emotion.valence,
        "arousal": emotion.arousal,
        "intensity": emotion.intensity,
        "persistence": emotion.persistence,
        "unexpectedness": emotion.unexpectedness,
    }

    excessive_precision = [
        name
        for name, value in decimal_values.items()
        if not has_at_most_two_decimal_places(value)
    ]

    if excessive_precision:
        raise ValueError(
            "以下数值超过两位小数："
            + ", ".join(excessive_precision)
        )


def build_motion_preview(
    decision: EmotionDecision,
    current_result: str,
    result_history: List[str],
) -> Dict[str, Any]:
    level = decision.intensity_level
    parameters = LEVEL_PARAMETERS[level]

    emotion_state = {
        key: (
            round(value, 2)
            if isinstance(value, float)
            else value
        )
        for key, value in (
            decision.emotion_state.model_dump().items()
        )
    }

    return {
        "result": current_result,
        "transition_type": expected_transition_type(
            result_history
        ),
        "emotion_state": emotion_state,
        "intensity_level": level,
        "brief_reason": decision.brief_reason,
        "motion": MOTION_BY_RESULT[current_result],
        "start_pose": START_POSE,
        "end_pose": END_POSE,
        "speed_scale": parameters["speed_scale"],
        "fixed_primitives": (
            FIXED_PRIMITIVES_BY_RESULT[current_result]
        ),
        "repeated_primitive": (
            REPEATED_PRIMITIVE_BY_RESULT[current_result]
        ),
        "repeat_count": parameters["repeat_count"],
    }


def call_openai(
    model: str,
    system_prompt: str,
    round_input: Dict[str, Any],
    max_retries: int,
) -> EmotionDecision:
    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少openai库。请先运行："
            "python3 -m pip install --user "
            "-r requirements.txt"
        ) from exc

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未设置DEEPSEEK_API_KEY。"
            "请先在终端安全设置密钥。"
        )

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    json_prompt = (
        system_prompt
        + "\n\n你必须只输出一个JSON对象，"
        "不要输出Markdown、代码围栏或解释文字。"
        + "\nJSON必须符合下面的Schema：\n"
        + json.dumps(
            EmotionDecision.model_json_schema(),
            ensure_ascii=False,
        )
    )

    messages = [
        {"role": "system", "content": json_prompt},
        {
            "role": "user",
            "content": json.dumps(
                round_input,
                ensure_ascii=False,
            ),
        },
    ]

    last_error = "未知错误"

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=1000,
                stream=False,
            )
        except Exception as exc:
            last_error = f"API调用失败：{exc}"
            continue

        content = response.choices[0].message.content
        if not content or not content.strip():
            last_error = "DeepSeek返回了空内容"
            continue

        try:
            decision = (
                EmotionDecision.model_validate_json(content)
            )
            validate_decision_for_result(
                decision=decision,
                current_result=str(
                    round_input["current_result"]
                ),
                result_history=list(
                    round_input["result_history"]
                ),
                previous_decision=round_input.get(
                    "previous_decision"
                ),
            )
            return decision
        except Exception as exc:
            last_error = str(exc)

        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": content,
                },
                {
                    "role": "user",
                    "content": (
                        "上一个JSON未通过本地校验："
                        f"{last_error}。"
                        "请重新输出符合Schema和历史情境的"
                        "合法JSON，不要添加解释或额外字段。"
                    ),
                },
            ]
        )

    raise RuntimeError(
        f"连续{max_retries + 1}次"
        f"未获得有效情感输出：{last_error}"
    )


def format_preview_json(
    preview: Dict[str, Any],
) -> str:
    return json.dumps(
        preview,
        ensure_ascii=False,
        indent=2,
    )


def append_log(
    path: Path,
    record: Dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                record,
                ensure_ascii=False,
            )
            + "\n"
        )


def main() -> int:
    args = build_parser().parse_args()

    if args.max_rounds < 0:
        print(
            "max-rounds不能小于0。",
            file=sys.stderr,
        )
        return 2

    if args.max_retries < 0:
        print(
            "max-retries不能小于0。",
            file=sys.stderr,
        )
        return 2

    try:
        system_prompt = Path(
            args.prompt_file
        ).read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"读取提示词失败：{exc}",
            file=sys.stderr,
        )
        return 2

    print(
        "模式：LLM情感评价与固定动作预览"
        "（本脚本不会连接机械臂）"
    )
    print(f"模型：{args.model}")
    print("每轮输入：1=猜对，0=猜错，e=结束")

    history: List[str] = []
    previous_emotion_state: Dict[str, Any] = dict(
        NEUTRAL_EMOTION_STATE
    )
    previous_decision: Optional[Dict[str, Any]] = None

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    log_path = (
        ROOT
        / "logs"
        / f"llm_primitive_preview_{timestamp}.jsonl"
    )

    while True:
        if (
            args.max_rounds
            and len(history) >= args.max_rounds
        ):
            print(f"已达到{args.max_rounds}轮。")
            break

        text = input(
            f"\n第{len(history) + 1}轮结果 [1/0/e]："
        ).strip()

        if text.lower() == "e":
            break

        current_result = normalize_result(text)
        if current_result is None:
            print("输入无效，请输入1、0或e。")
            continue

        candidate_history = history + [current_result]

        round_input = {
            "current_result": current_result,
            "result_history": candidate_history,
            "transition_type": expected_transition_type(
                candidate_history
            ),
            "previous_emotion_state": (
                previous_emotion_state
            ),
            "previous_decision": previous_decision,
        }

        print(
            "正在调用LLM生成情感状态……",
            flush=True,
        )

        try:
            decision = call_openai(
                model=args.model,
                system_prompt=system_prompt,
                round_input=round_input,
                max_retries=args.max_retries,
            )
        except Exception as exc:
            print(
                f"本轮调用失败：{exc}",
                file=sys.stderr,
            )
            print(
                "本轮不会写入历史，"
                "可以检查后重新输入。"
            )
            continue

        preview = build_motion_preview(
            decision=decision,
            current_result=current_result,
            result_history=candidate_history,
        )

        print(format_preview_json(preview))

        decision_dict = decision.model_dump(mode="json")

        append_log(
            log_path,
            {
                "timestamp_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
                "input": round_input,
                "decision": decision_dict,
                "preview": preview,
                "robot_motion": False,
            },
        )

        history = candidate_history
        previous_emotion_state = dict(
            decision_dict["emotion_state"]
        )
        previous_decision = decision_dict

    print(f"\n结束，共完成{len(history)}轮。")
    if history:
        print(f"记录文件：{log_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
