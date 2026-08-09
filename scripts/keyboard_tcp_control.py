#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import termios
import tty
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"

AXIS_NAMES = ["x", "y", "z", "rx", "ry", "rz"]

HELP = """
AUBO ES3 末端位姿键盘点动

w/s  X +/-
a/d  Y +/-
r/f  Z +/-
i/k  RX +/-
j/l  RY +/-
u/o  RZ +/-
[/]  调整步长
p    打印当前 TCP 位姿
h    显示帮助
q    退出
"""

KEY_TO_AXIS_DELTA = {
    "w": (0, +1),
    "s": (0, -1),
    "a": (1, +1),
    "d": (1, -1),
    "r": (2, +1),
    "f": (2, -1),
    "i": (3, +1),
    "k": (3, -1),
    "j": (4, +1),
    "l": (4, -1),
    "u": (5, +1),
    "o": (5, -1),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过键盘控制 AUBO ES3 末端 TCP 位姿点动")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--linear-step", type=float, default=0.005, help="XYZ 点动步长(m)")
    parser.add_argument("--angular-step", type=float, default=0.02, help="RX/RY/RZ 点动步长(rad)")
    parser.add_argument("--linear-acc", type=float, default=0.05, help="moveLine 线加速度 m/s^2")
    parser.add_argument("--linear-vel", type=float, default=0.02, help="moveLine 线速度 m/s")
    parser.add_argument("--dry-run", action="store_true", help="只预览目标，不下发运动")
    parser.add_argument(
        "--confirm-motion",
        default="",
        help=f"真实运动必须填入固定确认文本: {CONFIRM}",
    )
    return parser


def read_key() -> str:
    return sys.stdin.read(1)


def format_pose(pose) -> str:
    if len(pose) != 6:
        return str(pose)
    return (
        f"x={pose[0]:+.6f} y={pose[1]:+.6f} z={pose[2]:+.6f} m, "
        f"rx={pose[3]:+.6f} ry={pose[4]:+.6f} rz={pose[5]:+.6f} rad"
    )


def main() -> int:
    args = build_parser().parse_args()
    execute = not args.dry_run and args.confirm_motion == CONFIRM
    if not execute:
        print("当前为 DRY-RUN：会读取状态、求逆解并预览目标，但不会给机器人上电或发送运动。")
        print(f"真实运动需要添加: --confirm-motion {CONFIRM}")

    config = load_config(args.config)
    linear_step = float(args.linear_step)
    angular_step = float(args.angular_step)
    old_settings = termios.tcgetattr(sys.stdin)

    print(HELP)
    with AuboSdkClient(config) as client:
        print("当前 TCP:", format_pose(client.current_pose()))
        print(f"平移步长 {linear_step:.4f} m，姿态步长 {angular_step:.4f} rad")
        try:
            tty.setcbreak(sys.stdin.fileno())
            while True:
                key = read_key()
                if key == "q":
                    print("\n退出末端位姿键盘控制")
                    break
                if key == "h":
                    print(HELP)
                    continue
                if key == "p":
                    print("\n当前 TCP:", format_pose(client.current_pose()))
                    continue
                if key == "[":
                    linear_step = max(0.001, linear_step / 2.0)
                    angular_step = max(0.002, angular_step / 2.0)
                    print(f"\n步长: {linear_step:.4f} m / {angular_step:.4f} rad")
                    continue
                if key == "]":
                    linear_step = min(0.08, linear_step * 2.0)
                    angular_step = min(0.08, angular_step * 2.0)
                    print(f"\n步长: {linear_step:.4f} m / {angular_step:.4f} rad")
                    continue
                if key not in KEY_TO_AXIS_DELTA:
                    continue

                axis, sign = KEY_TO_AXIS_DELTA[key]
                delta = sign * (linear_step if axis < 3 else angular_step)
                try:
                    target = client.make_tcp_jog_target(axis, delta)
                    ik = client.inverse_kinematics(target)
                    if not execute:
                        print(
                            f"\nDRY-RUN {AXIS_NAMES[axis]} {delta:+.6f} -> "
                            f"{format_pose(target)}"
                        )
                    else:
                        target = client.jog_tcp(
                            axis,
                            delta,
                            linear_acc_m_s2=args.linear_acc,
                            linear_vel_m_s=args.linear_vel,
                            prepare=True,
                        )
                        print(f"\n已移动 {AXIS_NAMES[axis]} {delta:+.6f} -> {format_pose(target)}")
                except AuboSdkError as exc:
                    print(f"\n错误: {exc}")
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
