from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .config import SafetyConfig
from .safety import validate_joint_vector


class ExpressiveSafetyError(ValueError):
    pass


@dataclass(frozen=True)
class ExpressiveSafetyLimits:
    max_segment_joint_delta_rad: float
    max_sample_joint_delta_rad: float
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    max_jerk_rad_s3: float
    sample_period_s: float
    workspace_min_m: tuple[float, float, float]
    workspace_max_m: tuple[float, float, float]
    max_orientation_residual_rad: float
    max_ik_step_rad: float
    require_collision_model_for_cartesian: bool


def load_expressive_limits(path: Path) -> ExpressiveSafetyLimits:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExpressiveSafetyLimits(
        max_segment_joint_delta_rad=float(
            data["max_segment_joint_delta_rad"]
        ),
        max_sample_joint_delta_rad=float(
            data["max_sample_joint_delta_rad"]
        ),
        max_velocity_rad_s=float(data["max_velocity_rad_s"]),
        max_acceleration_rad_s2=float(
            data["max_acceleration_rad_s2"]
        ),
        max_jerk_rad_s3=float(data["max_jerk_rad_s3"]),
        sample_period_s=float(data["sample_period_s"]),
        workspace_min_m=tuple(
            float(value) for value in data["workspace_min_m"]
        ),
        workspace_max_m=tuple(
            float(value) for value in data["workspace_max_m"]
        ),
        max_orientation_residual_rad=float(
            data["max_orientation_residual_rad"]
        ),
        max_ik_step_rad=float(data["max_ik_step_rad"]),
        require_collision_model_for_cartesian=bool(
            data["require_collision_model_for_cartesian"]
        ),
    )


def maximum_difference(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ExpressiveSafetyError("向量长度不一致")
    return max(abs(float(x) - float(y)) for x, y in zip(a, b))


def interpolate_vector(
    start: Sequence[float],
    target: Sequence[float],
    scale: float,
) -> list[float]:
    if len(start) != len(target):
        raise ExpressiveSafetyError("插值向量长度不一致")
    factor = float(scale)
    return [
        float(a) + factor * (float(b) - float(a))
        for a, b in zip(start, target)
    ]


def _quintic(progress: float) -> float:
    return (
        10.0 * progress**3
        - 15.0 * progress**4
        + 6.0 * progress**5
    )


def sample_joint_keyframes(
    keyframes: Sequence[Sequence[float]],
    *,
    max_velocity_rad_s: float,
    sample_period_s: float,
    max_acceleration_rad_s2: float | None = None,
    max_jerk_rad_s3: float | None = None,
) -> list[list[float]]:
    if len(keyframes) < 2:
        raise ExpressiveSafetyError("至少需要两个关节关键帧")
    velocity = max(0.01, float(max_velocity_rad_s))
    period = max(0.005, float(sample_period_s))
    sampled: list[list[float]] = [
        [float(value) for value in keyframes[0]]
    ]
    for start, target in zip(keyframes, keyframes[1:]):
        distance = maximum_difference(start, target)
        # Peak normalized derivatives for 10t^3-15t^4+6t^5 are
        # 1.875 (velocity), about 5.774 (acceleration), and 60 (jerk).
        duration_candidates = [0.40, 1.875 * distance / velocity]
        if max_acceleration_rad_s2 is not None:
            acceleration = max(0.01, float(max_acceleration_rad_s2))
            duration_candidates.append(math.sqrt(5.774 * distance / acceleration))
        if max_jerk_rad_s3 is not None:
            jerk = max(0.01, float(max_jerk_rad_s3))
            duration_candidates.append((60.0 * distance / jerk) ** (1.0 / 3.0))
        duration = max(duration_candidates)
        steps = max(2, int(math.ceil(duration / period)))
        for index in range(1, steps + 1):
            blend = _quintic(index / steps)
            sampled.append(interpolate_vector(start, target, blend))
    return sampled


def validate_tcp_pose(
    pose: Sequence[float],
    limits: ExpressiveSafetyLimits,
) -> list[float]:
    values = [float(value) for value in pose]
    if len(values) != 6 or not all(math.isfinite(value) for value in values):
        raise ExpressiveSafetyError("TCP位姿必须是6个有限数值")
    for axis, value in enumerate(values[:3]):
        low = limits.workspace_min_m[axis]
        high = limits.workspace_max_m[axis]
        if value < low or value > high:
            raise ExpressiveSafetyError(
                f"TCP轴{axis}={value:.4f}超出工作空间[{low:.4f}, {high:.4f}]"
            )
    return values


def validate_joint_keyframes(
    keyframes: Sequence[Sequence[float]],
    robot_limits: SafetyConfig,
    expressive_limits: ExpressiveSafetyLimits,
) -> list[list[float]]:
    if len(keyframes) < 2:
        raise ExpressiveSafetyError("至少需要两个关节关键帧")
    validated = [
        validate_joint_vector(point, robot_limits)
        for point in keyframes
    ]
    for index, (start, target) in enumerate(
        zip(validated, validated[1:]),
        start=1,
    ):
        delta = maximum_difference(start, target)
        if delta > expressive_limits.max_segment_joint_delta_rad:
            raise ExpressiveSafetyError(
                f"关键帧段{index}关节跨度{delta:.4f}rad超过"
                f"{expressive_limits.max_segment_joint_delta_rad:.4f}rad"
            )
    return validated


def validate_sampled_trajectory(
    points: Sequence[Sequence[float]],
    robot_limits: SafetyConfig,
    expressive_limits: ExpressiveSafetyLimits,
) -> list[list[float]]:
    validated = [
        validate_joint_vector(point, robot_limits)
        for point in points
    ]
    if len(validated) < 2:
        raise ExpressiveSafetyError("轨迹至少需要两个采样点")

    period = expressive_limits.sample_period_s
    velocity_limit = min(
        robot_limits.max_velocity_rad_s,
        expressive_limits.max_velocity_rad_s,
    )
    acceleration_limit = min(
        robot_limits.max_acceleration_rad_s2,
        expressive_limits.max_acceleration_rad_s2,
    )
    velocities: list[list[float]] = []
    for index, (start, target) in enumerate(
        zip(validated, validated[1:]),
        start=1,
    ):
        delta = maximum_difference(start, target)
        if delta > expressive_limits.max_sample_joint_delta_rad:
            raise ExpressiveSafetyError(
                f"采样段{index}关节跳变{delta:.4f}rad超过限制"
            )
        segment_velocity = [
            (target_value - start_value) / period
            for start_value, target_value in zip(start, target)
        ]
        if max(abs(value) for value in segment_velocity) > velocity_limit * 1.02:
            raise ExpressiveSafetyError(
                f"采样段{index}速度超过{velocity_limit:.4f}rad/s"
            )
        velocities.append(segment_velocity)

    accelerations: list[list[float]] = []
    for index, (previous, current) in enumerate(
        zip(velocities, velocities[1:]),
        start=1,
    ):
        acceleration = [
            (current_value - previous_value) / period
            for previous_value, current_value in zip(previous, current)
        ]
        if max(abs(value) for value in acceleration) > acceleration_limit * 1.05:
            raise ExpressiveSafetyError(
                f"采样点{index}加速度超过{acceleration_limit:.4f}rad/s²"
            )
        accelerations.append(acceleration)

    for index, (previous, current) in enumerate(
        zip(accelerations, accelerations[1:]),
        start=1,
    ):
        jerk = max(
            abs(current_value - previous_value) / period
            for previous_value, current_value in zip(previous, current)
        )
        if jerk > expressive_limits.max_jerk_rad_s3 * 1.05:
            raise ExpressiveSafetyError(
                f"采样点{index}jerk={jerk:.4f}超过限制"
            )
    return validated


def validate_sequential_ik(
    joint_path: Iterable[Sequence[float]],
    limits: ExpressiveSafetyLimits,
) -> list[list[float]]:
    points = [[float(value) for value in point] for point in joint_path]
    for index, (start, target) in enumerate(zip(points, points[1:]), start=1):
        delta = maximum_difference(start, target)
        if delta > limits.max_ik_step_rad:
            raise ExpressiveSafetyError(
                f"IK采样点{index}关节跳变{delta:.4f}rad，疑似切换解分支"
            )
    return points
