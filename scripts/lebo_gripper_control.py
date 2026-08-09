#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.config import GripperConfig
from aubo_es3_actions.gripper import CONFIRM, LebaiGripper
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


def make_gripper_config(config, device: str, slave: int) -> GripperConfig:
    base = config.gripper
    return GripperConfig(
        modbus_device=device or base.modbus_device,
        slave_id=slave or base.slave_id,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="乐白夹爪控制，走 AUBO 工具端 RS485 透传")
    parser.add_argument("command", choices=[
        "open",
        "close",
        "move",
        "force",
        "speed",
        "feedback",
        "status",
        "find_stroke",
        "disable_auto_home",
    ])
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--width", type=int, default=50, help="move 用: 幅度 0-100，0 闭合，100 打开")
    parser.add_argument("--force", type=int, default=-1, help="move/force 用: 力度 0-100")
    parser.add_argument("--speed", type=int, default=-1, help="move/speed 用: 速度 0-100")
    parser.add_argument("--persist", action="store_true", help="速度或关闭自动找行程写入断电保存")
    parser.add_argument("--wait", action="store_true", help="move 后尝试等待 done 状态，当前反馈读取仍需现场验证")
    parser.add_argument("--command-delay", type=float, default=0.3, help="连续写 force/speed/width 之间的等待秒数")
    parser.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    parser.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    parser.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10，现场已验证 0x06 会动作")
    parser.add_argument("--confirm-motion", default="", help=f"真实写寄存器必须填入: {CONFIRM}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    execute = args.confirm_motion == CONFIRM
    if args.command not in ("feedback", "status") and not execute:
        print("DRY-RUN：只打印将要发送的 Modbus 指令，不写夹爪寄存器。", file=sys.stderr)
        print(f"真实控制需要添加: --confirm-motion {CONFIRM}", file=sys.stderr)

    config = load_config(args.config)
    gripper_config = make_gripper_config(config, args.modbus_device, args.slave)

    try:
        with AuboSdkClient(config) as client:
            gripper = LebaiGripper(client, gripper_config)
            if args.command == "open":
                result = gripper.move(
                    100,
                    execute=execute,
                    wait=args.wait,
                    command_delay_s=args.command_delay,
                    position_tolerance=args.tolerance,
                    verify=not args.no_verify,
                    verify_timeout_s=args.verify_timeout,
                    function_code=args.function_code,
                )
            elif args.command == "close":
                result = gripper.move(
                    0,
                    execute=execute,
                    wait=args.wait,
                    command_delay_s=args.command_delay,
                    position_tolerance=args.tolerance,
                    verify=not args.no_verify,
                    verify_timeout_s=args.verify_timeout,
                    function_code=args.function_code,
                )
            elif args.command == "move":
                result = gripper.move(
                    args.width,
                    force=None if args.force < 0 else args.force,
                    speed=None if args.speed < 0 else args.speed,
                    execute=execute,
                    wait=args.wait,
                    command_delay_s=args.command_delay,
                    position_tolerance=args.tolerance,
                    verify=not args.no_verify,
                    verify_timeout_s=args.verify_timeout,
                    function_code=args.function_code,
                )
            elif args.command == "force":
                if args.force < 0:
                    raise ValueError("force 命令必须传 --force 0-100")
                result = gripper.set_force(args.force, execute=execute, function_code=args.function_code)
            elif args.command == "speed":
                if args.speed < 0:
                    raise ValueError("speed 命令必须传 --speed 0-100")
                result = gripper.set_speed(
                    args.speed,
                    execute=execute,
                    persist=args.persist,
                    function_code=args.function_code,
                )
            elif args.command == "feedback":
                result = gripper.feedback()
            elif args.command == "status":
                result = gripper.status()
            elif args.command == "find_stroke":
                result = gripper.find_stroke(execute=execute, function_code=args.function_code)
            elif args.command == "disable_auto_home":
                result = gripper.disable_auto_find_stroke(
                    execute=execute,
                    persist=args.persist,
                    function_code=args.function_code,
                )
            else:
                raise ValueError(f"未知命令: {args.command}")
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
