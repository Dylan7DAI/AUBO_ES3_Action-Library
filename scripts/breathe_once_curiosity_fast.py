#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from pathlib import Path


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

POSE_FILE = (
    ROOT
    / "config"
    / "emotion_poses_new_es3.json"
)

HELPER_SCRIPT = (
    ROOT
    / "scripts"
    / "play_anticipation_continuous.py"
)

PATH_SAMPLE_TIME = 0.01
PATH_BUFFER_CUBIC_SPLINE = 2


def load_helper():
    spec = importlib.util.spec_from_file_location(
        "anticipation_helper",
        HELPER_SCRIPT,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"无法载入：{HELPER_SCRIPT}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def make_buffer_name(
    pose_name: str,
    pose: list[float],
    amplitude_deg: float,
    cycle_seconds: float,
) -> str:
    signature = {
        "version": 5,
        "pose_name": pose_name,
        "pose": [
            round(float(value), 6)
            for value in pose
        ],
        "amplitude_deg": round(
            amplitude_deg,
            3,
        ),
        "cycle_seconds": round(
            cycle_seconds,
            3,
        ),
    }

    encoded = json.dumps(
        signature,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha1(
        encoded
    ).hexdigest()[:10]

    return f"pose_breathe_once_{digest}"


def build_trajectory(
    center: list[float],
    *,
    amplitude_deg: float,
    cycle_seconds: float,
) -> list[list[float]]:
    """生成中心→负向→正向→中心的双向平滑轨迹。"""
    wrist3_index = JOINT_NAMES.index(
        "wrist3_joint"
    )

    point_count = max(
        100,
        int(
            round(
                cycle_seconds
                / PATH_SAMPLE_TIME
            )
        )
        + 1,
    )

    amplitude_rad = math.radians(
        amplitude_deg
    )

    def smootherstep(value: float) -> float:
        """端点速度和加速度均为零的五次平滑曲线。"""
        return (
            value
            * value
            * value
            * (
                value
                * (
                    value * 6.0
                    - 15.0
                )
                + 10.0
            )
        )

    trajectory: list[list[float]] = []

    for index in range(point_count):
        progress = index / (
            point_count - 1
        )

        if progress <= 0.25:
            # 中心 → 负向15°
            local = progress / 0.25
            offset = (
                -amplitude_rad
                * smootherstep(local)
            )

        elif progress <= 0.75:
            # 负向15° → 正向15°
            local = (
                progress - 0.25
            ) / 0.50

            offset = (
                -amplitude_rad
                + 2.0
                * amplitude_rad
                * smootherstep(local)
            )

        else:
            # 正向15° → 中心
            local = (
                progress - 0.75
            ) / 0.25

            offset = (
                amplitude_rad
                * (
                    1.0
                    - smootherstep(local)
                )
            )

        point = list(center)

        point[wrist3_index] = (
            center[wrist3_index]
            + offset
        )

        trajectory.append(point)

    trajectory[0] = list(center)
    trajectory[-1] = list(center)

    return trajectory


def wait_until_center(

    client: AuboSdkClient,
    target: list[float],
    *,
    timeout: float,
) -> float:
    wrist3_index = JOINT_NAMES.index(
        "wrist3_joint"
    )

    deadline = (
        time.monotonic() + timeout
    )

    last_error = float("inf")

    while time.monotonic() < deadline:
        current = client.current_joints()

        last_error = abs(
            current[wrist3_index]
            - target[wrist3_index]
        )

        if last_error <= math.radians(2.0):
            return last_error

        time.sleep(0.05)

    raise AuboSdkError(
        "单次呼吸结束后腕部未回中。"
        f"误差：{last_error:.4f} rad"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "在指定示教姿态上执行一次"
            "腕部呼吸动作。"
        )
    )

    parser.add_argument(
        "pose_name",
    )

    parser.add_argument(
        "--amplitude-deg",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--cycle-seconds",
        type=float,
        default=4.0,
    )

    parser.add_argument(
        "--config",
        default=None,
    )

    parser.add_argument(
        "--confirm-motion",
        default="",
    )

    args = parser.parse_args()

    if args.confirm_motion != CONFIRM:
        print("当前为预览模式，不会运动。")
        return 0

    if not 2.0 <= args.amplitude_deg <= 20.0:
        print(
            "呼吸左右各自幅度必须在2°到20°之间。",
            file=sys.stderr,
        )
        return 1

    if not 1.0 <= args.cycle_seconds <= 8.0:
        print(
            "呼吸周期必须在1.0秒到8秒之间。",
            file=sys.stderr,
        )
        return 1

    helper = load_helper()

    data = json.loads(
        POSE_FILE.read_text(
            encoding="utf-8"
        )
    )

    center = helper.load_pose(
        data,
        args.pose_name,
    )

    trajectory = build_trajectory(
        center,
        amplitude_deg=args.amplitude_deg,
        cycle_seconds=args.cycle_seconds,
    )

    buffer_name = make_buffer_name(
        args.pose_name,
        center,
        args.amplitude_deg,
        args.cycle_seconds,
    )

    config = load_config(args.config)

    try:
        with AuboSdkClient(config) as client:
            current = client.current_joints()

            start_error = (
                helper.max_joint_difference(
                    current,
                    center,
                )
            )

            if start_error > 0.08:
                raise AuboSdkError(
                    "机械臂当前不在目标姿态附近。"
                    f"最大误差："
                    f"{start_error:.4f} rad"
                )

            robot = client._require_robot()
            motion = (
                robot.getMotionControl()
            )

            helper.prepare_buffer(
                motion,
                buffer_name,
                trajectory,
                rebuild=False,
            )

            client.prepare_for_motion()

            print()
            print(
                f"在 {args.pose_name} "
                "执行一次呼吸……"
            )

            result = motion.movePathBuffer(
                buffer_name
            )

            helper.check_result(
                result,
                "执行单次呼吸",
            )

            wrist3_index = (
                JOINT_NAMES.index(
                    "wrist3_joint"
                )
            )

            minimum_deg = 0.0
            maximum_deg = 0.0

            deadline = (
                time.monotonic()
                + args.cycle_seconds
                + 0.10
            )

            while time.monotonic() < deadline:
                current = client.current_joints()

                offset_deg = math.degrees(
                    current[wrist3_index]
                    - center[wrist3_index]
                )

                minimum_deg = min(
                    minimum_deg,
                    offset_deg,
                )

                maximum_deg = max(
                    maximum_deg,
                    offset_deg,
                )

                time.sleep(0.05)

            final_error = wait_until_center(
                client,
                center,
                timeout=2.0,
            )

            print(
                "单次呼吸完成并回中。"
            )

            print(
                "实际范围："
                f"{minimum_deg:.2f}° 至 "
                f"{maximum_deg:.2f}°"
            )

            print(
                f"回中误差："
                f"{final_error:.4f} rad"
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
            f"单次呼吸失败：{exc}",
            file=sys.stderr,
        )
        return 8


if __name__ == "__main__":
    raise SystemExit(main())
