from __future__ import annotations

from typing import Iterable, List

from .config import SafetyConfig


class SafetyError(ValueError):
    pass


def validate_joint_vector(values: Iterable[float], safety: SafetyConfig) -> List[float]:
    joints = [float(v) for v in values]
    if len(joints) != safety.joint_count:
        raise SafetyError(f"需要 {safety.joint_count} 个关节值，实际收到 {len(joints)} 个")
    for idx, value in enumerate(joints):
        low = safety.joint_min_rad[idx]
        high = safety.joint_max_rad[idx]
        if value < low or value > high:
            raise SafetyError(
                f"关节 {idx} 目标 {value:.6f} rad 超出限制 [{low:.6f}, {high:.6f}]"
            )
    return joints


def validate_delta(joint_index: int, delta: float, safety: SafetyConfig) -> float:
    if joint_index < 0 or joint_index >= safety.joint_count:
        raise SafetyError(f"关节编号必须在 0 到 {safety.joint_count - 1} 之间")
    delta = float(delta)
    max_delta = safety.max_delta_rad[joint_index]
    if abs(delta) > max_delta:
        raise SafetyError(
            f"关节 {joint_index} 单次移动 {delta:.6f} rad 超过限制 {max_delta:.6f} rad"
        )
    return delta


def make_jog_target(current: Iterable[float], joint_index: int, delta: float, safety: SafetyConfig) -> List[float]:
    joints = validate_joint_vector(current, safety)
    delta = validate_delta(joint_index, delta, safety)
    joints[joint_index] += delta
    return validate_joint_vector(joints, safety)
