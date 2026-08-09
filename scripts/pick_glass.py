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
    parser = argparse.ArgumentParser(description="低夹力夹取玻璃杯并抬高")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--force", type=int, default=18, help="夹爪力度 0-100，建议从 15-20 开始")
    parser.add_argument("--close-position", type=int, default=55, help="最终夹爪位置 0-100，0 全闭，100 全开")
    parser.add_argument("--lift", type=float, default=0.05, help="最终抬高距离 m")
    parser.add_argument("--test-lift", type=float, default=0.005, help="正式抬高前的试提距离 m")
    parser.add_argument("--linear-acc", type=float, default=0.03, help="线加速度 m/s^2")
    parser.add_argument("--linear-vel", type=float, default=0.01, help="线速度 m/s")
    parser.add_argument("--close-delay", type=float, default=0.35, help="每次夹爪闭合后的等待秒数")
    parser.add_argument("--settle-delay", type=float, default=0.8, help="夹紧/试提后的稳定等待秒数")
    parser.add_argument("--grip-only", action="store_true", help="只低夹力夹住杯子，不做试提/抬高")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    parser.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    execute = args.confirm_motion == CONFIRM

    if execute:
        print("即将真实控制夹爪和机械臂：请确认玻璃杯位于两指中间，急停可触达。", file=sys.stderr)
    else:
        print("dry-run：只读取当前位姿并预览夹爪/抬高计划，不发送运动命令。", file=sys.stderr)

    try:
        with AuboSdkClient(config) as client:
            result = run_action(
                "pick_glass",
                client,
                execute=execute,
                force=args.force,
                close_position=args.close_position,
                lift=args.lift,
                test_lift=args.test_lift,
                linear_acc=args.linear_acc,
                linear_vel=args.linear_vel,
                close_delay=args.close_delay,
                settle_delay=args.settle_delay,
                grip_only=args.grip_only,
                device=args.modbus_device,
                slave=args.slave,
                function_code=args.function_code,
            )
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
