#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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

CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"

PATH_BUFFER_CUBIC_SPLINE = 2
PATH_SAMPLE_TIME = 0.01

BASE_TRAJECTORY_SPEED = 1.50
BASE_MIN_SEGMENT_TIME = 0.24

MAX_VELOCITY = 2.80
MAX_ACCELERATION = 6.00


def load_pose(
    data: dict,
    pose_name: str,
) -> List[float]:
    """按照固定关节顺序读取示教点位。"""
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
    """检查AUBO接口返回结果。"""
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
    """把比例转换成缓存名称可用的字符串。"""
    return f"{value:.2f}".replace(".", "p")


def make_buffer_name(
    first_scale: float,
    second_scale: float,
    down_scale: float,
) -> str:
    """生成当前动作版本对应的缓存名称。"""
    return (
        "anti_"
        f"f{scale_tag(first_scale)}_"
        f"s{scale_tag(second_scale)}_"
        f"d{scale_tag(down_scale)}_"
        "v6"
    )


def calculate_base_duration(
    start: Sequence[float],
    end: Sequence[float],
) -> float:
    """计算一个基础动作段的时长。"""
    distance = max_joint_difference(
        start,
        end,
    )

    return max(
        BASE_MIN_SEGMENT_TIME,
        1.5 * distance / BASE_TRAJECTORY_SPEED,
    )


def build_observation_trajectory(
    center: List[float],
    left: List[float],
    right: List[float],
    look_down: List[float],
    first_scale: float,
    second_scale: float,
    down_scale: float,
) -> tuple[List[List[float]], List[float]]:
    """
    生成期待阶段完整轨迹。

    顺序：

    正中
    → 左
    → 正中
    → 右
    → 正中
    → 左
    → 正中
    → 低头

    最终停在低头位置，不再回正中。
    """

    anchors = [
        center,
        left,
        center,
        right,
        center,
        left,
        center,
        look_down,
    ]

    # 前4段：第一次左看、右看
    # 中间2段：第二次左看和回位
    # 最后1段：低头
    segment_scales = [
        first_scale,
        first_scale,
        first_scale,
        first_scale,
        second_scale,
        second_scale,
        down_scale,
    ]

    segment_steps: List[int] = []
    segment_durations: List[float] = []

    for index in range(len(anchors) - 1):
        base_duration = calculate_base_duration(
            anchors[index],
            anchors[index + 1],
        )

        requested_duration = (
            base_duration
            * segment_scales[index]
        )

        steps = max(
            3,
            int(
                round(
                    requested_duration
                    / PATH_SAMPLE_TIME
                )
            ),
        )

        actual_duration = (
            steps * PATH_SAMPLE_TIME
        )

        segment_steps.append(steps)
        segment_durations.append(
            actual_duration
        )

    anchor_times = [0.0]

    for duration in segment_durations:
        anchor_times.append(
            anchor_times[-1] + duration
        )

    # 计算关键点切线，使动作连续衔接
    tangents: List[List[float]] = []

    for index in range(len(anchors)):
        if (
            index == 0
            or index == len(anchors) - 1
        ):
            tangents.append(
                [0.0] * len(JOINT_NAMES)
            )
            continue

        total_time = (
            anchor_times[index + 1]
            - anchor_times[index - 1]
        )

        tangent = [
            (
                anchors[index + 1][joint_index]
                - anchors[index - 1][joint_index]
            )
            / total_time
            for joint_index in range(
                len(JOINT_NAMES)
            )
        ]

        tangents.append(tangent)

    trajectory: List[List[float]] = []

    for segment_index in range(
        len(anchors) - 1
    ):
        start = anchors[segment_index]
        end = anchors[segment_index + 1]

        start_velocity = tangents[
            segment_index
        ]

        end_velocity = tangents[
            segment_index + 1
        ]

        steps = segment_steps[
            segment_index
        ]

        duration = segment_durations[
            segment_index
        ]

        for step_index in range(steps):
            u = step_index / steps

            u2 = u * u
            u3 = u2 * u

            h00 = (
                2.0 * u3
                - 3.0 * u2
                + 1.0
            )

            h10 = (
                u3
                - 2.0 * u2
                + u
            )

            h01 = (
                -2.0 * u3
                + 3.0 * u2
            )

            h11 = (
                u3
                - u2
            )

            point: List[float] = []

            for joint_index in range(
                len(JOINT_NAMES)
            ):
                value = (
                    h00
                    * start[joint_index]

                    + h10
                    * duration
                    * start_velocity[joint_index]

                    + h01
                    * end[joint_index]

                    + h11
                    * duration
                    * end_velocity[joint_index]
                )

                point.append(value)

            trajectory.append(point)

    # 最终停在低头点位
    trajectory.append(
        list(look_down)
    )

    return trajectory, segment_durations


def append_in_chunks(
    motion: object,
    buffer_name: str,
    trajectory: List[List[float]],
    *,
    chunk_size: int = 250,
) -> None:
    """分批上传轨迹点。"""
    for start_index in range(
        0,
        len(trajectory),
        chunk_size,
    ):
        chunk = trajectory[
            start_index:
            start_index + chunk_size
        ]

        result = motion.pathBufferAppend(
            buffer_name,
            chunk,
        )

        check_result(
            result,
            (
                f"添加轨迹点"
                f"{start_index}—"
                f"{start_index + len(chunk) - 1}"
            ),
        )


def wait_for_buffer_valid(
    motion: object,
    buffer_name: str,
    *,
    timeout: float = 60.0,
) -> None:
    """等待控制器完成轨迹计算。"""
    deadline = time.monotonic() + timeout
    last_print_time = 0.0

    while time.monotonic() < deadline:
        if motion.pathBufferValid(
            buffer_name
        ):
            print("期待轨迹缓存计算成功。")
            return

        now = time.monotonic()

        if now - last_print_time >= 1.0:
            print("期待轨迹仍在计算……")
            last_print_time = now

        time.sleep(0.20)

    raise AuboSdkError(
        "等待60秒后期待轨迹缓存仍然无效"
    )


def get_buffer_names(
    motion: object,
) -> List[str]:
    """读取控制器中已有的缓存名称。"""
    try:
        result = motion.pathBufferList()

        if result is None:
            return []

        return [
            str(name)
            for name in result
        ]

    except Exception:
        return []


def prepare_buffer(
    motion: object,
    buffer_name: str,
    trajectory: List[List[float]],
    *,
    rebuild: bool,
) -> bool:
    """
    准备期待动作缓存。

    返回：
    True：复用已有缓存
    False：重新创建缓存
    """
    buffer_names = get_buffer_names(
        motion
    )

    buffer_exists = (
        buffer_name in buffer_names
    )

    if buffer_exists and not rebuild:
        try:
            if motion.pathBufferValid(
                buffer_name
            ):
                print(
                    "复用已计算的期待轨迹缓存，"
                    "无需重新计算。"
                )
                return True

        except Exception:
            pass

    if buffer_exists:
        print("清除旧的同名期待轨迹缓存。")

        try:
            motion.pathBufferFree(
                buffer_name
            )
        except Exception:
            pass

    print("正在创建新的期待轨迹缓存……")
    print(f"缓存名称：{buffer_name}")
    print(f"轨迹点数：{len(trajectory)}")

    result = motion.pathBufferAlloc(
        buffer_name,
        PATH_BUFFER_CUBIC_SPLINE,
        len(trajectory),
    )

    check_result(
        result,
        "创建期待轨迹缓存",
    )

    append_in_chunks(
        motion,
        buffer_name,
        trajectory,
    )

    acceleration_limits = [
        MAX_ACCELERATION
    ] * len(JOINT_NAMES)

    velocity_limits = [
        MAX_VELOCITY
    ] * len(JOINT_NAMES)

    print("正在计算期待轨迹缓存……")

    result = motion.pathBufferEval(
        buffer_name,
        acceleration_limits,
        velocity_limits,
        PATH_SAMPLE_TIME,
    )

    check_result(
        result,
        "计算期待轨迹缓存",
    )

    wait_for_buffer_valid(
        motion,
        buffer_name,
        timeout=60.0,
    )

    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "期待动作："
            "左看、右看、左看，最后低头"
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
            "最后低头动作的时间比例，"
            "默认3.5；数值越大越慢"
        ),
    )

    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help=(
            "只提前计算并保存轨迹缓存，"
            "机械臂不会运动"
        ),
    )

    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="强制删除并重新创建缓存",
    )

    parser.add_argument(
        "--confirm-motion",
        default="",
        help="真实运动确认文本",
    )

    parser.add_argument(
        "--config",
        default=None,
        help="可选机器人配置文件",
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

    try:
        data = json.loads(
            POSE_FILE.read_text(
                encoding="utf-8"
            )
        )

        center = load_pose(
            data,
            "anticipation_face_user",
        )

        left = load_pose(
            data,
            "anticipation_idle_left",
        )

        right = load_pose(
            data,
            "anticipation_idle_right",
        )

        look_down = load_pose(
            data,
            "anticipation_look_down",
        )

        (
            trajectory,
            segment_durations,
        ) = build_observation_trajectory(
            center,
            left,
            right,
            look_down,
            args.first_scale,
            args.second_scale,
            args.down_scale,
        )

    except (
        OSError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"读取或生成动作失败：{exc}",
            file=sys.stderr,
        )
        return 4

    buffer_name = make_buffer_name(
        args.first_scale,
        args.second_scale,
        args.down_scale,
    )

    first_duration = sum(
        segment_durations[:4]
    )

    second_duration = sum(
        segment_durations[4:6]
    )

    down_duration = segment_durations[6]

    if (
        not args.prepare_only
        and args.confirm_motion != CONFIRM
    ):
        print(
            "当前为预览模式，"
            "机械臂不会运动。"
        )

        print()
        print("动作顺序：")
        print(
            "正中 → 左 → 正中 → 右 "
            "→ 正中 → 左 → 正中 → 低头"
        )

        print()
        print(
            f"第一次观察时长："
            f"{first_duration:.2f}秒"
        )

        print(
            f"第二次左看时长："
            f"{second_duration:.2f}秒"
        )

        print(
            f"低头时长："
            f"{down_duration:.2f}秒"
        )

        print(
            f"总预计时长："
            f"{sum(segment_durations):.2f}秒"
        )

        print(
            f"轨迹点数："
            f"{len(trajectory)}"
        )

        print(
            f"缓存名称："
            f"{buffer_name}"
        )

        return 0

    config = load_config(args.config)

    try:
        with AuboSdkClient(config) as client:
            robot = client._require_robot()
            motion = robot.getMotionControl()

            reused = prepare_buffer(
                motion,
                buffer_name,
                trajectory,
                rebuild=args.rebuild_cache,
            )

            if args.prepare_only:
                print()
                print("期待轨迹预热完成。")

                if reused:
                    print("本次复用了已有缓存。")
                else:
                    print("本次创建了新的缓存。")

                return 0

            current = client.current_joints()

            start_error = max_joint_difference(
                current,
                center,
            )

            if start_error > 0.12:
                raise AuboSdkError(
                    "机械臂当前不在"
                    "anticipation_face_user附近。"
                    f"最大偏差：{start_error:.4f} rad。"
                    "为避免异常，取消期待轨迹。"
                )

            client.prepare_for_motion()

            print()
            print("开始期待观察动作。")
            print(
                "左看 → 右看 → 左看 "
                "→ 回正 → 低头"
            )

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
            print("期待观察动作执行完成。")

            print(
                f"最终低头点误差："
                f"{final_error:.6f} rad"
            )

        return 0

    except AuboSdkError as exc:
        print(
            f"期待动作执行失败：{exc}",
            file=sys.stderr,
        )
        return 5

    except AttributeError as exc:
        print(
            f"SDK接口缺失：{exc}",
            file=sys.stderr,
        )
        return 6

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