"""关节空间运动的纯计算安全校验。

本模块不连接机器人，只验证向量长度、有限数、当前/目标软限位和单次
增量，并生成便于人工复核的运动预览。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import degrees, isfinite
from typing import Sequence

from .config import SafetyConfig


class SafetyError(ValueError):
    """请求的运动超出当前配置的软件安全边界时抛出。"""


@dataclass(frozen=True)
class MotionPreview:
    """一次相对运动的当前值、增量和目标值快照。"""

    current_rad: tuple[float, ...]
    delta_rad: tuple[float, ...]
    target_rad: tuple[float, ...]

    def as_text(self) -> str:
        """以 rad/degree 对照表输出六个关节，供执行前人工检查。"""

        lines = [
            "关节   当前(rad/deg)       增量(rad/deg)       目标(rad/deg)",
            "-----  ------------------  ------------------  ------------------",
        ]
        for index, (current, delta, target) in enumerate(
            zip(self.current_rad, self.delta_rad, self.target_rad), start=1
        ):
            lines.append(
                f"J{index:<4} {current:>8.4f}/{degrees(current):>7.2f}°  "
                f"{delta:>+8.4f}/{degrees(delta):>+7.2f}°  "
                f"{target:>8.4f}/{degrees(target):>7.2f}°"
            )
        return "\n".join(lines)


def _validate_vector(values: Sequence[float], name: str, count: int) -> tuple[float, ...]:
    """校验关节向量长度和有限性，并统一转换为浮点元组。"""

    if len(values) != count:
        raise SafetyError(f"{name} 必须包含 {count} 个关节值。")
    converted = tuple(float(value) for value in values)
    if not all(isfinite(value) for value in converted):
        raise SafetyError(f"{name} 包含 NaN 或无穷大。")
    return converted


def validate_absolute_joint_target(
    target_rad: Sequence[float],
    safety: SafetyConfig,
    name: str = "目标关节角",
) -> tuple[float, ...]:
    """检查完整绝对关节目标是否位于配置软限位内。"""

    target = _validate_vector(target_rad, name, safety.joint_count)
    for index, target_value in enumerate(target):
        if not safety.joint_min_rad[index] <= target_value <= safety.joint_max_rad[index]:
            raise SafetyError(
                f"{name} J{index + 1}={target_value:.4f} rad 超出配置软限位 "
                f"[{safety.joint_min_rad[index]:.4f}, {safety.joint_max_rad[index]:.4f}]。"
            )
    return target


def preview_relative_joint_move(
    current_rad: Sequence[float],
    delta_rad: Sequence[float],
    safety: SafetyConfig,
) -> MotionPreview:
    """检查相对运动的单步增量和目标软限位，并返回可读预览。"""

    current = _validate_vector(current_rad, "当前关节角", safety.joint_count)
    delta = _validate_vector(delta_rad, "关节增量", safety.joint_count)
    # 目标值只由“当前值 + 相对增量”得到，随后逐关节检查三者。
    target = tuple(current_value + delta_value for current_value, delta_value in zip(current, delta))

    if all(abs(value) < 1e-12 for value in delta):
        raise SafetyError("所有关节增量均为 0，没有可执行的运动。")

    for index, (current_value, delta_value, target_value) in enumerate(
        zip(current, delta, target)
    ):
        if not safety.joint_min_rad[index] <= current_value <= safety.joint_max_rad[index]:
            raise SafetyError(
                f"当前 J{index + 1}={current_value:.4f} rad 已超出配置软限位；拒绝运动。"
            )
        if abs(delta_value) > safety.max_delta_rad[index]:
            raise SafetyError(
                f"J{index + 1} 增量 {delta_value:.4f} rad 超过单次限制 "
                f"{safety.max_delta_rad[index]:.4f} rad。"
            )
        if not safety.joint_min_rad[index] <= target_value <= safety.joint_max_rad[index]:
            raise SafetyError(
                f"目标 J{index + 1}={target_value:.4f} rad 超出配置软限位。"
            )

    return MotionPreview(current, delta, target)

