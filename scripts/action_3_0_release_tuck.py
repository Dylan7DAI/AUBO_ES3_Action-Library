#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config, run_action
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="动作 3.0: 松爪并回到收缩位姿")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--pose", default="tucked", help="收缩位姿名")
    parser.add_argument("--pose-file", default="", help="机械臂位姿 JSON 路径")
    parser.add_argument("--open-width", type=int, default=100, help="松爪张开幅度 0-100")
    parser.add_argument("--open-force", type=int, default=15, help="松爪时夹爪力度 0-100")
    parser.add_argument("--release-delay", type=float, default=1.0, help="松爪后等待秒数")
    parser.add_argument("--move-timeout", type=float, default=30.0, help="等待关节运动到位的最长秒数")
    parser.add_argument("--joint-tolerance", type=float, default=0.01, help="关节到位误差 rad")
    parser.add_argument("--command-delay", type=float, default=0.3, help="夹爪 force/width 写入间隔秒数")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    execute = args.confirm_motion == CONFIRM
    if execute:
        print("即将真实张开夹爪并移动机械臂到收缩位姿。请确认物体释放位置和回位路径安全。", file=sys.stderr)
    else:
        print("dry-run：只读取当前状态并预览松爪/收缩目标，不发送命令。", file=sys.stderr)

    config = load_config(args.config)
    try:
        with AuboSdkClient(config) as client:
            result = run_action(
                "3.0",
                client,
                execute=execute,
                pose=args.pose,
                pose_file=args.pose_file,
                open_width=args.open_width,
                open_force=args.open_force,
                release_delay=args.release_delay,
                move_timeout=args.move_timeout,
                joint_tolerance=args.joint_tolerance,
                command_delay=args.command_delay,
                device=args.modbus_device,
                slave=args.slave,
            )
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
