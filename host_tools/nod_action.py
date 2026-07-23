#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AUBO ES3 点头动作的轨迹生成与真机执行工具。

默认离线预演，不连接、不上电、不运动。全身模式让 J1～J6 协同动作，
单腕模式保留原有小幅测试；所有关键姿态都会按单步增量上限插值并检查
软限位，真实执行还要求固定确认文本和倒计时。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from math import ceil, isfinite
from pathlib import Path
from typing import Sequence

# 支持直接运行脚本，无需先安装 src 下的本地包。
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client import (  # noqa: E402
    AuboClient,
    AuboClientError,
    ConfigError,
    SafetyError,
    load_config,
    preview_relative_joint_move,
)
from aubo_sdk_client.config import SafetyConfig  # noqa: E402

CONFIRMATION_TEXT = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"
DEFAULT_STYLE = "full-body"
DEFAULT_JOINT_NUMBER = 5
DEFAULT_WRIST_AMPLITUDE_RAD = 0.04
DEFAULT_BODY_SCALE = 1.0
MAX_BODY_SCALE = 1.5
TARGET_TOLERANCE_RAD = 0.015
TARGET_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.05
NORMAL_ACTION_VELOCITY_RAD_S = 0.35
NORMAL_ACTION_ACCELERATION_RAD_S2 = 0.70
MAX_ACTION_VELOCITY_RAD_S = 0.50
MAX_ACTION_ACCELERATION_RAD_S2 = 1.00
TRANSITION_HOLD_S = 0.0


@dataclass(frozen=True)
class ActionKeyframe:
    """相对动作起点定义的六关节关键姿态。"""

    label: str
    offset_rad: tuple[float, ...]
    hold_s: float


@dataclass(frozen=True)
class NodWaypoint:
    """经过单步增量和软限位校验后的绝对关节目标。"""

    label: str
    target_rad: tuple[float, ...]
    hold_s: float
    keyframe_end: bool = False


# 全身点头关键姿态。每个值都是相对动作起始姿态的关节偏移，单位 rad。
# J1/J4/J6 提供身体侧向配合，J2/J3/J5 形成明显的抬头和俯身效果。
FULL_BODY_KEYFRAMES = (
    ActionKeyframe("展开抬头", (+0.035, -0.060, +0.080, -0.050, +0.100, +0.055), 0.10),
    ActionKeyframe("大幅点头", (-0.035, +0.080, -0.100, +0.060, -0.120, -0.055), 0.12),
    ActionKeyframe("有力回弹", (+0.025, -0.050, +0.070, -0.045, +0.085, +0.045), 0.08),
    ActionKeyframe("再次确认", (-0.025, +0.060, -0.080, +0.045, -0.100, -0.045), 0.10),
    ActionKeyframe("回到中位", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0), 0.15),
)


def _validate_current(current_rad: Sequence[float], safety: SafetyConfig) -> tuple[float, ...]:
    """检查动作起点的维度、有限性和软限位。"""

    current = tuple(float(value) for value in current_rad)
    if len(current) != safety.joint_count:
        raise SafetyError(f"当前关节角必须包含 {safety.joint_count} 个值。")
    # 用一个极小但非零且最终取消的动作借用现有验证并不合适，因此直接检查有限值和软限位。
    if not all(isfinite(value) for value in current):
        raise SafetyError("当前关节角包含 NaN 或无穷大。")
    for index, value in enumerate(current):
        if not safety.joint_min_rad[index] <= value <= safety.joint_max_rad[index]:
            raise SafetyError(f"当前 J{index + 1}={value:.4f} rad 已超出配置软限位。")
    return current


def _interpolate_keyframes(
    current_rad: Sequence[float],
    keyframes: Sequence[ActionKeyframe],
    safety: SafetyConfig,
) -> tuple[NodWaypoint, ...]:
    """把幅度较大的关键姿态拆成符合每关节单步限制的小 waypoint。"""

    neutral = _validate_current(current_rad, safety)
    if safety.joint_count != 6:
        raise SafetyError("全身点头动作目前只支持 6 关节机械臂。")

    previous_offset = (0.0,) * safety.joint_count
    previous_target = neutral
    waypoints: list[NodWaypoint] = []

    for keyframe in keyframes:
        if len(keyframe.offset_rad) != safety.joint_count:
            raise SafetyError(f"关键姿态“{keyframe.label}”不是 6 关节向量。")
        offset_delta = tuple(
            target - previous
            for target, previous in zip(keyframe.offset_rad, previous_offset)
        )
        # 只使用配置单步上限的 80%，避免浮点边界和控制器插补误差。
        segment_count = max(
            1,
            max(
                ceil(abs(delta) / (safety.max_delta_rad[index] * 0.8))
                for index, delta in enumerate(offset_delta)
            ),
        )

        for segment in range(1, segment_count + 1):
            fraction = segment / segment_count
            interpolated_offset = tuple(
                start + delta * fraction
                for start, delta in zip(previous_offset, offset_delta)
            )
            target = tuple(
                base + offset for base, offset in zip(neutral, interpolated_offset)
            )
            delta = tuple(
                target_value - previous_value
                for target_value, previous_value in zip(target, previous_target)
            )
            preview = preview_relative_joint_move(previous_target, delta, safety)
            is_keyframe_end = segment == segment_count
            waypoints.append(
                NodWaypoint(
                    keyframe.label if is_keyframe_end else f"{keyframe.label}-过渡{segment}",
                    preview.target_rad,
                    keyframe.hold_s if is_keyframe_end else TRANSITION_HOLD_S,
                    is_keyframe_end,
                )
            )
            previous_target = preview.target_rad

        previous_offset = keyframe.offset_rad

    if any(abs(actual - expected) > 1e-9 for actual, expected in zip(previous_target, neutral)):
        raise SafetyError("动作定义错误：全身动作结束后没有回到起始姿态。")
    return tuple(waypoints)


def build_full_body_nod_waypoints(
    current_rad: Sequence[float],
    safety: SafetyConfig,
    scale: float = DEFAULT_BODY_SCALE,
    count: int = 1,
) -> tuple[NodWaypoint, ...]:
    """生成 J1～J6 协调参与、最终回到起点的全身点头轨迹。"""

    scale_value = float(scale)
    if not isfinite(scale_value) or not 0.25 <= scale_value <= MAX_BODY_SCALE:
        raise SafetyError(f"全身动作倍率必须在 0.25 到 {MAX_BODY_SCALE} 之间。")
    if count < 1 or count > 3:
        raise SafetyError("全身点头次数必须在 1 到 3 之间。")

    scaled_cycle = tuple(
        ActionKeyframe(
            keyframe.label,
            tuple(value * scale_value for value in keyframe.offset_rad),
            keyframe.hold_s,
        )
        for keyframe in FULL_BODY_KEYFRAMES
    )
    return _interpolate_keyframes(current_rad, scaled_cycle * count, safety)


def build_wrist_nod_waypoints(
    current_rad: Sequence[float],
    safety: SafetyConfig,
    joint_number: int = DEFAULT_JOINT_NUMBER,
    amplitude_rad: float = DEFAULT_WRIST_AMPLITUDE_RAD,
    count: int = 1,
) -> tuple[NodWaypoint, ...]:
    """生成仅指定腕部关节运动的小幅兼容轨迹。"""

    if not 1 <= joint_number <= safety.joint_count:
        raise SafetyError(f"动作关节必须在 J1 到 J{safety.joint_count} 之间。")
    amplitude = float(amplitude_rad)
    if not isfinite(amplitude) or amplitude <= 0:
        raise SafetyError("腕部点头幅度必须是大于 0 的有限弧度值。")
    if count < 1 or count > 5:
        raise SafetyError("腕部点头次数必须在 1 到 5 之间。")

    joint_index = joint_number - 1
    keyframes: list[ActionKeyframe] = []
    for _ in range(count):
        for label, offset, hold in (
            ("轻微抬头", +amplitude, 0.35),
            ("回到中位", 0.0, 0.20),
            ("轻微低头", -amplitude, 0.35),
            ("回到起点", 0.0, 0.35),
        ):
            values = [0.0] * safety.joint_count
            values[joint_index] = offset
            keyframes.append(ActionKeyframe(label, tuple(values), hold))
    return _interpolate_keyframes(current_rad, keyframes, safety)


def build_nod_waypoints(
    current_rad: Sequence[float],
    safety: SafetyConfig,
    style: str = DEFAULT_STYLE,
    scale: float = DEFAULT_BODY_SCALE,
    joint_number: int = DEFAULT_JOINT_NUMBER,
    amplitude_rad: float = DEFAULT_WRIST_AMPLITUDE_RAD,
    count: int = 1,
) -> tuple[NodWaypoint, ...]:
    """按动作风格分派到全身点头或单腕点头轨迹生成器。"""

    if style == "full-body":
        return build_full_body_nod_waypoints(current_rad, safety, scale, count)
    if style == "wrist":
        return build_wrist_nod_waypoints(
            current_rad, safety, joint_number, amplitude_rad, count
        )
    raise SafetyError(f"未知动作风格：{style}")


def print_waypoints(
    current_rad: Sequence[float],
    waypoints: Sequence[NodWaypoint],
    style: str,
) -> None:
    """列出所有安全拆分后的绝对 waypoint，便于人工检查。"""

    print("起始关节角(rad)：", [round(float(value), 5) for value in current_rad])
    print("动作风格：", "J1~J6 全身协调点头" if style == "full-body" else "单腕小幅点头")
    print(f"共 {len(waypoints)} 个经过安全拆分的 waypoint：")
    for index, waypoint in enumerate(waypoints, start=1):
        target = ", ".join(f"{value:.4f}" for value in waypoint.target_rad)
        marker = "关键姿态" if waypoint.keyframe_end else "过渡"
        print(f"  {index:02d}. [{marker}] {waypoint.label:<12} [{target}]")


def wait_until_target(
    client: AuboClient,
    target_rad: Sequence[float],
    timeout_s: float = TARGET_TIMEOUT_S,
    tolerance_rad: float = TARGET_TOLERANCE_RAD,
) -> None:
    """轮询实际关节角，确认当前 waypoint 已在容差内到位。"""

    deadline = time.monotonic() + timeout_s
    last_error = float("inf")
    while time.monotonic() < deadline:
        actual = client.snapshot().joint_positions_rad
        last_error = max(abs(value - target) for value, target in zip(actual, target_rad))
        if last_error <= tolerance_rad:
            return
        time.sleep(POLL_INTERVAL_S)
    raise AuboClientError(
        f"等待机械臂到位超时（最大关节误差 {last_error:.4f} rad）；"
        "已停止发送后续 waypoint。请查看控制器状态，必要时使用急停。"
    )


def validate_motion_parameters(velocity: float, acceleration: float) -> tuple[float, float]:
    """校验点头速度和加速度，并执行独立于配置文件的硬上限。"""

    velocity_value = float(velocity)
    acceleration_value = float(acceleration)
    if not isfinite(velocity_value) or not 0.05 <= velocity_value <= MAX_ACTION_VELOCITY_RAD_S:
        raise SafetyError(
            f"点头速度必须在 0.05 到 {MAX_ACTION_VELOCITY_RAD_S:.2f} rad/s 之间。"
        )
    if not isfinite(acceleration_value) or not 0.05 <= acceleration_value <= MAX_ACTION_ACCELERATION_RAD_S2:
        raise SafetyError(
            f"点头加速度必须在 0.05 到 {MAX_ACTION_ACCELERATION_RAD_S2:.2f} rad/s² 之间。"
        )
    return velocity_value, acceleration_value

def build_parser() -> argparse.ArgumentParser:
    """定义动作风格、幅度、速度以及真机确认参数。"""

    parser = argparse.ArgumentParser(
        description="AUBO ES3 点头动作。命令行默认只离线预演，不会运动。"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "robot.local.json"),
        help="本地 JSON 配置文件路径。",
    )
    parser.add_argument(
        "--style",
        choices=("full-body", "wrist"),
        default=DEFAULT_STYLE,
        help="full-body 为默认全身动作；wrist 为原来的单腕小动作。",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_BODY_SCALE,
        help="全身动作幅度倍率，范围 0.25~1.5，默认 1.0。",
    )
    parser.add_argument("--joint", type=int, default=DEFAULT_JOINT_NUMBER)
    parser.add_argument("--amplitude", type=float, default=DEFAULT_WRIST_AMPLITUDE_RAD)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--velocity",
        type=float,
        default=NORMAL_ACTION_VELOCITY_RAD_S,
        help="点头动作速度(rad/s)，默认 0.35，最大 0.50。",
    )
    parser.add_argument(
        "--acceleration",
        type=float,
        default=NORMAL_ACTION_ACCELERATION_RAD_S2,
        help="点头动作加速度(rad/s²)，默认 0.70，最大 1.00。",
    )
    parser.add_argument("--execute", action="store_true", help="允许连接真机并执行动作。")
    parser.add_argument("--confirm-motion", default="")
    parser.add_argument("--countdown", type=float, default=5.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """生成点头轨迹；默认离线预演，确认后才执行真机 waypoint。"""

    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        action_velocity, action_acceleration = validate_motion_parameters(
            args.velocity, args.acceleration
        )

        # dry-run 用全零起点展示相对动作形状，不读取或控制机器人。
        if not args.execute:
            fake_current = (0.0,) * config.safety.joint_count
            waypoints = build_nod_waypoints(
                fake_current,
                config.safety,
                args.style,
                args.scale,
                args.joint,
                args.amplitude,
                args.count,
            )
            print("DRY-RUN：仅按全零示例姿态预演，不连接、不上电、不运动。")
            print_waypoints(fake_current, waypoints, args.style)
            print(
                f"预设正常速度：{action_velocity:.2f} rad/s；"
                f"加速度：{action_acceleration:.2f} rad/s²。"
            )
            print("\n真实执行请运行项目根目录的 main.py，并在菜单中选择点头动作。")
            return 0

        if args.confirm_motion != CONFIRMATION_TEXT:
            print("拒绝运动：确认文本不正确。", file=sys.stderr)
            return 3
        if not isfinite(args.countdown) or args.countdown < 3.0:
            raise SafetyError("真实运动倒计时不能少于 3 秒。")


        print("正在连接机械臂并读取当前姿态……")
        with AuboClient(config.robot) as client:
            before = client.snapshot()
            waypoints = build_nod_waypoints(
                before.joint_positions_rad,
                config.safety,
                args.style,
                args.scale,
                args.joint,
                args.amplitude,
                args.count,
            )
            print_waypoints(before.joint_positions_rad, waypoints, args.style)
            print(
                f"点头速度 {action_velocity:.3f} rad/s；"
                f"点头加速度 {action_acceleration:.3f} rad/s²。"
            )

            seconds = int(args.countdown)
            print(f"\n{seconds} 秒后上电/启动并执行。可按 Ctrl+C 取消；急停必须保持可达。")
            for remaining in range(seconds, 0, -1):
                print(f"  {remaining}……", flush=True)
                time.sleep(1.0)

            client.prepare_for_motion(
                config.safety.power_on_wait_s, config.safety.startup_wait_s
            )

            # 上电/启动后重新读取起点，避免使用准备阶段之前的旧状态。
            start = client.snapshot()
            waypoints = build_nod_waypoints(
                start.joint_positions_rad,
                config.safety,
                args.style,
                args.scale,
                args.joint,
                args.amplitude,
                args.count,
            )
            print("启动后重新校验通过，开始动作。")

            for index, waypoint in enumerate(waypoints, start=1):
                print(f"[{index}/{len(waypoints)}] {waypoint.label}")
                result = client.move_joint(
                    waypoint.target_rad,
                    action_acceleration,
                    action_velocity,
                )
                if isinstance(result, int) and result != 0:
                    raise AuboClientError(
                        f"moveJoint 在步骤“{waypoint.label}”返回错误码 {result}；"
                        "已停止发送后续 waypoint。"
                    )
                wait_until_target(client, waypoint.target_rad)
                time.sleep(waypoint.hold_s)

            print("[完成] 全身点头动作结束，机械臂已回到动作开始时的关节姿态。")
            return 0

    except KeyboardInterrupt:
        print("\n已取消或中断。若机械臂仍在运动，请立即使用控制器停止或急停。", file=sys.stderr)
        return 130
    except (ConfigError, SafetyError, AuboClientError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
