#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

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

# 实机绝对上限。LLM无法修改这些限制。
MAX_VELOCITY = 0.80
MAX_ACCELERATION = 1.50


def load_script(
    name: str,
    path: Path,
) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入脚本：{path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pose(
    data: dict[str, Any],
    name: str,
) -> list[float]:
    try:
        values = data["poses"][name]["joint_positions_rad"]
        return [
            float(values[joint])
            for joint in JOINT_NAMES
        ]
    except KeyError as exc:
        raise KeyError(
            f"点位{name}不完整：{exc}"
        ) from exc


def max_difference(
    a: Sequence[float],
    b: Sequence[float],
) -> float:
    return max(
        abs(x - y)
        for x, y in zip(a, b)
    )


def wait_pose(
    client: AuboSdkClient,
    target: Sequence[float],
    *,
    timeout: float,
    earliest_finish: float = 0.0,
    tolerance: float = 0.018,
    stable_samples: int = 3,
) -> float:
    deadline = time.monotonic() + timeout
    stable = 0
    last_error = float("inf")

    while time.monotonic() < deadline:
        last_error = max_difference(
            client.current_joints(),
            target,
        )

        if (
            time.monotonic() >= earliest_finish
            and last_error <= tolerance
        ):
            stable += 1
            if stable >= stable_samples:
                return last_error
        else:
            stable = 0

        time.sleep(0.025)

    raise AuboSdkError(
        "等待目标点位超时，"
        f"最后误差：{last_error:.4f} rad"
    )


def move_joint(
    client: AuboSdkClient,
    motion: object,
    target: Sequence[float],
    *,
    label: str,
    velocity: float,
    acceleration: float,
    stable_samples: int = 3,
) -> None:
    distance = max_difference(
        client.current_joints(),
        target,
    )

    if distance <= 0.025:
        print(
            f"{label}：当前已在目标点附近，"
            f"最大误差{distance:.4f} rad，跳过。"
        )
        return

    velocity = min(
        max(float(velocity), 0.05),
        MAX_VELOCITY,
    )
    acceleration = min(
        max(float(acceleration), 0.08),
        MAX_ACCELERATION,
    )

    timeout = max(
        5.0,
        distance / velocity * 4.0 + 3.0,
    )

    print(
        f"{label}："
        f"速度={velocity:.2f} rad/s，"
        f"加速度={acceleration:.2f} rad/s²"
    )

    result = motion.moveJoint(
        list(target),
        acceleration,
        velocity,
        0,
        0,
    )

    if result is False:
        raise AuboSdkError(
            f"{label}下发失败，接口返回False"
        )

    if (
        isinstance(result, int)
        and not isinstance(result, bool)
        and result != 0
    ):
        raise AuboSdkError(
            f"{label}下发失败，返回码：{result}"
        )

    error = wait_pose(
        client,
        target,
        timeout=timeout,
        stable_samples=stable_samples,
    )

    print(
        f"{label}完成，"
        f"最大误差{error:.4f} rad。"
    )


class FixedMotionExecutor:
    def __init__(
        self,
        client: AuboSdkClient,
        motion: object,
        poses: dict[str, list[float]],
        breathing: ModuleType,
        breathing_helper: ModuleType,
    ) -> None:
        self.client = client
        self.motion = motion
        self.poses = poses
        self.breathing = breathing
        self.breathing_helper = breathing_helper

    def execute(
        self,
        result: str,
        level: int,
        speed_scale: float,
        repeat_count: int,
    ) -> None:
        print(
            "\n开始固定动作映射："
            f"result={result}，"
            f"level={level}，"
            f"repeat={repeat_count}，"
            f"speed_scale={speed_scale:.2f}"
        )

        if result == "correct":
            self.execute_joy(
                speed_scale=speed_scale,
                repeat_count=repeat_count,
            )
        elif result == "incorrect":
            self.execute_disappointment(
                speed_scale=speed_scale,
                repeat_count=repeat_count,
            )
        else:
            raise ValueError(
                f"未知结果类型：{result}"
            )

    def execute_joy(
        self,
        *,
        speed_scale: float,
        repeat_count: int,
    ) -> None:
        center = self.poses["curiosity_down"]
        lift = self.poses["joy_lift_max"]
        peak = self.poses["joy_shout_peak_max"]
        lookdown = self.poses["anticipation_look_down"]

        move_joint(
            self.client,
            self.motion,
            center,
            label="进入curiosity_down",
            velocity=0.40 * speed_scale,
            acceleration=0.80 * speed_scale,
        )
        time.sleep(0.20 / speed_scale)

        move_joint(
            self.client,
            self.motion,
            lift,
            label="举高到joy_lift_max",
            velocity=0.40 * speed_scale,
            acceleration=0.80 * speed_scale,
        )
        time.sleep(0.10 / speed_scale)

        for index in range(repeat_count):
            print(
                f"执行振臂强化"
                f"{index + 1}/{repeat_count}……"
            )

            move_joint(
                self.client,
                self.motion,
                peak,
                label="振臂上扬",
                velocity=0.75 * speed_scale,
                acceleration=1.50 * speed_scale,
                stable_samples=1,
            )

            time.sleep(0.12 / speed_scale)

            move_joint(
                self.client,
                self.motion,
                lift,
                label="回到joy_lift_max",
                velocity=0.68 * speed_scale,
                acceleration=1.30 * speed_scale,
                stable_samples=1,
            )

        move_joint(
            self.client,
            self.motion,
            lookdown,
            label="回到anticipation_look_down",
            velocity=0.68 * speed_scale,
            acceleration=1.30 * speed_scale,
            stable_samples=1,
        )

        print(
            "高兴动作完成，当前位于"
            "anticipation_look_down。"
        )

    def execute_disappointment(
        self,
        *,
        speed_scale: float,
        repeat_count: int,
    ) -> None:
        start = self.poses["curiosity_down"]
        turn = self.poses["disappointment_turn_away"]
        retreat = self.poses["disappointment_retreat_max"]
        lookdown = self.poses["anticipation_look_down"]

        move_joint(
            self.client,
            self.motion,
            start,
            label="进入curiosity_down",
            velocity=0.30 * speed_scale,
            acceleration=0.45 * speed_scale,
        )

        move_joint(
            self.client,
            self.motion,
            turn,
            label="低头转脸",
            velocity=0.28 * speed_scale,
            acceleration=0.38 * speed_scale,
        )

        print("低头转脸后停留……")
        time.sleep(0.45 / speed_scale)

        move_joint(
            self.client,
            self.motion,
            retreat,
            label="回撤受挫",
            velocity=0.24 * speed_scale,
            acceleration=0.32 * speed_scale,
        )

        print("回撤受挫后停稳……")
        time.sleep(0.60 / speed_scale)

        if repeat_count <= 0:
            print(
                "强度等级为0，不执行呼吸强化，"
                "缓慢回到期待位……"
            )

            move_joint(
                self.client,
                self.motion,
                lookdown,
                label="回到anticipation_look_down",
                velocity=0.22 * speed_scale,
                acceleration=0.30 * speed_scale,
            )
            return

        amplitude_deg = 10.0
        cycle_seconds = 4.0 / speed_scale

        one_cycle = (
            self.breathing.build_breathing_trajectory(
                retreat,
                amplitude_deg=amplitude_deg,
                cycle_seconds=cycle_seconds,
            )
        )

        trajectory: list[list[float]] = []

        for cycle_index in range(repeat_count):
            if cycle_index < repeat_count - 1:
                trajectory.extend(
                    list(point)
                    for point in one_cycle[:-1]
                )
            else:
                trajectory.extend(
                    list(point)
                    for point in one_cycle
                )

        post_hold_seconds = 0.60 / speed_scale
        post_hold_steps = max(
            2,
            int(
                round(
                    post_hold_seconds
                    / self.breathing.PATH_SAMPLE_TIME
                )
            ),
        )

        trajectory.extend(
            list(retreat)
            for _ in range(post_hold_steps)
        )

        maximum_delta = max_difference(
            retreat,
            lookdown,
        )

        transition_velocity = 0.22 * speed_scale

        transition_seconds = max(
            1.20,
            1.90
            * maximum_delta
            / transition_velocity,
        )

        transition_steps = max(
            100,
            int(
                round(
                    transition_seconds
                    / self.breathing.PATH_SAMPLE_TIME
                )
            ),
        )

        for index in range(1, transition_steps + 1):
            progress = index / transition_steps

            smooth = (
                10.0 * progress ** 3
                - 15.0 * progress ** 4
                + 6.0 * progress ** 5
            )

            point = [
                start_value
                + (end_value - start_value) * smooth
                for start_value, end_value
                in zip(retreat, lookdown)
            ]

            trajectory.append(point)

        trajectory[-1] = list(lookdown)

        combined_duration = (
            (len(trajectory) - 1)
            * self.breathing.PATH_SAMPLE_TIME
        )

        signature = {
            "cycles": repeat_count,
            "amplitude": amplitude_deg,
            "cycle_seconds": round(cycle_seconds, 4),
            "post_hold": round(post_hold_seconds, 4),
            "transition_seconds": round(
                transition_seconds,
                4,
            ),
            "lookdown": [
                round(value, 6)
                for value in lookdown
            ],
        }

        digest = hashlib.sha1(
            json.dumps(
                signature,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:10]

        buffer_name = (
            self.breathing.make_buffer_name(
                retreat,
                amplitude_deg=amplitude_deg,
                cycle_seconds=cycle_seconds,
            )
            + f"_x{repeat_count}"
            + f"_to_lookdown_{digest}"
        )

        print(
            "准备失望连续轨迹："
            f"呼吸{repeat_count}次"
            " → 停稳"
            " → anticipation_look_down"
        )

        self.breathing.prepare_breathing_buffer(
            self.motion,
            self.breathing_helper,
            buffer_name,
            trajectory,
            rebuild=False,
        )

        started = time.monotonic()

        result = self.motion.movePathBuffer(
            buffer_name
        )

        if result is False:
            raise AuboSdkError(
                "失望连续轨迹执行失败，"
                "接口返回False"
            )

        if (
            isinstance(result, int)
            and not isinstance(result, bool)
            and result != 0
        ):
            raise AuboSdkError(
                "失望连续轨迹执行失败，"
                f"返回码：{result}"
            )

        wait_pose(
            self.client,
            lookdown,
            timeout=combined_duration + 3.0,
            earliest_finish=(
                started
                + combined_duration
                - 0.20
            ),
            stable_samples=4,
        )

        print(
            "失望动作完成，当前位于"
            "anticipation_look_down。"
        )


def fixed_decision(
    result: str,
    level: int,
) -> dict[str, Any]:
    intensity_values = {
        0: 0.12,
        1: 0.37,
        2: 0.62,
        3: 0.87,
    }

    positive = result == "correct"
    intensity = intensity_values[level]

    return {
        "emotion_state": {
            "emotion_name": (
                "joy"
                if positive
                else "disappointment"
            ),
            "valence": (
                intensity
                if positive
                else -intensity
            ),
            "arousal": intensity,
            "intensity": intensity,
            "persistence": 0.00,
            "unexpectedness": 0.00,
        },
        "intensity_level": level,
        "brief_reason": (
            f"固定测试等级{level}"
        ),
    }


def build_parser(
    preview: ModuleType,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DeepSeek判断情感强度，"
            "本地固定映射AUBO动作"
        )
    )

    parser.add_argument(
        "--config",
        default="config/robot.curiosity_fast.json",
    )

    parser.add_argument(
        "--model",
        default=os.environ.get(
            "DEEPSEEK_MODEL",
            "deepseek-v4-flash",
        ),
    )

    parser.add_argument(
        "--prompt-file",
        default=str(preview.DEFAULT_PROMPT),
    )

    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
    )

    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--fixed-level",
        type=int,
        choices=(0, 1, 2, 3),
        default=None,
        help=(
            "不调用LLM，固定使用指定强度等级"
        ),
    )

    parser.add_argument(
        "--confirm-motion",
        default="",
    )

    return parser


def main() -> int:
    preview = load_script(
        "llm_primitive_preview_inline",
        ROOT / "scripts" / "llm_primitive_preview.py",
    )

    disappointment = load_script(
        "play_disappointment_demo_inline",
        ROOT / "scripts" / "play_disappointment_demo.py",
    )

    args = build_parser(preview).parse_args()

    if args.max_rounds < 0:
        print(
            "max-rounds不能小于0。",
            file=sys.stderr,
        )
        return 2

    if args.max_retries < 0:
        print(
            "max-retries不能小于0。",
            file=sys.stderr,
        )
        return 2

    if args.confirm_motion != CONFIRM:
        print(
            "当前为安全预览模式，"
            "不会连接或移动机械臂。"
        )

        if args.fixed_level is None:
            print(
                "运行方式：LLM判断强度，"
                "本地执行固定动作。"
            )
        else:
            print(
                f"运行方式：固定等级"
                f"{args.fixed_level}测试，"
                "不会调用LLM。"
            )

        print(
            "正式连接需添加："
        )
        print(
            f"--confirm-motion {CONFIRM}"
        )
        return 0

    try:
        system_prompt = Path(
            args.prompt_file
        ).read_text(encoding="utf-8")

        pose_data = json.loads(
            POSE_FILE.read_text(encoding="utf-8")
        )

        poses = {
            name: load_pose(pose_data, name)
            for name in (
                "curiosity_down",
                "joy_lift_max",
                "joy_shout_peak_max",
                "disappointment_turn_away",
                "disappointment_retreat_max",
                "anticipation_look_down",
            )
        }

        breathing = (
            disappointment.load_breathing_module()
        )
        breathing_helper = (
            breathing.load_anticipation_module()
        )
        config = load_config(args.config)

    except (
        OSError,
        KeyError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"初始化失败：{exc}",
            file=sys.stderr,
        )
        return 2

    print(
        "模式：LLM情感判断 + 本地固定动作映射"
        if args.fixed_level is None
        else (
            "模式：固定强度实机测试，"
            f"level={args.fixed_level}"
        )
    )
    print(
        "每轮输入：1=猜对，0=猜错，e=结束"
    )
    print(
        "没有输入MOVE时，本轮绝不运动。"
    )

    history: list[str] = []
    previous_emotion_state: dict[str, Any] = dict(
        preview.NEUTRAL_EMOTION_STATE
    )
    previous_decision: dict[str, Any] | None = None

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")

    log_path = (
        ROOT
        / "logs"
        / f"llm_primitive_robot_{timestamp}.jsonl"
    )

    try:
        with AuboSdkClient(config) as client:
            robot = client._require_robot()
            motion = robot.getMotionControl()

            client.prepare_for_motion()

            executor = FixedMotionExecutor(
                client,
                motion,
                poses,
                breathing,
                breathing_helper,
            )

            print(
                "机械臂已连接并准备完成，"
                "当前尚未下发运动。"
            )

            while True:
                if (
                    args.max_rounds
                    and len(history) >= args.max_rounds
                ):
                    print(
                        f"已达到{args.max_rounds}轮。"
                    )
                    break

                text = input(
                    f"\n第{len(history) + 1}轮"
                    "结果 [1/0/e]："
                ).strip()

                if text.lower() == "e":
                    break

                current_result = (
                    preview.normalize_result(text)
                )

                if current_result is None:
                    print(
                        "输入无效，请输入1、0或e。"
                    )
                    continue

                candidate_history = (
                    history + [current_result]
                )

                round_input = {
                    "current_result": current_result,
                    "result_history": candidate_history,
                    "transition_type": (
                        preview.expected_transition_type(
                            candidate_history
                        )
                    ),
                    "previous_emotion_state": (
                        previous_emotion_state
                    ),
                    "previous_decision": (
                        previous_decision
                    ),
                }

                if args.fixed_level is not None:
                    decision_dict = fixed_decision(
                        current_result,
                        args.fixed_level,
                    )
                else:
                    print(
                        "正在调用LLM判断情感状态……",
                        flush=True,
                    )

                    try:
                        decision = preview.call_openai(
                            model=args.model,
                            system_prompt=system_prompt,
                            round_input=round_input,
                            max_retries=args.max_retries,
                        )
                    except Exception as exc:
                        print(
                            f"本轮LLM调用或校验失败："
                            f"{exc}",
                            file=sys.stderr,
                        )
                        print(
                            "本轮不会运动，"
                            "也不会写入结果历史。"
                        )
                        continue

                    decision_dict = (
                        decision.model_dump(
                            mode="json"
                        )
                    )

                level = int(
                    decision_dict["intensity_level"]
                )

                parameters = (
                    preview.LEVEL_PARAMETERS[level]
                )

                repeat_count = int(
                    parameters["repeat_count"]
                )
                speed_scale = float(
                    parameters["speed_scale"]
                )

                motion_preview = {
                    "result": current_result,
                    "transition_type": (
                        preview.expected_transition_type(
                            candidate_history
                        )
                    ),
                    **decision_dict,
                    "motion": (
                        preview.MOTION_BY_RESULT[
                            current_result
                        ]
                    ),
                    "start_pose": preview.START_POSE,
                    "end_pose": preview.END_POSE,
                    "speed_scale": speed_scale,
                    "repeated_primitive": (
                        preview
                        .REPEATED_PRIMITIVE_BY_RESULT[
                            current_result
                        ]
                    ),
                    "repeat_count": repeat_count,
                }

                print("\n待执行内容：")
                print(
                    json.dumps(
                        motion_preview,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

                confirmation = input(
                    "确认人员和障碍物已离开后，"
                    "输入MOVE执行；"
                    "其他输入取消："
                ).strip()

                if confirmation != "MOVE":
                    print(
                        "已取消。机械臂未运动，"
                        "本轮不写入历史。"
                    )
                    continue

                started_at = datetime.now(
                    timezone.utc
                ).isoformat()

                try:
                    executor.execute(
                        result=current_result,
                        level=level,
                        speed_scale=speed_scale,
                        repeat_count=repeat_count,
                    )
                except Exception:
                    preview.append_log(
                        log_path,
                        {
                            "timestamp_utc": started_at,
                            "input": round_input,
                            "decision": decision_dict,
                            "motion_preview": (
                                motion_preview
                            ),
                            "robot_motion": True,
                            "completed": False,
                        },
                    )
                    raise

                preview.append_log(
                    log_path,
                    {
                        "timestamp_utc": started_at,
                        "input": round_input,
                        "decision": decision_dict,
                        "motion_preview": motion_preview,
                        "robot_motion": True,
                        "completed": True,
                    },
                )

                history = candidate_history
                previous_emotion_state = dict(
                    decision_dict["emotion_state"]
                )
                previous_decision = decision_dict

                print(
                    "本轮完整动作执行完成。"
                )

        print(
            f"\n结束，共完成{len(history)}轮。"
        )

        if history:
            print(f"记录文件：{log_path}")

        return 0

    except (
        AuboSdkError,
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        KeyboardInterrupt,
    ) as exc:
        print(
            f"实机程序已经停止：{exc}",
            file=sys.stderr,
        )
        print(
            "如果机械臂仍在异常运动，"
            "请立即按实体Stop按钮。",
            file=sys.stderr,
        )
        return 8


if __name__ == "__main__":
    raise SystemExit(main())
