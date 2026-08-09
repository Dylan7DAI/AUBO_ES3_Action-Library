#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.sdk_client import (
    AuboSdkClient,
    AuboSdkError,
    JOINT_NAMES,
)


ANTICIPATION_SCRIPT = (
    SCRIPTS_DIR
    / "play_anticipation_continuous.py"
)


# GLOBAL_CHECK_RESULT_FIX
def check_result(
    result: object,
    action_name: str,
) -> None:
    """检查AUBO SDK接口返回结果。"""
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


POSE_FILE = (
    ROOT
    / "config"
    / "emotion_poses_new_es3.json"
)

RUNTIME_DIR = ROOT / ".runtime"
LOG_DIR = ROOT / "logs"

STOP_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.stop"
)

PID_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.pid"
)

READY_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.ready"
)

LOG_FILE = (
    LOG_DIR
    / "anticipation_breathing.log"
)

CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"

PATH_BUFFER_CUBIC_SPLINE = 2
PATH_SAMPLE_TIME = 0.01

# 只运动wrist3，限制保持保守
MAX_VELOCITY = 0.80
MAX_ACCELERATION = 2.00

# SETTLE_WAIT_V1
# 确认机械臂真正停稳后，再后，再交给呼吸轨迹。
SETTLE_TIMEOUT_SECONDS = 8.0
SETTLE_STABLE_SECONDS = 0.80
SETTLE_SAMPLE_SECONDS = 0.05
SETTLE_MAX_STEP_RAD = 0.0008
SETTLE_MAX_TARGET_ERROR_RAD = 0.05


def load_anticipation_module():
    """载入当前项目里的期待动作脚本，复用其可靠工具函数。"""
    spec = importlib.util.spec_from_file_location(
        "play_anticipation_continuous",
        ANTICIPATION_SCRIPT,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "无法载入play_anticipation_continuous.py"
        )

    module = importlib.util.module_from_spec(
        spec
    )
    spec.loader.exec_module(module)

    return module



def wait_until_arm_settled(
    client: object,
    helper: object,
    target: Sequence[float],
) -> tuple[List[float], float, float]:
    """等待靠近目标点且关节角连续一段时间几乎不再变化。"""
    required_samples = max(
        1,
        int(round(
            SETTLE_STABLE_SECONDS
            / SETTLE_SAMPLE_SECONDS
        )),
    )

    deadline = (
        time.monotonic()
        + SETTLE_TIMEOUT_SECONDS
    )

    previous = list(
        client.current_joints()
    )

    stable_count = 0

    last_error = (
        helper.max_joint_difference(
            previous,
            target,
        )
    )

    last_step = float("inf")

    print(
        "等待机械臂完全停稳后再启动呼吸……",
        flush=True,
    )

    while time.monotonic() < deadline:
        time.sleep(
            SETTLE_SAMPLE_SECONDS
        )

        current = list(
            client.current_joints()
        )

        last_error = (
            helper.max_joint_difference(
                current,
                target,
            )
        )

        last_step = (
            helper.max_joint_difference(
                current,
                previous,
            )
        )

        if (
            last_error
            <= SETTLE_MAX_TARGET_ERROR_RAD
            and last_step
            <= SETTLE_MAX_STEP_RAD
        ):
            stable_count += 1

            if stable_count >= required_samples:
                print(
                    "机械臂已停稳，准备启动呼吸："
                    f"目标误差={last_error:.6f} rad，"
                    f"单次变化={last_step:.6f} rad",
                    flush=True,
                )

                return (
                    current,
                    last_error,
                    last_step,
                )
        else:
            stable_count = 0

        previous = current

    raise AuboSdkError(
        "等待机械臂停稳超时，呼吸未启动。"
        f"目标误差：{last_error:.4f} rad；"
        f"单次变化：{last_step:.4f} rad"
    )


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def read_pid() -> Optional[int]:
    try:
        return int(
            PID_FILE.read_text(
                encoding="utf-8"
            ).strip()
        )
    except (
        OSError,
        ValueError,
    ):
        return None


def clear_stale_runtime_files() -> None:
    pid = read_pid()

    if (
        pid is not None
        and process_is_running(pid)
    ):
        raise RuntimeError(
            "腕部呼吸动作已经在运行。"
            "请先执行："
            "python3 scripts/stop_breathing.py"
        )

    for path in (
        STOP_FILE,
        PID_FILE,
        READY_FILE,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def build_breathing_trajectory(
    center: Sequence[float],
    *,
    amplitude_deg: float,
    cycle_seconds: float,
) -> List[List[float]]:
    """
    正中 → +幅度 → 正中 → -幅度 → 正中。

    使用sin^3曲线，使中心和两端换向更柔和。
    """
    wrist3_index = JOINT_NAMES.index(
        "wrist3_joint"
    )

    amplitude_rad = math.radians(
        amplitude_deg
    )

    steps = max(
        200,
        int(
            round(
                cycle_seconds
                / PATH_SAMPLE_TIME
            )
        ),
    )

    trajectory: List[List[float]] = []

    for index in range(steps):
        progress = index / (steps - 1)
        phase = 2.0 * math.pi * progress

        offset = (
            amplitude_rad
            * math.sin(phase) ** 3
        )

        point = list(center)
        point[wrist3_index] = (
            center[wrist3_index]
            + offset
        )

        trajectory.append(point)

    trajectory[-1] = list(center)

    return trajectory


def make_buffer_name(
    center: Sequence[float],
    *,
    amplitude_deg: float,
    cycle_seconds: float,
) -> str:
    signature = {
        "version": 1,
        "amplitude_deg": round(
            amplitude_deg,
            4,
        ),
        "cycle_seconds": round(
            cycle_seconds,
            4,
        ),
        "center": [
            round(float(value), 6)
            for value in center
        ],
    }

    encoded = json.dumps(
        signature,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    digest = hashlib.sha1(
        encoded
    ).hexdigest()[:10]

    return f"anti_breathe_v1_{digest}"


def prepare_breathing_buffer(
    motion: object,
    helper: object,
    buffer_name: str,
    trajectory: List[List[float]],
    *,
    rebuild: bool,
) -> None:
    buffer_exists = (
        buffer_name
        in helper.get_buffer_names(motion)
    )

    if buffer_exists and not rebuild:
        try:
            if motion.pathBufferValid(
                buffer_name
            ):
                print(
                    "复用已有腕部呼吸轨迹缓存。"
                )
                return
        except Exception:
            pass

    if buffer_exists:
        print(
            "删除旧的同名腕部呼吸轨迹缓存。"
        )

        try:
            motion.pathBufferFree(
                buffer_name
            )
        except Exception:
            pass

    print("正在创建腕部呼吸轨迹缓存……")
    print(f"缓存名称：{buffer_name}")
    print(f"轨迹点数：{len(trajectory)}")

    result = motion.pathBufferAlloc(
        buffer_name,
        PATH_BUFFER_CUBIC_SPLINE,
        len(trajectory),
    )

    helper.check_result(
        result,
        "创建腕部呼吸轨迹缓存",
    )

    helper.append_in_chunks(
        motion,
        buffer_name,
        trajectory,
    )

    result = motion.pathBufferEval(
        buffer_name,
        [MAX_ACCELERATION] * len(JOINT_NAMES),
        [MAX_VELOCITY] * len(JOINT_NAMES),
        PATH_SAMPLE_TIME,
    )

    helper.check_result(
        result,
        "计算腕部呼吸轨迹",
    )

    helper.wait_for_buffer_valid(
        motion,
        buffer_name,
        timeout=60.0,
    )



def prepare_fast_handoff(
    client: AuboSdkClient,
    robot: object,
) -> None:
    """机器人已上电时缩短进入持续呼吸前的固定等待。"""
    state = robot.getRobotState()

    if not state.isPowerOn():
        print("机器人未上电，执行常规安全启动……", flush=True)
        client.prepare_for_motion()
        return

    manage = robot.getRobotManage()
    manage.startup()
    time.sleep(0.20)

    print(
        "已复用机器人当前启动状态，"
        "进入呼吸前只等待0.2秒。",
        flush=True,
    )

def run_worker(
    args: argparse.Namespace,
) -> int:
    """后台持续执行呼吸循环。"""
    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    PID_FILE.write_text(
        str(os.getpid()),
        encoding="utf-8",
    )

    try:
        helper = load_anticipation_module()

        data = json.loads(
            POSE_FILE.read_text(
                encoding="utf-8"
            )
        )

        center_pose_name = args.center_pose
        center_pose = helper.load_pose(
            data,
            center_pose_name,
        )

        trajectory = (
            build_breathing_trajectory(
                center_pose,
                amplitude_deg=(
                    args.breath_amplitude_deg
                ),
                cycle_seconds=(
                    args.breath_cycle_seconds
                ),
            )
        )

        buffer_name = make_buffer_name(
            center_pose,
            amplitude_deg=(
                args.breath_amplitude_deg
            ),
            cycle_seconds=(
                args.breath_cycle_seconds
            ),
        )

        config = load_config(
            args.config
        )

        with AuboSdkClient(config) as client:
            (
                current,
                start_error,
                _,
            ) = wait_until_arm_settled(
                client,
                helper,
                center_pose,
            )

            if start_error > 0.10:
                raise AuboSdkError(
                    "机械臂当前不在"
                    f"{center_pose_name}附近。"
                    f"最大误差：{start_error:.4f} rad。"
                )

            robot = client._require_robot()
            motion = robot.getMotionControl()

            prepare_breathing_buffer(
                motion,
                helper,
                buffer_name,
                trajectory,
                rebuild=(
                    args.rebuild_breath_cache
                ),
            )

            prepare_fast_handoff(client, robot)

            READY_FILE.write_text(
                (
                    f"pid={os.getpid()}\n"
                    f"center_pose={center_pose_name}\n"
                    f"amplitude_deg="
                    f"{args.breath_amplitude_deg}\n"
                    f"cycle_seconds="
                    f"{args.breath_cycle_seconds}\n"
                ),
                encoding="utf-8",
            )

            print()
            print("=" * 56)
            print("腕部呼吸循环已启动")
            print("=" * 56)
            print(
                f"中心点位：{center_pose_name}"
            )
            print(
                "单侧幅度："
                f"{args.breath_amplitude_deg:.2f}度"
            )
            print(
                "总往返范围：约"
                f"{2.0 * args.breath_amplitude_deg:.2f}度"
            )
            print(
                "单轮周期："
                f"{args.breath_cycle_seconds:.2f}秒"
            )

            # BREATH_LOOP_WAIT_V2
            # 每轮只提交一次轨迹。
            # 在等待期间读取真实wrist3角度，
            # 避免轨迹被不断重新启动。
            cycle_count = 0

            wrist3_index = JOINT_NAMES.index(
                "wrist3_joint"
            )

            center_wrist3 = center_pose[
                wrist3_index
            ]

            while (
                not STOP_FILE.exists()
                and (
                    args.cycles == 0
                    or cycle_count < args.cycles
                )
            ):
                cycle_count += 1

                print(
                    f"开始呼吸循环："
                    f"{cycle_count}",
                    flush=True,
                )

                result = motion.movePathBuffer(
                    buffer_name
                )

                check_result(
                    result,
                    "执行腕部呼吸轨迹",
                )

                minimum_offset_deg = 0.0
                maximum_offset_deg = 0.0

                cycle_deadline = (
                    time.monotonic()
                    + args.breath_cycle_seconds
                    + 0.50
                )

                while (
                    time.monotonic()
                    < cycle_deadline
                ):
                    current_joints = (
                        client.current_joints()
                    )

                    offset_deg = math.degrees(
                        current_joints[
                            wrist3_index
                        ]
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

                    time.sleep(0.05)

                print(
                    f"已完成呼吸循环："
                    f"{cycle_count}；"
                    f"wrist3实际范围："
                    f"{minimum_offset_deg:.2f}°"
                    f" 至 "
                    f"{maximum_offset_deg:.2f}°",
                    flush=True,
                )

            # 收到停止信号时，
            # 当前完整周期已经执行结束并回到中心。
            final_joints = (
                client.current_joints()
            )

            final_error = (
                helper.max_joint_difference(
                    final_joints,
                    center_pose,
                )
            )

            print()
            print("已收到停止信号。")
            print(
                f"腕部已回到{center_pose_name}中心角度，"
                f"最大误差：{final_error:.6f} rad"
            )

        return 0

    except (
        OSError,
        KeyError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
        AuboSdkError,
    ) as exc:
        print(
            f"腕部呼吸动作失败：{exc}",
            file=sys.stderr,
        )
        print(
            "机械臂仍在异常运动时，"
            "请按实体Stop按钮。",
            file=sys.stderr,
        )
        return 8

    except KeyboardInterrupt:
        print()
        print(
            "后台流程收到中断。"
            "机械臂仍在运动时，"
            "请按实体Stop按钮。"
        )
        return 130

    finally:
        for path in (
            READY_FILE,
            PID_FILE,
            STOP_FILE,
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def start_background_worker(
    args: argparse.Namespace,
) -> int:
    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )
    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        clear_stale_runtime_files()
    except RuntimeError as exc:
        print(
            str(exc),
            file=sys.stderr,
        )
        return 2

    if not args.breathing_only:
        command = [
            sys.executable,
            str(ANTICIPATION_SCRIPT),
        ]

        if args.rebuild_anticipation_cache:
            command.append(
                "--rebuild-cache"
            )

        if args.config is not None:
            command.extend(
                [
                    "--config",
                    args.config,
                ]
            )

        command.extend(
            [
                "--confirm-motion",
                CONFIRM,
            ]
        )

        print("先执行完整期待动作……")

        result = subprocess.run(
            command,
            cwd=str(ROOT),
            check=False,
        )

        if result.returncode != 0:
            print(
                "期待动作未成功完成，"
                "不会启动腕部呼吸。",
                file=sys.stderr,
            )
            return result.returncode

    worker_command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--breath-amplitude-deg",
        str(args.breath_amplitude_deg),
        "--breath-cycle-seconds",
        str(args.breath_cycle_seconds),
        "--center-pose",
        args.center_pose,
        "--cycles",
        str(args.cycles),
    ]

    if args.rebuild_breath_cache:
        worker_command.append(
            "--rebuild-breath-cache"
        )

    if args.config is not None:
        worker_command.extend(
            [
                "--config",
                args.config,
            ]
        )

    if args.cycles > 0:
        print(
            f"前台执行有限呼吸："
            f"{args.cycles}轮"
        )

        result = subprocess.run(
            worker_command,
            cwd=str(ROOT),
            check=False,
        )

        return result.returncode

    with LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as log_handle:
        process = subprocess.Popen(
            worker_command,
            cwd=str(ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.monotonic() + 90.0

    while time.monotonic() < deadline:
        if READY_FILE.exists():
            print()
            print("=" * 56)
            print("腕部呼吸已在后台持续运行")
            print("=" * 56)
            print(f"后台PID：{process.pid}")
            print(
                "停止并等待回中："
            )
            print(
                "python3 scripts/stop_breathing.py"
            )
            print(
                f"日志：{LOG_FILE}"
            )
            return 0

        if process.poll() is not None:
            print(
                "后台呼吸进程启动失败。"
                f"请查看日志：{LOG_FILE}",
                file=sys.stderr,
            )
            return (
                process.returncode
                if process.returncode is not None
                else 8
            )

        time.sleep(0.20)

    print(
        "等待后台呼吸进程就绪超时。"
        f"请查看日志：{LOG_FILE}",
        file=sys.stderr,
    )
    return 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "执行完整期待动作，随后在低头姿态"
            "持续进行wrist3呼吸式往返旋转。"
        )
    )

    parser.add_argument(
        "--breath-amplitude-deg",
        type=float,
        default=10.0,
        help=(
            "单侧幅度，默认10度；"
            "总往返范围约20度"
        ),
    )

    parser.add_argument(
        "--breath-cycle-seconds",
        type=float,
        default=4.0,
        help="完整一轮周期，默认4秒",
    )

    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help=(
            "呼吸循环次数；"
            "0表示持续运行，"
            "正整数表示完成指定次数后回中退出"
        ),
    )

    parser.add_argument(
        "--rebuild-anticipation-cache",
        action="store_true",
        help="强制重建期待动作缓存",
    )

    parser.add_argument(
        "--rebuild-breath-cache",
        action="store_true",
        help="强制重建呼吸轨迹缓存",
    )

    parser.add_argument(
        "--center-pose",
        default="anticipation_look_down",
        help=(
            "持续呼吸使用的中心点位；"
            "默认anticipation_look_down"
        ),
    )

    parser.add_argument(
        "--breathing-only",
        action="store_true",
        help=(
            "跳过期待动作；"
            "仅在已经处于look_down附近时"
            "启动呼吸"
        ),
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

    parser.add_argument(
        "--worker",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not 2.0 <= args.breath_amplitude_deg <= 20.0:
        print(
            "breath-amplitude-deg必须在"
            "2度到20度之间。",
            file=sys.stderr,
        )
        return 1

    if not 2.5 <= args.breath_cycle_seconds <= 8.0:
        print(
            "breath-cycle-seconds必须在"
            "2.5秒到8秒之间。",
            file=sys.stderr,
        )
        return 1

    if args.worker:
        return run_worker(args)

    if args.confirm_motion != CONFIRM:
        print(
            "当前为预览模式，机械臂不会运动。"
        )
        print(
            "动作：完整期待"
            " → 低头腕部持续呼吸"
        )
        print(
            "单侧幅度："
            f"{args.breath_amplitude_deg:.2f}度"
        )
        print(
            "总往返范围：约"
            f"{2.0 * args.breath_amplitude_deg:.2f}度"
        )
        print(
            "单轮周期："
            f"{args.breath_cycle_seconds:.2f}秒"
        )
        return 0

    return start_background_worker(args)


if __name__ == "__main__":
    raise SystemExit(main())

