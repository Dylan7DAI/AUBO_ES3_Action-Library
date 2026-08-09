from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aubo_es3_actions.expressive_common import (
    CompiledMotion,
    EmotionState,
    TransitionType,
    load_joint_pose,
    validate_common_plan,
)
from aubo_es3_actions.expressive_safety import (
    validate_sequential_ik,
    validate_tcp_pose,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEME_ID = "llm_scheme_3_residual"
SCHEME_NAME = "方案三：参考动作加受限笛卡尔残差"
DEFAULT_PROMPT = ROOT / "config" / "llm_scheme_3_prompt.txt"
TEMPERATURE = 0.40

RESIDUAL_LIMITS = {
    "translation_m": {
        "up_down": 0.025,
        "open_close": 0.025,
        "lateral": 0.015,
        "away_from_user": 0.020,
    },
    "orientation_rad": 0.12,
    "path_curvature_m": 0.010,
    "normalized_parameter_range": [-1.0, 1.0],
}


class ResidualModifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    up_down: float = Field(ge=-1.0, le=1.0)
    open_close: float = Field(ge=-1.0, le=1.0)
    lateral: float = Field(ge=-1.0, le=1.0)
    yaw: float = Field(ge=-1.0, le=1.0)
    pitch: float = Field(ge=-1.0, le=1.0)
    roll: float = Field(ge=-1.0, le=1.0)
    away_from_user: float = Field(ge=0.0, le=1.0)


class ResidualPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: TransitionType
    brief_reason: str = Field(min_length=1, max_length=100)
    reference_family: Literal["positive_reference", "negative_reference"]
    onset_modifier: ResidualModifier
    apex_modifier: ResidualModifier
    path_curvature: float = Field(ge=0.0, le=1.0)
    duration_scale: float = Field(ge=0.80, le=1.20)
    apex_hold_ratio: float = Field(ge=0.05, le=0.22)
    onset_accent: float = Field(ge=0.0, le=1.0)
    local_pulse_count: int = Field(ge=0, le=2)
    pulse_scale: float = Field(ge=0.0, le=0.45)


PlanModel = ResidualPlan


def build_round_input(base_input: dict[str, Any]) -> dict[str, Any]:
    return {**base_input, "allowed_normalized_limits": RESIDUAL_LIMITS}


def _max_abs(modifier: ResidualModifier) -> float:
    values = modifier.model_dump().values()
    return max(abs(float(value)) for value in values)


def validate_plan(plan: ResidualPlan, round_input: dict[str, Any]) -> None:
    result = str(round_input["current_result"])
    validate_common_plan(
        plan,
        current_result=result,
        result_history=list(round_input["result_history"]),
    )
    expected_family = (
        "positive_reference" if result == "correct" else "negative_reference"
    )
    if plan.reference_family != expected_family:
        raise ValueError("reference_family与本轮结果方向不一致")
    if result == "correct":
        if plan.apex_modifier.up_down < 0.10 or plan.apex_modifier.open_close < 0.05:
            raise ValueError("猜对时顶点残差必须至少轻微上扬和展开")
    else:
        if plan.apex_modifier.up_down > -0.05 or plan.apex_modifier.open_close > 0.0:
            raise ValueError("猜错时顶点残差不得上扬或展开")
    if plan.transition_type == "fluctuation":
        if max(_max_abs(plan.onset_modifier), _max_abs(plan.apex_modifier)) > 0.45:
            raise ValueError("fluctuation时残差绝对值不得超过0.45")
        if plan.local_pulse_count > 1:
            raise ValueError("fluctuation时脉冲次数不得超过1")
    if plan.transition_type == "reversal":
        if max(_max_abs(plan.onset_modifier), _max_abs(plan.apex_modifier)) > 0.65:
            raise ValueError("reversal时残差绝对值不得超过0.65")
        if plan.local_pulse_count > 1:
            raise ValueError("reversal时脉冲次数不得超过1")
    if plan.local_pulse_count == 0 and plan.pulse_scale != 0.0:
        raise ValueError("没有局部脉冲时pulse_scale必须为0.00")


def build_preview(plan: ResidualPlan, round_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme": SCHEME_ID,
        "result": round_input["current_result"],
        **plan.model_dump(mode="json"),
        "local_generator": "reference_tcp_plus_bounded_residual_then_sequential_ik",
        "safety": (
            "残差先限幅，再做TCP工作空间、连续种子IK、IK分支跳变与关节轨迹检查；"
            "无经验证碰撞模型时禁止实机"
        ),
    }


def _normalized(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1e-9:
        return [0.0, 0.0, 0.0]
    return [value / length for value in vector]


def _apply_modifier(
    base_pose: list[float],
    modifier: ResidualModifier,
    *,
    expressive_axis: list[float],
    retreat_axis: list[float],
    limits: Any,
) -> list[float]:
    pose = list(base_pose)
    pose[2] += 0.025 * modifier.up_down
    for axis in range(3):
        pose[axis] += 0.025 * modifier.open_close * expressive_axis[axis]
        pose[axis] += 0.020 * modifier.away_from_user * retreat_axis[axis]
    pose[1] += 0.015 * modifier.lateral
    pose[3] += 0.12 * modifier.roll
    pose[4] += 0.12 * modifier.pitch
    pose[5] += 0.12 * modifier.yaw
    return validate_tcp_pose(pose, limits)


def _pose_segment(
    start: list[float],
    target: list[float],
    *,
    curvature: float,
    steps: int = 10,
) -> list[list[float]]:
    points: list[list[float]] = []
    for index in range(1, steps + 1):
        progress = index / steps
        point = [
            a + progress * (b - a)
            for a, b in zip(start, target)
        ]
        point[1] += 0.010 * curvature * math.sin(math.pi * progress)
        points.append(point)
    return points


def compile_motion(
    plan: ResidualPlan,
    *,
    round_input: dict[str, Any],
    pose_data: dict[str, Any],
    client: Any,
    expressive_limits: Any,
) -> CompiledMotion:
    result = str(round_input["current_result"])
    start_joints = load_joint_pose(pose_data, "curiosity_down")
    end_joints = load_joint_pose(pose_data, "anticipation_look_down")
    retreat_joints = load_joint_pose(pose_data, "disappointment_retreat_max")
    if result == "correct":
        onset_joints = load_joint_pose(pose_data, "joy_lift_max")
        apex_joints = load_joint_pose(pose_data, "joy_shout_peak_max")
    else:
        onset_joints = load_joint_pose(pose_data, "disappointment_turn_away")
        apex_joints = retreat_joints

    start_pose = client.forward_kinematics(start_joints)
    onset_reference = client.forward_kinematics(onset_joints)
    apex_reference = client.forward_kinematics(apex_joints)
    retreat_pose = client.forward_kinematics(retreat_joints)
    expressive_axis = _normalized([
        apex_reference[index] - onset_reference[index]
        for index in range(3)
    ])
    retreat_axis = _normalized([
        retreat_pose[index] - start_pose[index]
        for index in range(3)
    ])
    onset_pose = _apply_modifier(
        onset_reference,
        plan.onset_modifier,
        expressive_axis=expressive_axis,
        retreat_axis=retreat_axis,
        limits=expressive_limits,
    )
    apex_pose = _apply_modifier(
        apex_reference,
        plan.apex_modifier,
        expressive_axis=expressive_axis,
        retreat_axis=retreat_axis,
        limits=expressive_limits,
    )

    tcp_path = _pose_segment(
        start_pose,
        onset_pose,
        curvature=plan.path_curvature * 0.55,
    ) + _pose_segment(
        onset_pose,
        apex_pose,
        curvature=plan.path_curvature,
    )
    seed = list(start_joints)
    joint_path = [seed]
    for tcp_pose in tcp_path:
        validate_tcp_pose(tcp_pose, expressive_limits)
        seed = client.inverse_kinematics(tcp_pose, seed_joints=seed)
        joint_path.append(seed)
    validate_sequential_ik(joint_path, expressive_limits)

    keyframes = list(joint_path)
    holds = [0.0] * len(keyframes)
    holds[-1] = 0.40 * plan.apex_hold_ratio * plan.duration_scale
    pulse_target = joint_path[-3]
    for _ in range(plan.local_pulse_count):
        fraction = 0.08 * plan.pulse_scale
        pulse = [
            apex + fraction * (previous - apex)
            for apex, previous in zip(joint_path[-1], pulse_target)
        ]
        keyframes.extend([pulse, joint_path[-1]])
        holds.extend([0.03, 0.05])
    keyframes.append(end_joints)
    holds.append(0.0)

    speed = (0.15 + 0.10 * plan.onset_accent) / plan.duration_scale
    acceleration = 0.25 + 0.20 * plan.onset_accent
    return CompiledMotion(
        scheme=SCHEME_ID,
        keyframes=keyframes,
        velocity_rad_s=speed,
        acceleration_rad_s2=acceleration,
        holds_s=holds,
        metadata={
            "reference_family": plan.reference_family,
            "tcp_sample_count": len(tcp_path),
            "uses_sequential_seed_ik": True,
            "execution_note": "密集IK点为保守原型；接入已验证碰撞模型后再评估样条化",
        },
        requires_cartesian_validation=True,
    )
