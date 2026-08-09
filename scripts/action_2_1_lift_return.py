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
    parser = argparse.ArgumentParser(description="动作 2.1: 当前夹爪状态下上移后仰，停顿后原路回原位")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--lift", type=float, default=None, help="兼容参数: 总上移距离 m；默认由 vertical-lift + arc-lift 决定")
    parser.add_argument("--vertical-lift", type=float, default=0.05, help="先垂直上移距离 m")
    parser.add_argument("--arc-lift", type=float, default=0.05, help="弧线段继续上移距离 m")
    parser.add_argument("--back-offset", type=float, default=0.10, help="弧线段向后偏移距离 m")
    parser.add_argument("--back-axis", choices=("x", "y"), default="x", help="向后使用的基坐标轴")
    parser.add_argument("--back-sign", type=int, choices=(-1, 1), default=-1, help="向后方向: -1 为坐标负向，1 为坐标正向")
    parser.add_argument("--arc-segments", type=int, default=4, help="弧线段 waypoint 数，越大越平滑")
    parser.add_argument("--pause", type=float, default=3.0, help="上方停顿秒数")
    parser.add_argument("--linear-acc", type=float, default=0.03, help="线加速度 m/s^2")
    parser.add_argument("--linear-vel", type=float, default=0.01, help="线速度 m/s")
    parser.add_argument("--move-timeout", type=float, default=20.0, help="等待每段 TCP 运动到位的最长秒数")
    parser.add_argument("--position-tolerance", type=float, default=0.005, help="TCP xyz 到位误差 m")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    execute = args.confirm_motion == CONFIRM
    if execute:
        print("即将真实移动机械臂：当前夹爪状态保持不变，上移后仰后原路返回原位。", file=sys.stderr)
    else:
        print("dry-run：只读取当前位姿/夹爪反馈并检查逆解，不发送运动命令。", file=sys.stderr)

    config = load_config(args.config)
    try:
        with AuboSdkClient(config) as client:
            result = run_action(
                "2.1",
                client,
                execute=execute,
                lift=args.lift,
                vertical_lift=args.vertical_lift,
                arc_lift=args.arc_lift,
                back_offset=args.back_offset,
                back_axis=args.back_axis,
                back_sign=args.back_sign,
                arc_segments=args.arc_segments,
                pause=args.pause,
                linear_acc=args.linear_acc,
                linear_vel=args.linear_vel,
                move_timeout=args.move_timeout,
                position_tolerance=args.position_tolerance,
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
