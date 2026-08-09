#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.sdk_client import (
    AuboSdkClient,
    AuboSdkError,
    JOINT_NAMES,
)

CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"
POSE_FILE = ROOT / "config" / "emotion_poses_new_es3.json"


def load_pose(
    data: dict,
    pose_name: str,
) -> list[float]:
    try:
        joints = data["poses"][pose_name]["joint_positions_rad"]
    except KeyError as exc:
        raise KeyError(
            f"点位 {pose_name} 不完整：{exc}"
        ) from exc

    return [
        float(joints[name])
        for name in JOINT_NAMES
    ]


def max_joint_difference(
    current: Sequence[float],
    target: Sequence[float],
) -> float:
    return max(
        abs(a - b)
        for a, b in zip(current, target)
    )


def wait_pose(
    client: AuboSdkClient,
    target: Sequence[float],
    *,
    timeout: float,
    tolerance: float = 0.018,
) -> float:
    deadline = time.monotonic() + timeout
    stable_count = 0
    last_error = float("inf")

    while time.monotonic() < deadline:
        current = client.current_joints()

        last_error = max_joint_difference(
            current,
            target,
        )

        if last_error <= tolerance:
            stable_count += 1

            if stable_count >= 4:
                return last_error
        else:
            stable_count = 0

        time.sleep(0.025)

    raise AuboSdkError(
        "等待目标点位超时，"
        f"最后误差：{last_error:.4f} rad"
    )


def move_segment(
    client: AuboSdkClient,
    motion: object,
    target: Sequence[float],
    *,
    label: str,
    velocity: float,
    acceleration: float,
) -> None:
    current = client.current_joints()

    distance = max_joint_difference(
        current,
        target,
    )

    if distance <= 0.025:
        print(
            f"{label}：当前已在目标点附近，"
            f"最大误差{distance:.4f} rad，跳过移动。"
        )
        return

    timeout = max(
        5.0,
        distance / max(velocity, 0.05) * 4.0 + 3.0,
    )

    print(
        f"{label}："
        f"速度={velocity:.2f} rad/s，"
        f"加速度={acceleration:.2f} rad/s²"
    )

    started = time.monotonic()

    result = motion.moveJoint(
        list(target),
        acceleration,
        velocity,
        0,
        0,
    )

    if isinstance(result, int) and result != 0:
        raise AuboSdkError(
            f"{label}下发失败，返回码：{result}"
        )

    error = wait_pose(
        client,
        target,
        timeout=timeout,
    )

    elapsed = time.monotonic() - started

    print(
        f"{label}完成："
        f"用时{elapsed:.2f}秒，"
        f"最大误差{error:.4f} rad"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "高兴—兴奋动作演示："
            "中间位→举高→快速振臂上扬→举高位"
        )
    )

    parser.add_argument(
        "--config",
        default="config/robot.curiosity_fast.json",
    )

    parser.add_argument(
        "--approach-velocity",
        type=float,
        default=0.40,
        help="进入中间位和举高位的速度",
    )

    parser.add_argument(
        "--approach-acceleration",
        type=float,
        default=0.80,
    )

    parser.add_argument(
        "--shout-velocity",
        type=float,
        default=0.75,
        help="振臂上扬速度",
    )

    parser.add_argument(
        "--shout-acceleration",
        type=float,
        default=1.50,
    )

    parser.add_argument(
        "--return-velocity",
        type=float,
        default=0.68,
        help="峰值回到举高位的速度",
    )

    parser.add_argument(
        "--return-acceleration",
        type=float,
        default=1.30,
    )

    parser.add_argument(
        "--center-hold",
        type=float,
        default=0.20,
    )

    parser.add_argument(
        "--lift-hold",
        type=float,
        default=0.10,
    )

    parser.add_argument(
        "--peak-hold",
        type=float,
        default=0.12,
    )

    parser.add_argument(
        "--confirm-motion",
        default="",
    )

    args = parser.parse_args()

    if args.confirm_motion != CONFIRM:
        print("当前为预览模式，不会运动。")
        print(
            "顺序：curiosity_down"
            " → joy_lift_max"
            " → joy_shout_peak_max"
            " → joy_lift_max"
        )
        return 0

    if not 0.20 <= args.approach_velocity <= 0.80:
        print(
            "approach-velocity必须在0.20到0.80之间。",
            file=sys.stderr,
        )
        return 2

    if not 0.35 <= args.shout_velocity <= 1.60:
        print(
            "shout-velocity必须在0.35到1.60之间。",
            file=sys.stderr,
        )
        return 2

    if not 0.30 <= args.return_velocity <= 1.30:
        print(
            "return-velocity必须在0.30到1.30之间。",
            file=sys.stderr,
        )
        return 2

    if not 0.05 <= args.peak_hold <= 0.50:
        print(
            "peak-hold必须在0.05到0.50秒之间。",
            file=sys.stderr,
        )
        return 2

    try:
        data = json.loads(
            POSE_FILE.read_text(encoding="utf-8")
        )

        center = load_pose(
            data,
            "curiosity_down",
        )

        lift = load_pose(
            data,
            "joy_lift_max",
        )

        shout_peak = load_pose(
            data,
            "joy_shout_peak_max",
        )

        config = load_config(args.config)

        with AuboSdkClient(config) as client:
            robot = client._require_robot()
            motion = robot.getMotionControl()

            client.prepare_for_motion()

            print("进入结果反应中间位……")

            move_segment(
                client,
                motion,
                center,
                label="到达中间位",
                velocity=args.approach_velocity,
                acceleration=args.approach_acceleration,
            )

            time.sleep(args.center_hold)

            print("开始举高……")

            move_segment(
                client,
                motion,
                lift,
                label="举高",
                velocity=args.approach_velocity,
                acceleration=args.approach_acceleration,
            )

            time.sleep(args.lift_hold)

            print("快速振臂高呼！")

            move_segment(
                client,
                motion,
                shout_peak,
                label="振臂上扬",
                velocity=args.shout_velocity,
                acceleration=args.shout_acceleration,
            )

            print(
                f"兴奋峰值停留{args.peak_hold:.2f}秒……"
            )

            time.sleep(args.peak_hold)

            print("快速回到举高准备位……")

            move_segment(
                client,
                motion,
                lift,
                label="回到举高位",
                velocity=args.return_velocity,
                acceleration=args.return_acceleration,
            )

        print(
            "振臂高呼演示完成，"
            "当前停在joy_lift_max。"
        )

        return 0

    except (
        AuboSdkError,
        KeyError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"振臂高呼动作失败：{exc}",
            file=sys.stderr,
        )
        print(
            "机械臂仍在异常运动时，"
            "请立即按实体Stop按钮。",
            file=sys.stderr,
        )
        return 8


if __name__ == "__main__":
    raise SystemExit(main())
