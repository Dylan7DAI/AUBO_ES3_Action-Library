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
SCHEME_ID = "llm_scheme_1_parameterized"
SCHEME_NAME = "方案一：固定轨迹连续参数化"
DEFAULT_PROMPT = ROOT / "config" / "llm_scheme_1_prompt.txt"
TEMPERATURE = 0.25

PARAMETER_LIMITS = {
    "spatial_extent": [0.55, 1.0],
    "speed_scale": [0.70, 1.10],
    "acceleration_scale": [0.65, 1.10],
    "hold_scale": [0.70, 1.30],
    "rhythmic_accent": [0.0, 1.0],
    "repeat_count": [0, 2],
    "repeat_amplitude": [0.50, 0.90],
    "return_speed_scale": [0.70, 1.0],
}


class MotionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spatial_extent: float = Field(ge=0.55, le=1.0)
    speed_scale: float = Field(ge=0.70, le=1.10)
    acceleration_scale: float = Field(ge=0.65, le=1.10)
    hold_scale: float = Field(ge=0.70, le=1.30)
    rhythmic_accent: float = Field(ge=0.0, le=1.0)
    repeat_count: int = Field(ge=0, le=2)
    repeat_amplitude: float = Field(ge=0.50, le=0.90)
    return_speed_scale: float = Field(ge=0.70, le=1.0)


class ParameterizedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: TransitionType
    brief_reason: str = Field(min_length=1, max_length=100)
    motion_profile: MotionProfile


PlanModel = ParameterizedPlan


def build_round_input(base_input: dict[str, Any]) -> dict[str, Any]:
    return {**base_input, "parameter_limits": PARAMETER_LIMITS}


def validate_plan(plan: ParameterizedPlan, round_input: dict[str, Any]) -> None:
    current_result = str(round_input["current_result"])
    history = list(round_input["result_history"])
    validate_common_plan(
        plan,
        current_result=current_result,
        result_history=history,
    )
    profile = plan.motion_profile
    if plan.transition_type == "fluctuation":
        if profile.spatial_extent > 0.75:
            raise ValueError("fluctuation时spatial_extent不得超过0.75")
        if profile.repeat_count > 1 or profile.rhythmic_accent > 0.50:
            raise ValueError("fluctuation时重复和节奏重音必须克制")
    if plan.transition_type == "reversal" and profile.repeat_count > 1:
        raise ValueError("reversal时repeat_count不得超过1")
    if (
        profile.spatial_extent >= 0.98
        and profile.speed_scale >= 1.08
        and profile.acceleration_scale >= 1.08
    ):
        raise ValueError("禁止空间、速度和加速度同时接近最大值")
    if current_result == "incorrect" and plan.emotion_state.emotion_name == "dejection":
        if profile.speed_scale > 0.90:
            raise ValueError("dejection的speed_scale不得超过0.90")


def build_preview(
    plan: ParameterizedPlan,
    round_input: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scheme": SCHEME_ID,
        "result": round_input["current_result"],
        **plan.model_dump(mode="json"),
        "local_motion_family": (
            "positive_fixed_trajectory"
            if round_input["current_result"] == "correct"
            else "negative_fixed_trajectory"
        ),
        "start_pose": "curiosity_down",
        "end_pose": "anticipation_look_down",
        "safety": "LLM参数将在本地再次裁剪并验证关节轨迹",
    }


def compile_motion(
    plan: ParameterizedPlan,
    *,
    round_input: dict[str, Any],
    pose_data: dict[str, Any],
    client: Any,
    expressive_limits: Any,
) -> CompiledMotion:
    del client, expressive_limits
    profile = plan.motion_profile
    start = load_joint_pose(pose_data, "curiosity_down")
    end = load_joint_pose(pose_data, "anticipation_look_down")
    keyframes: list[list[float]] = [start]
    holds: list[float] = [0.0]

    if round_input["current_result"] == "correct":
        lift_max = load_joint_pose(pose_data, "joy_lift_max")
        peak_max = load_joint_pose(pose_data, "joy_shout_peak_max")
        lift = interpolate_vector(start, lift_max, profile.spatial_extent)
        peak_extent = min(
            1.0,
            profile.spatial_extent * profile.repeat_amplitude,
        )
        peak = interpolate_vector(lift, peak_max, peak_extent)
        keyframes.append(lift)
        holds.append(0.08 * profile.hold_scale)
        for _ in range(profile.repeat_count):
            keyframes.extend([peak, lift])
            holds.extend([0.06 + 0.08 * profile.rhythmic_accent, 0.04])
    else:
        turn_max = load_joint_pose(pose_data, "disappointment_turn_away")
        retreat_max = load_joint_pose(pose_data, "disappointment_retreat_max")
        turn = interpolate_vector(start, turn_max, profile.spatial_extent)
        retreat = interpolate_vector(start, retreat_max, profile.spatial_extent)
        keyframes.extend([turn, retreat])
        holds.extend([0.20 * profile.hold_scale, 0.30 * profile.hold_scale])
        breath_in = interpolate_vector(
            retreat,
            turn,
            0.12 * profile.repeat_amplitude,
        )
        for _ in range(profile.repeat_count):
            keyframes.extend([breath_in, retreat])
            holds.extend([0.10 * profile.hold_scale, 0.12 * profile.hold_scale])

    keyframes.append(end)
    holds.append(0.0)
    return CompiledMotion(
        scheme=SCHEME_ID,
        keyframes=keyframes,
        velocity_rad_s=0.24 * profile.speed_scale,
        acceleration_rad_s2=0.45 * profile.acceleration_scale,
        holds_s=holds,
        metadata={
            "motion_profile": profile.model_dump(mode="json"),
            "keyframe_count": len(keyframes),
            "return_speed_scale": profile.return_speed_scale,
        },
        requires_cartesian_validation=False,
    )

