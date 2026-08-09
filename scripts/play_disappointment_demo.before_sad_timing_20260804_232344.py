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

POSE_FILE = ROOT / "config" / "emotion_poses_new_es3.json"
CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def load_pose(data: dict, name: str) -> list[float]:
    try:
        joints = data["poses"][name]["joint_positions_rad"]
        return [float(joints[joint]) for joint in JOINT_NAMES]
    except KeyError as exc:
        raise KeyError(f"点位{name}不完整：{exc}") from exc


def max_difference(
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
    timeout: float = 15.0,
    tolerance: float = 0.018,
) -> None:
    deadline = time.monotonic() + timeout
    stable = 0

    while time.monotonic() < deadline:
        error = max_difference(
            client.current_joints(),
            target,
        )

        if error <= tolerance:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0

        time.sleep(0.025)

    raise AuboSdkError("等待目标点位超时。")


def move(
    client: AuboSdkClient,
    motion: object,
    target: Sequence[float],
    label: str,
    velocity: float,
    acceleration: float,
) -> None:
    distance = max_difference(
        client.current_joints(),
        target,
    )

    if distance <= 0.025:
        print(f"{label}：已经在目标点附近，跳过。")
        return

    print(
        f"{label}：速度={velocity:.2f} rad/s，"
        f"加速度={acceleration:.2f} rad/s²"
    )

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

    wait_pose(client, target)
    print(f"{label}完成。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default="config/robot.curiosity_fast.json",
    )
    parser.add_argument(
        "--confirm-motion",
        default="",
    )
    args = parser.parse_args()

    if args.confirm_motion != CONFIRM:
        print("当前为预览模式，不会运动。")
        print(
            "curiosity_down"
            " → disappointment_turn_away"
            " → disappointment_retreat_max"
            " → anticipation_look_down"
        )
        return 0

    try:
        data = json.loads(
            POSE_FILE.read_text(encoding="utf-8")
        )

        start = load_pose(data, "curiosity_down")
        turn = load_pose(
            data,
            "disappointment_turn_away",
        )
        retreat = load_pose(
            data,
            "disappointment_retreat_max",
        )
        lookdown = load_pose(
            data,
            "anticipation_look_down",
        )

        config = load_config(args.config)

        with AuboSdkClient(config) as client:
            robot = client._require_robot()
            motion = robot.getMotionControl()

            client.prepare_for_motion()

            move(
                client,
                motion,
                start,
                "进入curiosity_down",
                velocity=0.35,
                acceleration=0.60,
            )

            move(
                client,
                motion,
                turn,
                "低头转脸",
                velocity=0.45,
                acceleration=0.80,
            )

            time.sleep(0.15)

            move(
                client,
                motion,
                retreat,
                "回撤受挫",
                velocity=0.55,
                acceleration=0.90,
            )

            time.sleep(0.30)

            move(
                client,
                motion,
                lookdown,
                "回到anticipation_look_down",
                velocity=0.38,
                acceleration=0.65,
            )

        print(
            "失望动作测试完成，"
            "当前停在anticipation_look_down。"
        )
        return 0

    except (
        AuboSdkError,
        KeyError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"失望动作失败：{exc}", file=sys.stderr)
        print(
            "机械臂仍在异常运动时，"
            "请立即按实体Stop按钮。",
            file=sys.stderr,
        )
        return 8


if __name__ == "__main__":
    raise SystemExit(main())
