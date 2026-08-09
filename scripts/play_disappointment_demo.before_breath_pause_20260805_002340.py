#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import math
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
BREATHING_SCRIPT = (
    ROOT / "scripts" / "play_anticipation_breathing.py"
)


def load_breathing_module():
    spec = importlib.util.spec_from_file_location(
        "play_anticipation_breathing_inline",
        BREATHING_SCRIPT,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "无法载入play_anticipation_breathing.py"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def execute_breath_cycle(
    client: AuboSdkClient,
    motion: object,
    buffer_name: str,
    center_pose: Sequence[float],
    *,
    amplitude_deg: float,
    cycle_seconds: float,
    cycle_index: int,
) -> None:
    wrist3_index = JOINT_NAMES.index(
        "wrist3_joint"
    )

    center_wrist3 = center_pose[wrist3_index]

    print(
        f"受挫呼吸第{cycle_index}次开始……",
        flush=True,
    )

    result = motion.movePathBuffer(buffer_name)

    if result is False:
        raise AuboSdkError(
            "执行受挫呼吸轨迹失败，接口返回False"
        )

    if (
        isinstance(result, int)
        and not isinstance(result, bool)
        and result != 0
    ):
        raise AuboSdkError(
            f"执行受挫呼吸轨迹失败，返回码：{result}"
        )

    threshold_deg = max(
        2.0,
        amplitude_deg * 0.35,
    )

    deadline = (
        time.monotonic()
        + cycle_seconds
        + 2.0
    )

    seen_positive = False
    seen_negative = False
    stable_center = 0

    minimum_offset_deg = 0.0
    maximum_offset_deg = 0.0

    while time.monotonic() < deadline:
        current = client.current_joints()

        offset_deg = math.degrees(
            current[wrist3_index]
            - center_wrist3
        )

        minimum_offset_deg = min(
            minimum_offset_deg,
            offset_deg,
        )

        maximum_offset_deg = max(
            maximum_offset_deg,
            offset_deg,
        )

        if offset_deg >= threshold_deg:
            seen_positive = True

        if offset_deg <= -threshold_deg:
            seen_negative = True

        center_error = max_difference(
            current,
            center_pose,
        )

        if (
            seen_positive
            and seen_negative
            and center_error <= 0.025
        ):
            stable_center += 1

            if stable_center >= 3:
                print(
                    f"受挫呼吸第{cycle_index}次完成；"
                    f"实际范围："
                    f"{minimum_offset_deg:.2f}°"
                    f" 至 "
                    f"{maximum_offset_deg:.2f}°",
                    flush=True,
                )
                return
        else:
            stable_center = 0

        time.sleep(0.02)

    raise AuboSdkError(
        f"受挫呼吸第{cycle_index}次等待超时。"
        f"实际范围：{minimum_offset_deg:.2f}°"
        f" 至 {maximum_offset_deg:.2f}°"
    )


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

    parser.add_argument(
        "--breath-amplitude-deg",
        type=float,
        default=10.0,
    )

    parser.add_argument(
        "--breath-cycle-seconds",
        type=float,
        default=4.0,
    )

    parser.add_argument(
        "--breath-cycles",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--return-only",
        action="store_true",
        help=(
            "仅从当前位置缓慢返回"
            "anticipation_look_down"
        ),
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

        breathing = load_breathing_module()
        helper = breathing.load_anticipation_module()

        trajectory = breathing.build_breathing_trajectory(
            retreat,
            amplitude_deg=args.breath_amplitude_deg,
            cycle_seconds=args.breath_cycle_seconds,
        )

        buffer_name = breathing.make_buffer_name(
            retreat,
            amplitude_deg=args.breath_amplitude_deg,
            cycle_seconds=args.breath_cycle_seconds,
        )

        config = load_config(args.config)

        with AuboSdkClient(config) as client:
            robot = client._require_robot()
            motion = robot.getMotionControl()

            # 提前检查缓存，避免回撤完成后才等待。
            breathing.prepare_breathing_buffer(
                motion,
                helper,
                buffer_name,
                trajectory,
                rebuild=False,
            )

            client.prepare_for_motion()

            if args.return_only:
                move(
                    client,
                    motion,
                    lookdown,
                    "回到anticipation_look_down",
                    velocity=0.22,
                    acceleration=0.30,
                )

                print(
                    "已缓慢回到"
                    "anticipation_look_down。"
                )
                return 0

            move(
                client,
                motion,
                start,
                "进入curiosity_down",
                velocity=0.30,
                acceleration=0.45,
            )

            move(
                client,
                motion,
                turn,
                "低头转脸",
                velocity=0.28,
                acceleration=0.38,
            )

            print("低头转脸后停留0.45秒……")
            time.sleep(0.45)

            move(
                client,
                motion,
                retreat,
                "回撤受挫",
                velocity=0.24,
                acceleration=0.32,
            )

            for cycle_index in range(
                1,
                args.breath_cycles + 1,
            ):
                execute_breath_cycle(
                    client,
                    motion,
                    buffer_name,
                    retreat,
                    amplitude_deg=(
                        args.breath_amplitude_deg
                    ),
                    cycle_seconds=(
                        args.breath_cycle_seconds
                    ),
                    cycle_index=cycle_index,
                )

            print(
                "受挫呼吸完成，"
                "立即衔接anticipation_look_down……"
            )

            move(
                client,
                motion,
                lookdown,
                "回到anticipation_look_down",
                velocity=0.22,
                acceleration=0.30,
            )

        print(
            "失望连续动作完成，"
            "当前停在anticipation_look_down。"
        )
        return 0

    except (
        AuboSdkError,
        KeyError,
        ValueError,
        OSError,
        RuntimeError,
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
