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
SCHEME_ID = "llm_scheme_4_keyframe_ik"
SCHEME_NAME = "方案四：归一化末端关键帧与连续IK"
DEFAULT_PROMPT = ROOT / "config" / "llm_scheme_4_prompt.txt"
TEMPERATURE = 0.35

OrientationToken = Literal[
    "keep_start",
    "look_up_soft",
    "look_down_soft",
    "turn_left_soft",
    "turn_right_soft",
    "open_wrist_soft",
]
PathType = Literal[
    "direct_soft",
    "vertical_arc",
    "lateral_arc",
    "release_arc",
]

NORMALIZED_LIMITS = {
    "vertical": [-1.0, 1.0],
    "lateral": [-0.6, 0.6],
    "away_from_user": [0.0, 1.0],
    "translation_caps_m": {"vertical": 0.045, "lateral": 0.030, "away": 0.040},
    "orientation_token_cap_rad": 0.12,
    "arc_cap_m": 0.010,
}


class NormalizedPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertical: float = Field(ge=-1.0, le=1.0)
    lateral: float = Field(ge=-0.6, le=0.6)
    away_from_user: float = Field(ge=0.0, le=1.0)


class KeyframeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Literal["preparation", "apex"]
    position_norm: NormalizedPosition
    orientation_token: OrientationToken
    path_type: PathType
    duration_weight: float = Field(ge=0.60, le=1.40)
    hold_ratio: float = Field(ge=0.0, le=0.20)
    speed_emphasis: float = Field(ge=0.0, le=1.0)


class KeyframeIkPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: TransitionType
    brief_reason: str = Field(min_length=1, max_length=100)
    keyframes: list[KeyframeSpec] = Field(min_length=2, max_length=2)


PlanModel = KeyframeIkPlan


def build_round_input(base_input: dict[str, Any]) -> dict[str, Any]:
    return {
        **base_input,
        "normalized_keyframe_limits": NORMALIZED_LIMITS,
        "required_phases": ["preparation", "apex"],
    }


def validate_plan(plan: KeyframeIkPlan, round_input: dict[str, Any]) -> None:
    result = str(round_input["current_result"])
    validate_common_plan(
        plan,
        current_result=result,
        result_history=list(round_input["result_history"]),
    )
    if [frame.phase for frame in plan.keyframes] != ["preparation", "apex"]:
        raise ValueError("keyframes必须依次为preparation和apex")
    apex = plan.keyframes[1]
    if result == "correct" and apex.position_norm.vertical < 0.15:
        raise ValueError("猜对时apex必须具有最低限度的上扬")
    if result == "incorrect" and apex.position_norm.vertical > -0.10:
        raise ValueError("猜错时apex必须具有最低限度的下沉")
    if result == "incorrect" and apex.speed_emphasis > 0.65:
        raise ValueError("猜错时不得使用高速度重音")
    if plan.transition_type == "fluctuation":
        for frame in plan.keyframes:
            if abs(frame.position_norm.vertical) > 0.50:
                raise ValueError("fluctuation时垂直范围不得超过0.50")
            if frame.speed_emphasis > 0.55:
                raise ValueError("fluctuation时速度重音不得超过0.55")
    if plan.transition_type == "reversal":
        for frame in plan.keyframes:
            if abs(frame.position_norm.vertical) > 0.70:
                raise ValueError("reversal时不得使用极端垂直关键帧")
            if frame.position_norm.away_from_user > 0.70:
                raise ValueError("reversal时不得使用极端回撤")


def build_preview(plan: KeyframeIkPlan, round_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme": SCHEME_ID,
        "result": round_input["current_result"],
        **plan.model_dump(mode="json"),
        "local_generator": "normalized_tcp_keyframes_to_dense_seeded_ik",
        "safety": (
            "归一化关键帧经本地尺度、方向标定、TCP边界和连续IK检查；"
            "无经验证碰撞模型时禁止实机"
        ),
    }


def _normalized(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length < 1e-9:
        raise ValueError("无法从示教姿态建立语义方向轴")
    return [value / length for value in vector]


def _cross(first: list[float], second: list[float]) -> list[float]:
    return [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]


def _orientation_offset(token: OrientationToken) -> tuple[float, float, float]:
    offsets = {
        "keep_start": (0.0, 0.0, 0.0),
        "look_up_soft": (0.0, -0.12, 0.0),
        "look_down_soft": (0.0, 0.12, 0.0),
        "turn_left_soft": (0.0, 0.0, 0.12),
        "turn_right_soft": (0.0, 0.0, -0.12),
        "open_wrist_soft": (0.12, 0.0, 0.0),
    }
    return offsets[token]


def _frame_pose(
    start_pose: list[float],
    frame: KeyframeSpec,
    *,
    away_axis: list[float],
    lateral_axis: list[float],
    limits: Any,
) -> list[float]:
    pose = list(start_pose)
    position = frame.position_norm
    pose[2] += 0.045 * position.vertical
    for axis in range(3):
        pose[axis] += 0.030 * position.lateral * lateral_axis[axis]
        pose[axis] += 0.040 * position.away_from_user * away_axis[axis]
    roll, pitch, yaw = _orientation_offset(frame.orientation_token)
    pose[3] += roll
    pose[4] += pitch
    pose[5] += yaw
    return validate_tcp_pose(pose, limits)


def _dense_segment(
    start: list[float],
    target: list[float],
    *,
    frame: KeyframeSpec,
    lateral_axis: list[float],
    steps: int = 12,
) -> list[list[float]]:
    points: list[list[float]] = []
    for index in range(1, steps + 1):
        progress = index / steps
        point = [a + progress * (b - a) for a, b in zip(start, target)]
        wave = 0.010 * math.sin(math.pi * progress)
        if frame.path_type == "vertical_arc":
            point[2] += wave
        elif frame.path_type == "lateral_arc":
            for axis in range(3):
                point[axis] += wave * lateral_axis[axis]
        elif frame.path_type == "release_arc":
            point[2] += wave * (1.0 - progress)
            for axis in range(3):
                point[axis] += 0.5 * wave * lateral_axis[axis]
        points.append(point)
    return points


def compile_motion(
    plan: KeyframeIkPlan,
    *,
    round_input: dict[str, Any],
    pose_data: dict[str, Any],
    client: Any,
    expressive_limits: Any,
) -> CompiledMotion:
    del round_input
    start_joints = load_joint_pose(pose_data, "curiosity_down")
    end_joints = load_joint_pose(pose_data, "anticipation_look_down")
    retreat_joints = load_joint_pose(pose_data, "disappointment_retreat_max")
    start_pose = client.forward_kinematics(start_joints)
    retreat_pose = client.forward_kinematics(retreat_joints)
    away_axis = _normalized([
        retreat_pose[index] - start_pose[index]
        for index in range(3)
    ])
    up_axis = [0.0, 0.0, 1.0]
    lateral_axis = _normalized(_cross(up_axis, away_axis))

    tcp_targets = [
        _frame_pose(
            start_pose,
            frame,
            away_axis=away_axis,
            lateral_axis=lateral_axis,
            limits=expressive_limits,
        )
        for frame in plan.keyframes
    ]
    tcp_path: list[list[float]] = []
    previous = start_pose
    for frame, target in zip(plan.keyframes, tcp_targets):
        segment = _dense_segment(
            previous,
            target,
            frame=frame,
            lateral_axis=lateral_axis,
        )
        for pose in segment:
            validate_tcp_pose(pose, expressive_limits)
        tcp_path.extend(segment)
        previous = target

    seed = list(start_joints)
    joint_path = [seed]
    for pose in tcp_path:
        seed = client.inverse_kinematics(pose, seed_joints=seed)
        joint_path.append(seed)
    validate_sequential_ik(joint_path, expressive_limits)

    holds = [0.0] * len(joint_path)
    holds[12] = 0.35 * plan.keyframes[0].hold_ratio
    holds[-1] = 0.45 * plan.keyframes[1].hold_ratio
    keyframes = joint_path + [end_joints]
    holds.append(0.0)
    emphasis = max(frame.speed_emphasis for frame in plan.keyframes)
    duration = sum(frame.duration_weight for frame in plan.keyframes) / 2.0
    return CompiledMotion(
        scheme=SCHEME_ID,
        keyframes=keyframes,
        velocity_rad_s=(0.14 + 0.10 * emphasis) / duration,
        acceleration_rad_s2=0.24 + 0.18 * emphasis,
        holds_s=holds,
        metadata={
            "tcp_keyframes": tcp_targets,
            "tcp_sample_count": len(tcp_path),
            "semantic_axes": {
                "away": away_axis,
                "lateral": lateral_axis,
                "up": up_axis,
            },
            "uses_sequential_seed_ik": True,
            "execution_note": "IK姿态仍需经用户研究验证其拟人性；安全检查不等于表现质量",
        },
        requires_cartesian_validation=True,
    )
