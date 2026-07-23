#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AUBO ES3 水杯位置往返动作命令行工具。

默认使用全零示例姿态做离线预演；真实执行时记录启动后的当前姿态，
依次运动到固定水杯关节目标并返回该姿态。两段都是直接关节运动，不含
自动避障点，因此执行前必须人工确认整条路径安全。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from math import acos, cos, isfinite, sin, sqrt
from pathlib import Path
from typing import Sequence

# 允许直接运行脚本，同时仍能导入 src 下的本地包。
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
    validate_absolute_joint_target,
)
from aubo_sdk_client.config import SafetyConfig  # noqa: E402

CONFIRMATION_TEXT = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"
CUP_JOINTS_RAD = (
    0.31908,
    -0.588733,
    1.432912,
    -0.222756,
    1.742074,
    0.151014,
)
CUP_TCP_POSE = (
    0.413412,
    0.028213,
    0.154102,
    -2.450776,
    -0.03654,
    1.6655,
)
DEFAULT_CUP_HOLD_S = 2.0
NORMAL_ACTION_VELOCITY_RAD_S = 0.35
NORMAL_ACTION_ACCELERATION_RAD_S2 = 0.70
MAX_ACTION_VELOCITY_RAD_S = 0.50
MAX_ACTION_ACCELERATION_RAD_S2 = 1.00
TARGET_TOLERANCE_RAD = 0.015
TARGET_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.05
MOTION_RPC_TIMEOUT_MS = 60_000
TCP_POSITION_TOLERANCE_M = 0.010
TCP_ORIENTATION_TOLERANCE_RAD = 0.10


@dataclass(frozen=True)
class CupMotionStep:
    """一个绝对关节目标，以及到位后需要主动停留的时间。"""

    label: str
    target_rad: tuple[float, ...]
    hold_s: float


def build_cup_round_trip(
    normal_rad: Sequence[float],
    safety: SafetyConfig,
    cup_hold_s: float = DEFAULT_CUP_HOLD_S,
) -> tuple[CupMotionStep, CupMotionStep]:
    """生成且只生成“起始正常姿态 → 水杯 → 起始正常姿态”两步。
    
    仅水杯目标带主动停顿；两段均为直接关节运动，不自动生成避障中间点。
    每个绝对目标都会先经过配置软限位检查。
    """

    normal = validate_absolute_joint_target(normal_rad, safety, "启动时正常姿态")
    cup = validate_absolute_joint_target(CUP_JOINTS_RAD, safety, "水杯目标姿态")
    hold = float(cup_hold_s)
    if not isfinite(hold) or not 0.0 <= hold <= 30.0:
        raise SafetyError("水杯处停顿时间必须在 0 到 30 秒之间。")
    if max(abs(cup_value - normal_value) for cup_value, normal_value in zip(cup, normal)) <= 1e-6:
        raise SafetyError("当前正常姿态已经等于水杯目标姿态，没有可执行的往返动作。")
    return (
        CupMotionStep("前往水杯位置", cup, hold),
        CupMotionStep("返回启动时正常位置", normal, 0.0),
    )


def validate_motion_parameters(velocity: float, acceleration: float) -> tuple[float, float]:
    """校验动作速度和加速度，并强制应用本动作独立的硬上限。"""

    velocity_value = float(velocity)
    acceleration_value = float(acceleration)
    if not isfinite(velocity_value) or not 0.05 <= velocity_value <= MAX_ACTION_VELOCITY_RAD_S:
        raise SafetyError(
            f"动作速度必须在 0.05 到 {MAX_ACTION_VELOCITY_RAD_S:.2f} rad/s 之间。"
        )
    if not isfinite(acceleration_value) or not 0.05 <= acceleration_value <= MAX_ACTION_ACCELERATION_RAD_S2:
        raise SafetyError(
            f"动作加速度必须在 0.05 到 {MAX_ACTION_ACCELERATION_RAD_S2:.2f} rad/s^2 之间。"
        )
    return velocity_value, acceleration_value


def wait_until_target(
    client: AuboClient,
    target_rad: Sequence[float],
    timeout_s: float = TARGET_TIMEOUT_S,
    tolerance_rad: float = TARGET_TOLERANCE_RAD,
) -> None:
    """轮询关节状态，直到所有关节进入容差或等待超时。"""

    deadline = time.monotonic() + timeout_s
    last_error = float("inf")
    while time.monotonic() < deadline:
        actual = client.snapshot().joint_positions_rad
        if len(actual) != len(target_rad):
            raise AuboClientError("机器人返回的关节数量与目标不一致。")
        last_error = max(abs(value - target) for value, target in zip(actual, target_rad))
        if last_error <= tolerance_rad:
            return
        time.sleep(POLL_INTERVAL_S)
    raise AuboClientError(
        f"等待机械臂到位超时（最大关节误差 {last_error:.4f} rad）；"
        "已停止发送后续动作。请查看控制器状态，必要时使用急停。"
    )


def _rotation_vector_to_matrix(vector: Sequence[float]) -> tuple[tuple[float, ...], ...]:
    """用 Rodrigues 公式把三维旋转向量转换为 3×3 旋转矩阵。"""

    x, y, z = (float(value) for value in vector)
    angle = sqrt(x * x + y * y + z * z)
    if angle < 1e-12:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    x, y, z = x / angle, y / angle, z / angle
    c = cos(angle)
    s = sin(angle)
    one_minus_c = 1.0 - c
    return (
        (c + x * x * one_minus_c, x * y * one_minus_c - z * s, x * z * one_minus_c + y * s),
        (y * x * one_minus_c + z * s, c + y * y * one_minus_c, y * z * one_minus_c - x * s),
        (z * x * one_minus_c - y * s, z * y * one_minus_c + x * s, c + z * z * one_minus_c),
    )


def tcp_pose_error(actual_pose: Sequence[float], expected_pose: Sequence[float]) -> tuple[float, float]:
    """返回 TCP 位置误差（米）和最短姿态角误差（弧度）。"""

    actual = tuple(float(value) for value in actual_pose)
    expected = tuple(float(value) for value in expected_pose)
    if len(actual) != 6 or len(expected) != 6:
        raise SafetyError("TCP 位姿必须包含 6 个值。")
    if not all(isfinite(value) for value in actual + expected):
        raise SafetyError("TCP 位姿包含 NaN 或无穷大。")

    position_error = sqrt(sum((actual[i] - expected[i]) ** 2 for i in range(3)))
    actual_rotation = _rotation_vector_to_matrix(actual[3:])
    expected_rotation = _rotation_vector_to_matrix(expected[3:])
    # trace(R_expected^T * R_actual) equals the element-wise dot product here.
    relative_trace = sum(
        expected_rotation[row][column] * actual_rotation[row][column]
        for row in range(3)
        for column in range(3)
    )
    cosine = max(-1.0, min(1.0, (relative_trace - 1.0) / 2.0))
    return position_error, acos(cosine)


def move_error_description(code: int) -> str:
    """把常见 ``moveJoint`` 返回码转换为便于排查的中文说明。"""

    descriptions = {
        1: "机器人状态不允许执行该动作",
        2: "运动队列已满",
        3: "控制器正忙",
        4: "调用超时",
        5: "参数无效",
        14: "轨迹规划失败",
        18: "目标位置超限",
        21: "轨迹生成失败",
        22: "轨迹存在自碰撞",
    }
    return descriptions.get(int(code), "未识别的 SDK 错误")


def _check_move_result(result: object, label: str) -> None:
    """统一检查 SDK 运动返回值，非零整数立即终止后续步骤。"""

    if type(result) is int and result != 0:
        detail = move_error_description(result)
        if result == 4:
            raise AuboClientError(
                f"moveJoint 在步骤“{label}”返回错误码 4（{detail}）。"
                f"当前运动专用 RPC 超时已设为 {MOTION_RPC_TIMEOUT_MS} ms；"
                "若机械臂仍在运动，不要把超时理解为指令一定未执行，"
                "请观察控制器状态并保持急停可用。已停止发送后续动作。"
            )
        raise AuboClientError(
            f"moveJoint 在步骤“{label}”返回错误码 {result}（{detail}）；"
            "已停止发送后续动作。"
        )


def print_plan(normal_rad: Sequence[float], steps: Sequence[CupMotionStep]) -> None:
    """打印起点、每步绝对目标和停顿时间，供操作者执行前复核。"""

    print("启动时正常关节角(rad)：", [round(float(value), 6) for value in normal_rad])
    print("水杯目标关节角(rad)：", [round(value, 6) for value in CUP_JOINTS_RAD])
    print("水杯参考 TCP 位姿：", [round(value, 6) for value in CUP_TCP_POSE])
    print("动作顺序：")
    for index, step in enumerate(steps, start=1):
        hold_text = f"，到位后停顿 {step.hold_s:.2f}s" if step.hold_s > 0 else "，到位后不额外停顿"
        print(f"  {index}. {step.label}{hold_text}")


def build_parser() -> argparse.ArgumentParser:
    """定义离线预演和真机执行参数。"""

    parser = argparse.ArgumentParser(
        description="AUBO ES3 当前正常位置到水杯位置再返回。默认仅离线预演。"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "robot.local.json"),
        help="本地 JSON 配置文件路径。",
    )
    parser.add_argument("--cup-hold", type=float, default=DEFAULT_CUP_HOLD_S)
    parser.add_argument("--velocity", type=float, default=NORMAL_ACTION_VELOCITY_RAD_S)
    parser.add_argument("--acceleration", type=float, default=NORMAL_ACTION_ACCELERATION_RAD_S2)
    parser.add_argument("--execute", action="store_true", help="允许连接真机并执行动作。")
    parser.add_argument("--confirm-motion", default="")
    parser.add_argument("--countdown", type=float, default=5.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """预演两步计划；通过固定确认和倒计时后才连接并执行真机动作。"""

    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        action_velocity, action_acceleration = validate_motion_parameters(
            args.velocity, args.acceleration
        )
        validate_absolute_joint_target(CUP_JOINTS_RAD, config.safety, "水杯目标姿态")

        # 离线模式使用全零示例起点，只检查计划和参数，不连接真机。
        if not args.execute:
            fake_normal = (0.0,) * config.safety.joint_count
            steps = build_cup_round_trip(fake_normal, config.safety, args.cup_hold)
            print("DRY-RUN：仅使用全零示例正常姿态预演，不连接、不上电、不运动。")
            print_plan(fake_normal, steps)
            print(
                f"动作速度 {action_velocity:.2f} rad/s；"
                f"加速度 {action_acceleration:.2f} rad/s^2。"
            )
            print("\n真实执行请运行项目根目录的 main.py，并在菜单中选择水杯动作。")
            return 0

        if args.confirm_motion != CONFIRMATION_TEXT:
            print("拒绝运动：确认文本不正确。", file=sys.stderr)
            return 3
        if not isfinite(args.countdown) or args.countdown < 3.0:
            raise SafetyError("真实运动倒计时不能少于 3 秒。")

        print("正在连接机械臂并读取当前正常姿态……")
        with AuboClient(config.robot) as client:
            before = client.snapshot()
            preview_steps = build_cup_round_trip(
                before.joint_positions_rad, config.safety, args.cup_hold
            )
            print_plan(before.joint_positions_rad, preview_steps)
            print(
                f"动作速度 {action_velocity:.3f} rad/s；"
                f"动作加速度 {action_acceleration:.3f} rad/s^2。"
            )

            seconds = int(args.countdown)
            print(f"\n{seconds} 秒后上电/启动并执行。可按 Ctrl+C 取消；急停必须保持可达。")
            for remaining in range(seconds, 0, -1):
                print(f"  {remaining}……", flush=True)
                time.sleep(1.0)

            client.prepare_for_motion(
                config.safety.power_on_wait_s, config.safety.startup_wait_s
            )

            # moveJoint 是可能持续数秒的 RPC 调用。状态读取保留配置中的短超时，
            # 真正开始运动前再把运动调用超时提高，避免较长动作被 3 秒超时截断。
            motion_timeout_ms = max(
                config.robot.request_timeout_ms, MOTION_RPC_TIMEOUT_MS
            )
            client.set_request_timeout(motion_timeout_ms)
            print(f"运动 RPC 超时已设置为 {motion_timeout_ms} ms。")

            # 以真正开始运动前的姿态作为最终返回目标，避免上电阶段状态变化。
            # 以真正开始运动前的最新姿态作为最终返回目标。
            start = client.snapshot()
            steps = build_cup_round_trip(
                start.joint_positions_rad, config.safety, args.cup_hold
            )
            print("启动后重新读取正常姿态并通过软限位校验，开始往返动作。")
            print("最终返回目标(rad)：", [round(value, 6) for value in steps[1].target_rad])

            cup_step, return_step = steps
            print(f"[1/2] {cup_step.label}")
            result = client.move_joint(
                cup_step.target_rad, action_acceleration, action_velocity
            )
            _check_move_result(result, cup_step.label)
            wait_until_target(client, cup_step.target_rad)

            # TCP 是辅助核验信息，不允许一次状态读取失败阻断返程。
            try:
                at_cup = client.snapshot()
                position_error, orientation_error = tcp_pose_error(
                    at_cup.tcp_pose, CUP_TCP_POSE
                )
                print("水杯处实际 TCP 位姿：", [round(value, 6) for value in at_cup.tcp_pose])
                print(
                    f"水杯 TCP 误差：位置 {position_error * 1000.0:.1f} mm；"
                    f"姿态 {orientation_error:.4f} rad。"
                )
                if (
                    position_error > TCP_POSITION_TOLERANCE_M
                    or orientation_error > TCP_ORIENTATION_TOLERANCE_RAD
                ):
                    print(
                        "警告：实际 TCP 与参考值偏差较大。关节目标已到位，"
                        "程序仍会按计划停顿后返回启动姿态。",
                        file=sys.stderr,
                    )
            except (AuboClientError, SafetyError) as exc:
                print(
                    f"警告：水杯处 TCP 辅助核验失败（{exc}），"
                    "程序仍会按计划停顿后返回启动姿态。",
                    file=sys.stderr,
                )

            if cup_step.hold_s > 0:
                print(f"仅在水杯位置停顿 {cup_step.hold_s:.2f} 秒……")
                time.sleep(cup_step.hold_s)

            print(f"[2/2] {return_step.label}（中途不设停顿）")
            result = client.move_joint(
                return_step.target_rad, action_acceleration, action_velocity
            )
            _check_move_result(result, return_step.label)
            wait_until_target(client, return_step.target_rad)

            after = client.snapshot()
            return_error = max(
                abs(actual - target)
                for actual, target in zip(after.joint_positions_rad, return_step.target_rad)
            )
            print(
                f"[完成] 已返回启动时正常姿态，最大关节误差 {return_error:.4f} rad。"
            )
            return 0

    except KeyboardInterrupt:
        print("\n已取消或中断。若机械臂仍在运动，请立即使用控制器停止或急停。", file=sys.stderr)
        return 130
    except (ConfigError, SafetyError, AuboClientError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())



