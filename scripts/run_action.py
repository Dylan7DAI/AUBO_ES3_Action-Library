#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import ACTIONS, load_config, run_action
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调用 AUBO ES3 动作库里的指定动作")
    parser.add_argument("action", choices=sorted(ACTIONS) + ["list"], help="动作名")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--joint", type=int, default=0, help="jog_joint 用: 关节编号 0-5")
    parser.add_argument("--delta", type=float, default=0.02, help="jog_joint 用: 移动弧度")
    parser.add_argument("--axis", type=int, default=0, help="jog_tcp 用: TCP 轴编号 0-5")
    parser.add_argument("--linear-acc", type=float, default=0.05, help="jog_tcp 用: 线加速度 m/s^2")
    parser.add_argument("--linear-vel", type=float, default=0.02, help="jog_tcp 用: 线速度 m/s")
    parser.add_argument("--pause", type=float, default=3.0, help="2.1/lift_return 用: 上方停顿秒数")
    parser.add_argument("--move-timeout", type=float, default=60.0, help="2.0/2.1/3.0 用: 等待每段运动到位的最长秒数")
    parser.add_argument("--position-tolerance", type=float, default=0.005, help="2.1/lift_return 用: TCP xyz 到位误差 m")
    parser.add_argument("--position", type=int, default=50, help="gripper_position 用: 夹爪开合 0-100")
    parser.add_argument("--width", type=int, default=50, help="gripper_move 用: 夹爪幅度 0-100")
    parser.add_argument("--preset", default="glass_good", help="2.0/return_grasp_pose 用: 夹取成功预设名")
    parser.add_argument("--preset-file", default="", help="2.0/return_grasp_pose 用: 预设 JSON 路径")
    parser.add_argument("--no-gripper", action="store_true", help="2.0/return_grasp_pose 用: 只回位姿，不恢复夹爪")
    parser.add_argument("--gripper-before-motion", action="store_true", help="2.0/return_grasp_pose 用: 先恢复夹爪再移动")
    parser.add_argument("--pose", default="tucked", help="3.0/release_and_tuck 用: 收缩位姿名")
    parser.add_argument("--pose-file", default="", help="3.0/release_and_tuck 用: 机械臂位姿 JSON 路径")
    parser.add_argument("--open-width", type=int, default=100, help="3.0/release_and_tuck 用: 松爪张开幅度")
    parser.add_argument("--open-force", type=int, default=15, help="3.0/release_and_tuck 用: 松爪力度")
    parser.add_argument("--release-delay", type=float, default=1.0, help="3.0/release_and_tuck 用: 松爪后等待秒数")
    parser.add_argument("--force", type=int, default=None, help="gripper_force/pick_glass/1.0/4.0 用: 夹爪力度 0-100")
    parser.add_argument("--speed", type=int, default=-1, help="gripper_speed/gripper_move/1.0/4.0 用: 夹爪速度 0-100")
    parser.add_argument("--persist", action="store_true", help="gripper_speed/disable_auto_find_stroke 用: 断电保存")
    parser.add_argument("--wait", action="store_true", help="gripper_move 用: 等待夹爪 done 状态")
    parser.add_argument("--command-delay", type=float, default=0.3, help="gripper_move 用: 连续写寄存器之间的等待秒数")
    parser.add_argument("--tolerance", type=int, default=3, help="gripper_move 用: 位置反馈允许误差")
    parser.add_argument("--verify-timeout", type=float, default=4.0, help="gripper_move 用: 执行后读取反馈验证的最长等待秒数")
    parser.add_argument("--no-verify", action="store_true", help="gripper_move 用: 真实执行后不读取反馈验证")
    parser.add_argument("--joint-tolerance", type=float, default=0.01, help="2.0/3.0 用: 关节到位误差 rad")
    parser.add_argument("--close-position", type=int, default=55, help="pick_glass 用: 最终夹爪位置 0-100")
    parser.add_argument("--lift", type=float, default=None, help="pick_glass/2.1 用: 抬高距离 m；2.1 中作为总上移距离兼容参数")
    parser.add_argument("--vertical-lift", type=float, default=0.05, help="2.1/lift_return 用: 先垂直上移距离 m")
    parser.add_argument("--arc-lift", type=float, default=0.05, help="2.1/lift_return 用: 弧线段继续上移距离 m")
    parser.add_argument("--back-offset", type=float, default=0.10, help="2.1/lift_return 用: 弧线段向后偏移距离 m")
    parser.add_argument("--back-axis", choices=("x", "y"), default="x", help="2.1/lift_return 用: 向后使用的基坐标轴")
    parser.add_argument("--back-sign", type=int, choices=(-1, 1), default=-1, help="2.1/lift_return 用: 向后方向")
    parser.add_argument("--arc-segments", type=int, default=4, help="2.1/lift_return 用: 弧线段 waypoint 数")
    parser.add_argument("--test-lift", type=float, default=0.005, help="pick_glass 用: 试提距离 m")
    parser.add_argument("--close-delay", type=float, default=0.35, help="pick_glass 用: 每次闭合后的等待秒数")
    parser.add_argument("--settle-delay", type=float, default=0.8, help="pick_glass 用: 夹紧/试提后的稳定等待秒数")
    parser.add_argument("--grip-only", action="store_true", help="pick_glass 用: 只夹住杯子，不做试提/抬高")
    parser.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    parser.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    parser.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="写寄存器功能码: 0x06 或 0x10")
    parser.add_argument(
        "--confirm-motion",
        default="",
        help=f"真实运动必须填入固定确认文本: {CONFIRM}",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.action == "list":
        print("\n".join(sorted(ACTIONS)))
        return 0

    config = load_config(args.config)
    kwargs = {}
    if args.action == "jog_joint":
        kwargs = {
            "joint": args.joint,
            "delta": args.delta,
            "execute": args.confirm_motion == CONFIRM,
        }
    elif args.action == "jog_tcp":
        kwargs = {
            "axis": args.axis,
            "delta": args.delta,
            "execute": args.confirm_motion == CONFIRM,
            "linear_acc": args.linear_acc,
            "linear_vel": args.linear_vel,
        }
    elif args.action in ("2.0", "return_grasp_pose"):
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "preset": args.preset,
            "preset_file": args.preset_file,
            "apply_gripper": not args.no_gripper,
            "gripper_after_motion": not args.gripper_before_motion,
            "move_timeout": args.move_timeout,
            "joint_tolerance": args.joint_tolerance,
            "command_delay": args.command_delay,
            "device": args.modbus_device,
            "slave": args.slave,
        }
    elif args.action in ("2.1", "lift_return"):
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "lift": args.lift,
            "vertical_lift": args.vertical_lift,
            "arc_lift": args.arc_lift,
            "back_offset": args.back_offset,
            "back_axis": args.back_axis,
            "back_sign": args.back_sign,
            "arc_segments": args.arc_segments,
            "pause": args.pause,
            "linear_acc": args.linear_acc,
            "linear_vel": args.linear_vel,
            "move_timeout": args.move_timeout,
            "position_tolerance": args.position_tolerance,
            "device": args.modbus_device,
            "slave": args.slave,
        }
    elif args.action in ("3.0", "release_and_tuck"):
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "pose": args.pose,
            "pose_file": args.pose_file,
            "open_width": args.open_width,
            "open_force": args.open_force,
            "release_delay": args.release_delay,
            "move_timeout": args.move_timeout,
            "joint_tolerance": args.joint_tolerance,
            "command_delay": args.command_delay,
            "device": args.modbus_device,
            "slave": args.slave,
        }
    elif args.action == "gripper_position":
        kwargs = {
            "position": args.position,
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_force":
        kwargs = {
            "force": 50 if args.force is None else args.force,
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_speed":
        kwargs = {
            "speed": args.speed,
            "execute": args.confirm_motion == CONFIRM,
            "persist": args.persist,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_move":
        kwargs = {
            "width": args.width,
            "force": -1 if args.force is None else args.force,
            "speed": args.speed,
            "execute": args.confirm_motion == CONFIRM,
            "wait": args.wait,
            "command_delay": args.command_delay,
            "tolerance": args.tolerance,
            "verify": not args.no_verify,
            "verify_timeout": args.verify_timeout,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_feedback":
        kwargs = {"device": args.modbus_device, "slave": args.slave}
    elif args.action == "gripper_find_stroke":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_disable_auto_find_stroke":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "persist": args.persist,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "gripper_status":
        kwargs = {"device": args.modbus_device, "slave": args.slave}
    elif args.action in ("1.0", "open_gripper", "4.0", "close_gripper"):
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "force": -1 if args.force is None else args.force,
            "speed": args.speed,
            "wait": args.wait,
            "command_delay": args.command_delay,
            "tolerance": args.tolerance,
            "verify": not args.no_verify,
            "verify_timeout": args.verify_timeout,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.action == "pick_glass":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "force": 18 if args.force is None else args.force,
            "close_position": args.close_position,
            "lift": 0.05 if args.lift is None else args.lift,
            "test_lift": args.test_lift,
            "linear_acc": args.linear_acc,
            "linear_vel": args.linear_vel,
            "close_delay": args.close_delay,
            "settle_delay": args.settle_delay,
            "grip_only": args.grip_only,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }

    try:
        with AuboSdkClient(config) as client:
            result = run_action(args.action, client, **kwargs)
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
