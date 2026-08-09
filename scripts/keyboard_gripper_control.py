#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import termios
import tty
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.config import GripperConfig
from aubo_es3_actions.gripper import CONFIRM, LebaiGripper
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


HELP = """
乐白夹爪键盘控制

o    打开到 100%
c    闭合到 0%
m    移动到当前位置变量 position
f    设置力度为 force
[ ]  position 减/加 10
- +  force 减/加 10
s    读取夹爪反馈
h    显示帮助
q    退出
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 AUBO Modbus 控制乐白 RS485 夹爪")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="写寄存器功能码: 0x06 或 0x10")
    parser.add_argument("--position", type=int, default=50, help="默认开合位置 0-100")
    parser.add_argument("--force", type=int, default=50, help="默认力度 0-100")
    parser.add_argument("--command-delay", type=float, default=0.3, help="连续写力度/幅度之间的等待秒数")
    parser.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    parser.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印 Modbus 指令，不写寄存器")
    parser.add_argument("--confirm-motion", default="", help=f"真实写寄存器必须填入: {CONFIRM}")
    return parser


def read_key() -> str:
    return sys.stdin.read(1)


def show_result(result) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    args = build_parser().parse_args()
    execute = not args.dry_run and args.confirm_motion == CONFIRM
    if not execute:
        print("当前为 DRY-RUN：只打印将要发送的 Modbus RTU 指令，不写夹爪寄存器。")
        print(f"真实控制需要添加: --confirm-motion {CONFIRM}")

    position = max(0, min(100, int(args.position)))
    force = max(0, min(100, int(args.force)))
    old_settings = termios.tcgetattr(sys.stdin)

    config = load_config(args.config)
    base = config.gripper
    gripper_config = GripperConfig(
        modbus_device=args.modbus_device or base.modbus_device,
        slave_id=args.slave or base.slave_id,
        baudrate=base.baudrate,
        data_bits=base.data_bits,
        parity=base.parity,
        stop_bits=base.stop_bits,
        position_register=base.position_register,
        force_register=base.force_register,
        current_position_register=base.current_position_register,
        torque_register=base.torque_register,
        done_register=base.done_register,
        find_stroke_register=base.find_stroke_register,
        stroke_not_found_register=base.stroke_not_found_register,
        speed_register=base.speed_register,
        speed_save_register=base.speed_save_register,
        auto_find_stroke_register=base.auto_find_stroke_register,
        set_address_register=base.set_address_register,
    )
    print(HELP)
    print(f"position={position}, force={force}, device={gripper_config.modbus_device}, slave={gripper_config.slave_id}")
    with AuboSdkClient(config) as client:
        gripper = LebaiGripper(client, gripper_config)
        try:
            tty.setcbreak(sys.stdin.fileno())
            while True:
                key = read_key()
                try:
                    if key == "q":
                        print("\n退出夹爪键盘控制")
                        break
                    if key == "h":
                        print(HELP)
                    elif key == "[":
                        position = max(0, position - 10)
                        print(f"\nposition={position}")
                    elif key == "]":
                        position = min(100, position + 10)
                        print(f"\nposition={position}")
                    elif key == "-":
                        force = max(0, force - 10)
                        print(f"\nforce={force}")
                    elif key in ("+", "="):
                        force = min(100, force + 10)
                        print(f"\nforce={force}")
                    elif key == "o":
                        position = 100
                        print("\n打开夹爪")
                        show_result(gripper.move(
                            100,
                            force=force,
                            execute=execute,
                            command_delay_s=args.command_delay,
                            position_tolerance=args.tolerance,
                            verify_timeout_s=args.verify_timeout,
                            function_code=args.function_code,
                        ))
                    elif key == "c":
                        position = 0
                        print("\n闭合夹爪")
                        show_result(gripper.move(
                            0,
                            force=force,
                            execute=execute,
                            command_delay_s=args.command_delay,
                            position_tolerance=args.tolerance,
                            verify_timeout_s=args.verify_timeout,
                            function_code=args.function_code,
                        ))
                    elif key == "m":
                        print(f"\n移动夹爪到 {position}%")
                        show_result(gripper.move(
                            position,
                            force=force,
                            execute=execute,
                            command_delay_s=args.command_delay,
                            position_tolerance=args.tolerance,
                            verify_timeout_s=args.verify_timeout,
                            function_code=args.function_code,
                        ))
                    elif key == "f":
                        print(f"\n设置力度 {force}%")
                        show_result(gripper.set_force(force, execute=execute, function_code=args.function_code))
                    elif key == "s":
                        print("\n夹爪反馈")
                        show_result(gripper.feedback())
                except AuboSdkError as exc:
                    print(f"\n错误: {exc}")
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
