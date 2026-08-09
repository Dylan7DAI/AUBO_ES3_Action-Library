from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aubo_es3_actions.expressive_common import (
    CompiledMotion,
    EmotionState,
    TransitionType,
    load_joint_pose,
    validate_common_plan,
)
from aubo_es3_actions.expressive_safety import interpolate_vector


ROOT = Path(__file__).resolve().parents[1]
SCHEME_ID = "llm_scheme_2_laban"
SCHEME_NAME = "方案二：统一拉班动作参数生成器"
DEFAULT_PROMPT = ROOT / "config" / "llm_scheme_2_prompt.txt"
TEMPERATURE = 0.35

PARAMETER_LIMITS = {
    "vertical_shape": [-1.0, 1.0],
    "radial_shape": [-1.0, 1.0],
    "depth_shape": [-1.0, 0.2],
    "suddenness": [0.0, 1.0],
    "freedom": [0.0, 1.0],
    "directness": [0.0, 1.0],
    "lightness": [0.0, 1.0],
    "curvature": [0.0, 1.0],
    "duration_scale": [0.8, 1.25],
    "hold_ratio": [0.05, 0.25],
    "oscillation_count": [0, 2],
    "oscillation_scale": [0.0, 0.5],
}


class MotionQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertical_shape: float = Field(ge=-1.0, le=1.0)
    radial_shape: float = Field(ge=-1.0, le=1.0)
    depth_shape: float = Field(ge=-1.0, le=0.2)
    suddenness: float = Field(ge=0.0, le=1.0)
    freedom: float = Field(ge=0.0, le=1.0)
    directness: float = Field(ge=0.0, le=1.0)
    lightness: float = Field(ge=0.0, le=1.0)
    curvature: float = Field(ge=0.0, le=1.0)
    duration_scale: float = Field(ge=0.8, le=1.25)
    hold_ratio: float = Field(ge=0.05, le=0.25)
    oscillation_count: int = Field(ge=0, le=2)
    oscillation_scale: float = Field(ge=0.0, le=0.5)


class LabanPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: TransitionType
    brief_reason: str = Field(min_length=1, max_length=100)
    motion_quality: MotionQuality


PlanModel = LabanPlan


def build_round_input(base_input: dict[str, Any]) -> dict[str, Any]:
    return {**base_input, "parameter_limits": PARAMETER_LIMITS}


def validate_plan(plan: LabanPlan, round_input: dict[str, Any]) -> None:
    result = str(round_input["current_result"])
    validate_common_plan(
        plan,
        current_result=result,
        result_history=list(round_input["result_history"]),
    )
    quality = plan.motion_quality
    if result == "correct":
        if quality.vertical_shape < 0.15 or quality.radial_shape < 0.10:
            raise ValueError("猜对时必须具有最低限度的上扬和舒展")
    else:
        if quality.vertical_shape > -0.10 or quality.radial_shape > -0.10:
            raise ValueError("猜错时必须具有最低限度的下沉和收缩")
        if quality.depth_shape > 0.0:
            raise ValueError("猜错时不得向用户方向前进")
    if plan.transition_type == "fluctuation":
        if max(abs(quality.vertical_shape), abs(quality.radial_shape)) > 0.55:
            raise ValueError("fluctuation时Shape绝对值不得超过0.55")
        if quality.oscillation_count > 1:
            raise ValueError("fluctuation时oscillation_count不得超过1")
    if plan.transition_type == "reversal":
        if max(abs(quality.vertical_shape), abs(quality.radial_shape)) > 0.75:
            raise ValueError("reversal时不得使用极端Shape值")


def build_preview(plan: LabanPlan, round_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme": SCHEME_ID,
        "result": round_input["current_result"],
        **plan.model_dump(mode="json"),
        "local_generator": "shared_laban_joint_basis",
        "start_pose": "curiosity_down",
        "end_pose": "anticipation_look_down",
        "safety": "本地将Laban向量映射到已验证关节姿态基并检查轨迹",
    }


def _blend_three(
    anchor: list[float],
    first: list[float],
    second: list[float],
    first_weight: float,
    second_weight: float,
) -> list[float]:
    result = interpolate_vector(anchor, first, first_weight)
    return [
        value + second_weight * (second_value - first_value)
        for value, first_value, second_value in zip(result, first, second)
    ]


def compile_motion(
    plan: LabanPlan,
    *,
    round_input: dict[str, Any],
    pose_data: dict[str, Any],
    client: Any,
    expressive_limits: Any,
) -> CompiledMotion:
    del client, expressive_limits
    quality = plan.motion_quality
    start = load_joint_pose(pose_data, "curiosity_down")
    end = load_joint_pose(pose_data, "anticipation_look_down")
    vertical_amount = min(1.0, max(0.25, abs(quality.vertical_shape)))
    radial_amount = min(0.45, max(0.05, abs(quality.radial_shape) * 0.35))

    if round_input["current_result"] == "correct":
        vertical_basis = load_joint_pose(pose_data, "joy_lift_max")
        radial_basis = load_joint_pose(pose_data, "joy_shout_peak_max")
        preparation = interpolate_vector(start, vertical_basis, vertical_amount * 0.65)
        apex = _blend_three(
            start,
            vertical_basis,
            radial_basis,
            vertical_amount,
            radial_amount,
        )
    else:
        vertical_basis = load_joint_pose(pose_data, "disappointment_retreat_max")
        radial_basis = load_joint_pose(pose_data, "disappointment_turn_away")
        preparation = interpolate_vector(start, radial_basis, radial_amount + 0.25)
        apex = _blend_three(
            start,
            vertical_basis,
            radial_basis,
            vertical_amount,
            radial_amount,
        )

    # Curvature is implemented as an intermediate bend toward a shared side
    # basis, not as an LLM-generated coordinate.
    side_name = "curiosity_left" if quality.depth_shape <= -0.25 else "curiosity_right"
    side = load_joint_pose(pose_data, side_name)
    curved_preparation = interpolate_vector(
        preparation,
        side,
        quality.curvature * (1.0 - quality.directness) * 0.12,
    )
    keyframes = [start, curved_preparation, apex]
    holds = [0.0, 0.0, 0.30 * quality.hold_ratio * quality.duration_scale]
    oscillation = interpolate_vector(
        apex,
        preparation,
        0.12 * quality.oscillation_scale,
    )
    for _ in range(quality.oscillation_count):
        keyframes.extend([oscillation, apex])
        holds.extend([0.04, 0.06])
    keyframes.append(end)
    holds.append(0.0)

    speed = (0.16 + 0.12 * quality.suddenness) / quality.duration_scale
    acceleration = 0.28 + 0.22 * quality.suddenness
    return CompiledMotion(
        scheme=SCHEME_ID,
        keyframes=keyframes,
        velocity_rad_s=speed,
        acceleration_rad_s2=acceleration,
        holds_s=holds,
        metadata={
            "motion_quality": quality.model_dump(mode="json"),
            "basis": [vertical_basis, radial_basis, side],
            "keyframe_count": len(keyframes),
        },
        requires_cartesian_validation=False,
    )

