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
from aubo_es3_actions.safety import make_jog_target
from aubo_es3_actions.sdk_client import AuboSdkClient, JOINT_NAMES


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"

HELP = """
AUBO ES3 SDK 键盘点动

1-6  选择关节
a/d  当前关节负/正方向移动
[/]  减小/增大步长
j    打印当前关节角
p    打印当前 TCP 位姿
h    显示帮助
q    退出
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 pyaubo_sdk 键盘控制 AUBO ES3 关节点动")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--step", type=float, default=0.02, help="默认关节点动步长(rad)")
    parser.add_argument("--dry-run", action="store_true", help="只预览目标，不下发运动")
    parser.add_argument(
        "--confirm-motion",
        default="",
        help=f"真实运动必须填入固定确认文本: {CONFIRM}",
    )
    return parser


def read_key() -> str:
    return sys.stdin.read(1)


def print_joints(client: AuboSdkClient) -> None:
    joints = client.current_joints()
    print("当前关节角:")
    for idx, (name, value) in enumerate(zip(JOINT_NAMES, joints), start=1):
        print(f"  {idx}. {name}: {value:+.6f} rad")


def print_pose(client: AuboSdkClient) -> None:
    tcp = client.current_pose()
    if len(tcp) == 6:
        x, y, z, rx, ry, rz = tcp
        print("当前 TCP 位姿:")
        print(f"  xyz(m): {x:+.6f}, {y:+.6f}, {z:+.6f}")
        print(f"  rpy(rad): {rx:+.6f}, {ry:+.6f}, {rz:+.6f}")
    else:
        print(f"当前 TCP 原始值: {tcp}")


def main() -> int:
    args = build_parser().parse_args()
    execute = not args.dry_run and args.confirm_motion == CONFIRM
    if not execute:
        print("当前为 DRY-RUN：会读取状态并预览目标，但不会给机器人上电或发送运动。")
        print(f"真实运动需要添加: --confirm-motion {CONFIRM}")

    config = load_config(args.config)
    step = float(args.step)
    selected = 0
    old_settings = termios.tcgetattr(sys.stdin)

    print(HELP)
    with AuboSdkClient(config) as client:
        print_joints(client)
        print(f"已选择关节 1: {JOINT_NAMES[selected]}, 步长 {step:.4f} rad")
        try:
            tty.setcbreak(sys.stdin.fileno())
            while True:
                key = read_key()
                if key == "q":
                    print("\n退出键盘控制")
                    break
                if key in "123456":
                    selected = int(key) - 1
                    print(f"\n已选择关节 {key}: {JOINT_NAMES[selected]}")
                    continue
                if key == "h":
                    print(HELP)
                    continue
                if key == "j":
                    print()
                    print_joints(client)
                    continue
                if key == "p":
                    print()
                    print_pose(client)
                    continue
                if key == "[":
                    step = max(0.001, step / 2.0)
                    print(f"\n步长: {step:.4f} rad")
                    continue
                if key == "]":
                    limit = config.safety.max_delta_rad[selected]
                    step = min(limit, step * 2.0)
                    print(f"\n步长: {step:.4f} rad")
                    continue
                if key in ("a", "d"):
                    delta = -step if key == "a" else step
                    current = client.current_joints()
                    target = make_jog_target(current, selected, delta, config.safety)
                    if not execute:
                        print(
                            f"\nDRY-RUN {JOINT_NAMES[selected]} -> {target[selected]:+.6f} rad"
                        )
                    else:
                        target = client.jog_joint(selected, delta, prepare=True)
                        print(
                            f"\n{JOINT_NAMES[selected]} -> {target[selected]:+.6f} rad"
                        )
                    continue
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
