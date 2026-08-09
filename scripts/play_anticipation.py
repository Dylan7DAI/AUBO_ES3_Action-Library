#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Sequence


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.sdk_client import (
    AuboSdkClient,
    AuboSdkError,
    JOINT_NAMES,
)


POSE_FILE = (
    ROOT
    / "config"
    / "emotion_poses_new_es3.json"
)

IDLE_HEAD_SCRIPT = (
    ROOT
    / "scripts"
    / "play_idle_head.py"
)

ANTICIPATION_CONFIG = (
    ROOT
    / "config"
    / "robot.anticipation.json"
)

CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def load_pose(
    data: dict,
    pose_name: str,
) -> List[float]:
    """按固定关节顺序读取示教点位。"""
    try:
        joints = (
            data["poses"][pose_name]
            ["joint_positions_rad"]
        )

        return [
            float(joints[joint_name])
            for joint_name in JOINT_NAMES
        ]

    except KeyError as exc:
        raise KeyError(
            f"点位 {pose_name} 不完整，"
            f"缺少字段：{exc}"
        ) from exc


def max_joint_difference(
    pose_a: Sequence[float],
    pose_b: Sequence[float],
) -> float:
    """计算两个姿态最大的单关节角度差。"""
    return max(
        abs(value_a - value_b)
        for value_a, value_b
        in zip(pose_a, pose_b)
    )


def check_result(
    result: object,
    action_name: str,
) -> None:
    """检查AUBO接口返回值。"""
    if result is False:
        raise AuboSdkError(
            f"{action_name}失败，接口返回False"
        )

    if (
        isinstance(result, int)
        and not isinstance(result, bool)
        and result != 0
    ):
        raise AuboSdkError(
            f"{action_name}失败，返回码：{result}"
        )


def scale_tag(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def make_buffer_name(
    first_scale: float,
    second_scale: float,
    down_scale: float,
) -> str:
    """必须与play_idle_head.py中的名称一致。"""
    return (
        "anti_"
        f"f{scale_tag(first_scale)}_"
        f"s{scale_tag(second_scale)}_"
        f"d{scale_tag(down_scale)}_"
        "v6"
    )


def prepare_head_buffer(
    first_scale: float,
    second_scale: float,
    down_scale: float,
    *,
    rebuild_cache: bool,
) -> None:
    """在展开前准备完整期待轨迹缓存。"""
    command = [
        sys.executable,
        str(IDLE_HEAD_SCRIPT),
        "--first-scale",
        str(first_scale),
        "--second-scale",
        str(second_scale),
        "--down-scale",
        str(down_scale),
        "--prepare-only",
    ]

    if rebuild_cache:
        command.append(
            "--rebuild-cache"
        )

    print("=" * 52)
    print("准备阶段：提前准备期待轨迹")
    print("=" * 52)

    result = subprocess.run(
        command,
        cwd=str(ROOT),
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "期待轨迹准备失败，"
            f"返回码：{result.returncode}"
        )


def wait_until_reached_and_stopped(
    client: AuboSdkClient,
    robot: object,
    target: List[float],
    *,
    timeout: float = 40.0,
    position_tolerance: float = 0.020,
    speed_tolerance: float = 0.006,
    stable_samples: int = 3,
    sample_interval: float = 0.01,
) -> tuple[float, float]:
    """
    等待展开到位并停止。

    连续3次满足条件后立即开始期待轨迹，
    软件确认时间约为0.03秒。
    """
    deadline = time.monotonic() + timeout

    stable_count = 0
    last_error = float("inf")
    last_speed = float("inf")
    last_print_time = 0.0

    state = robot.getRobotState()

    previous_positions: List[float] | None = None
    previous_time: float | None = None

    while time.monotonic() < deadline:
        now = time.monotonic()

        current_positions = (
            client.current_joints()
        )

        last_error = max_joint_difference(
            current_positions,
            target,
        )

        try:
            joint_speeds = [
                float(value)
                for value
                in state.getJointSpeeds()
            ]

            if len(joint_speeds) != len(JOINT_NAMES):
                raise ValueError(
                    "关节速度数量异常"
                )

            last_speed = max(
                abs(value)
                for value in joint_speeds
            )

        except Exception:
            if (
                previous_positions is not None
                and previous_time is not None
            ):
                delta_time = max(
                    now - previous_time,
                    1e-6,
                )

                estimated_speeds = [
                    abs(current - previous)
                    / delta_time
                    for current, previous
                    in zip(
                        current_positions,
                        previous_positions,
                    )
                ]

                last_speed = max(
                    estimated_speeds
                )

            previous_positions = list(
                current_positions
            )

            previous_time = now

        if (
            last_error <= position_tolerance
            and last_speed <= speed_tolerance
        ):
            stable_count += 1

            if stable_count >= stable_samples:
                return (
                    last_error,
                    last_speed,
                )
        else:
            stable_count = 0

        if now - last_print_time >= 1.0:
            print(
                "正在展开，"
                f"位置误差={last_error:.4f} rad，"
                f"最大关节速度={last_speed:.4f} rad/s"
            )

            last_print_time = now

        time.sleep(
            sample_interval
        )

    raise AuboSdkError(
        "等待展开完成超时。"
        f"最后位置误差={last_error:.4f} rad，"
        f"最大关节速度={last_speed:.4f} rad/s。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "完整期待动作："
            "展开、观察，最后低头"
        )
    )

    parser.add_argument(
        "--first-scale",
        type=float,
        default=4.0,
        help="第一次左右观察时间比例，默认4.0",
    )

    parser.add_argument(
        "--second-scale",
        type=float,
        default=7.0,
        help="第二次左看时间比例，默认7.0",
    )

    parser.add_argument(
        "--down-scale",
        type=float,
        default=3.5,
        help=(
            "最后低头动作时间比例，"
            "默认3.5"
        ),
    )

    parser.add_argument(
        "--face-config",
        default=None,
        help="展开专用配置文件",
    )

    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="强制重新计算期待轨迹缓存",
    )

    parser.add_argument(
        "--confirm-motion",
        default="",
        help="真实运动确认文本",
    )

    args = parser.parse_args()

    if not 1.0 <= args.first_scale <= 8.0:
        print(
            "first-scale必须在1.0到8.0之间。",
            file=sys.stderr,
        )
        return 1

    if not 1.0 <= args.second_scale <= 8.0:
        print(
            "second-scale必须在1.0到8.0之间。",
            file=sys.stderr,
        )
        return 2

    if not 1.0 <= args.down_scale <= 8.0:
        print(
            "down-scale必须在1.0到8.0之间。",
            file=sys.stderr,
        )
        return 3

    if args.face_config:
        config_path = args.face_config

    elif ANTICIPATION_CONFIG.exists():
        config_path = str(
            ANTICIPATION_CONFIG
        )

    else:
        config_path = None

    config = load_config(
        config_path
    )

    face_velocity = (
        config.safety.max_velocity_rad_s
    )

    face_acceleration = (
        config.safety.max_acceleration_rad_s2
    )

    try:
        data = json.loads(
            POSE_FILE.read_text(
                encoding="utf-8"
            )
        )

        pre_start = load_pose(
            data,
            "pre_start_rest",
        )

        center = load_pose(
            data,
            "anticipation_face_user",
        )

        look_down = load_pose(
            data,
            "anticipation_look_down",
        )

    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"读取点位失败：{exc}",
            file=sys.stderr,
        )
        return 4

    buffer_name = make_buffer_name(
        args.first_scale,
        args.second_scale,
        args.down_scale,
    )

    if args.confirm_motion != CONFIRM:
        print(
            "当前为预览模式，"
            "机械臂不会运动。"
        )

        print()
        print("完整动作：")
        print("1. 展开并朝向用户")
        print("2. 左看")
        print("3. 右看")
        print("4. 再次左看")
        print("5. 回正后低头")
        print("6. 保持低头，衔接桌面观察")

        print()
        print(
            f"展开速度："
            f"{face_velocity:.3f} rad/s"
        )

        print(
            f"展开加速度："
            f"{face_acceleration:.3f} rad/s²"
        )

        print(
            f"低头时间比例："
            f"{args.down_scale:.2f}"
        )

        print(
            f"缓存名称："
            f"{buffer_name}"
        )

        return 0

    try:
        prepare_head_buffer(
            args.first_scale,
            args.second_scale,
            args.down_scale,
            rebuild_cache=args.rebuild_cache,
        )

    except RuntimeError as exc:
        print(
            f"完整期待动作中止：{exc}",
            file=sys.stderr,
        )
        return 5

    try:
        with AuboSdkClient(config) as client:
            current = client.current_joints()

            start_error = max_joint_difference(
                current,
                pre_start,
            )

            if start_error > 0.15:
                raise AuboSdkError(
                    "机械臂当前不在"
                    "pre_start_rest附近。"
                    f"最大误差：{start_error:.4f} rad。"
                    "请先回到pre_start_rest。"
                )

            client.prepare_for_motion()

            robot = client._require_robot()
            motion = robot.getMotionControl()

            if not motion.pathBufferValid(
                buffer_name
            ):
                raise AuboSdkError(
                    "期待轨迹缓存没有保留或已经失效。"
                    f"缓存名称：{buffer_name}。"
                )

            print()
            print("=" * 52)
            print("期待第一步：展开并朝向用户")
            print("=" * 52)

            result = client.move_to_joints(
                center,
                prepare=False,
            )

            check_result(
                result,
                "下发展开动作",
            )

            (
                face_error,
                final_speed,
            ) = wait_until_reached_and_stopped(
                client,
                robot,
                center,
                timeout=40.0,
                position_tolerance=0.020,
                speed_tolerance=0.006,
                stable_samples=3,
                sample_interval=0.01,
            )

            print(
                "展开完成："
                f"位置误差={face_error:.6f} rad，"
                f"最大关节速度={final_speed:.6f} rad/s"
            )

            print()
            print("=" * 52)
            print("期待第二步：观察并低头")
            print("=" * 52)

            result = motion.movePathBuffer(
                buffer_name
            )

            check_result(
                result,
                "执行期待轨迹",
            )

            final_joints = (
                client.current_joints()
            )

            final_error = max_joint_difference(
                final_joints,
                look_down,
            )

            print()
            print("=" * 52)
            print("完整期待动作执行完成。")
            print("=" * 52)

            print(
                f"最终低头点误差："
                f"{final_error:.6f} rad"
            )

        return 0

    except AuboSdkError as exc:
        print()
        print(
            f"完整期待动作执行失败：{exc}",
            file=sys.stderr,
        )

        print(
            "出现碰撞提示时，"
            "请立即停止并在AuboStudio中恢复。",
            file=sys.stderr,
        )

        return 6

    except AttributeError as exc:
        print(
            f"SDK接口缺失：{exc}",
            file=sys.stderr,
        )
        return 7

    except KeyboardInterrupt:
        print()
        print("Python流程已中止。")
        print(
            "机械臂仍在运动时，"
            "请按实体Stop按钮。"
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())