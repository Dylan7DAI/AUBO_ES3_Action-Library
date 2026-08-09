#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from aubo_es3_actions import ACTIONS, load_config, run_action
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError, JOINT_NAMES


CONFIRM = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AUBO ES3 机械臂动作库统一入口")
    parser.add_argument("--config", help="robot.local.json 路径")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="列出动作库中的动作")
    sub.add_parser("current_joints", help="读取当前关节角")
    sub.add_parser("current_pose", help="读取当前 TCP 位姿")

    jog = sub.add_parser("jog_joint", help="相对当前姿态小步移动一个关节")
    jog.add_argument("--joint", type=int, required=True, help="关节编号 0-5")
    jog.add_argument("--delta", type=float, required=True, help="关节增量(rad)")
    jog.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    tcp = sub.add_parser("jog_tcp", help="相对当前 TCP 位姿小步移动末端")
    tcp.add_argument("--axis", type=int, required=True, help="TCP 轴编号 0-5: x,y,z,rx,ry,rz")
    tcp.add_argument("--delta", type=float, required=True, help="TCP 增量，平移单位 m，姿态单位 rad")
    tcp.add_argument("--linear-acc", type=float, default=0.05, help="线加速度 m/s^2")
    tcp.add_argument("--linear-vel", type=float, default=0.02, help="线速度 m/s")
    tcp.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    grip_pos = sub.add_parser("gripper_position", help="设置夹爪开合位置 0-100")
    grip_pos.add_argument("--position", type=int, required=True, help="0 全闭，100 全开")
    grip_pos.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_pos.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_pos.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_pos.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_force = sub.add_parser("gripper_force", help="设置夹爪力度 0-100")
    grip_force.add_argument("--force", type=int, required=True, help="夹爪力度百分比 0-100")
    grip_force.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_force.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_force.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_force.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_speed = sub.add_parser("gripper_speed", help="设置夹爪速度 0-100")
    grip_speed.add_argument("--speed", type=int, required=True, help="夹爪速度百分比 0-100")
    grip_speed.add_argument("--persist", action="store_true", help="写入断电保留速度寄存器")
    grip_speed.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_speed.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_speed.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_speed.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_move = sub.add_parser("gripper_move", help="一次性设置夹爪幅度/力度/速度")
    grip_move.add_argument("--width", type=int, required=True, help="夹爪幅度 0-100，0 全闭，100 全开")
    grip_move.add_argument("--force", type=int, default=-1, help="可选力度 0-100")
    grip_move.add_argument("--speed", type=int, default=-1, help="可选速度 0-100")
    grip_move.add_argument("--wait", action="store_true", help="等待夹爪 done 状态")
    grip_move.add_argument("--command-delay", type=float, default=0.3, help="连续写寄存器之间的等待秒数")
    grip_move.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    grip_move.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    grip_move.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    grip_move.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_move.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_move.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_move.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_feedback = sub.add_parser("gripper_feedback", help="读取夹爪当前位置/力矩/done 状态")
    grip_feedback.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_feedback.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")

    grip_stroke = sub.add_parser("gripper_find_stroke", help="触发夹爪找行程")
    grip_stroke.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_stroke.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_stroke.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_stroke.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_disable = sub.add_parser("gripper_disable_auto_find_stroke", help="关闭夹爪自动找行程")
    grip_disable.add_argument("--persist", action="store_true", help="关闭并断电保存")
    grip_disable.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_disable.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    grip_disable.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    grip_disable.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    grip_status = sub.add_parser("gripper_status", help="查看夹爪 Modbus 配置/状态")
    grip_status.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    grip_status.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")

    action_10 = sub.add_parser("1.0", help="夹爪从闭合变成最大张开")
    action_10.add_argument("--force", type=int, default=-1, help="可选力度 0-100")
    action_10.add_argument("--speed", type=int, default=-1, help="可选速度 0-100")
    action_10.add_argument("--wait", action="store_true", help="等待夹爪 done 状态")
    action_10.add_argument("--command-delay", type=float, default=0.3, help="连续写寄存器之间的等待秒数")
    action_10.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    action_10.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    action_10.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    action_10.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    action_10.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    action_10.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    action_10.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    open_gripper = sub.add_parser("open_gripper", help="同 1.0: 夹爪最大张开")
    open_gripper.add_argument("--force", type=int, default=-1, help="可选力度 0-100")
    open_gripper.add_argument("--speed", type=int, default=-1, help="可选速度 0-100")
    open_gripper.add_argument("--wait", action="store_true", help="等待夹爪 done 状态")
    open_gripper.add_argument("--command-delay", type=float, default=0.3, help="连续写寄存器之间的等待秒数")
    open_gripper.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    open_gripper.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    open_gripper.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    open_gripper.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    open_gripper.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    open_gripper.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    open_gripper.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    action_20 = sub.add_parser("2.0", help="回到保存的夹取成功位，并恢复夹爪参数")
    action_20.add_argument("--preset", default="glass_good", help="夹取成功预设名")
    action_20.add_argument("--preset-file", default="", help="预设 JSON 路径")
    action_20.add_argument("--no-gripper", action="store_true", help="只回机械臂位姿，不恢复夹爪")
    action_20.add_argument("--gripper-before-motion", action="store_true", help="先恢复夹爪，再移动机械臂")
    action_20.add_argument("--move-timeout", type=float, default=60.0, help="等待每段关节运动到位的最长秒数")
    action_20.add_argument("--joint-tolerance", type=float, default=0.01, help="关节到位误差 rad")
    action_20.add_argument("--command-delay", type=float, default=0.3, help="夹爪 force/width 写入间隔秒数")
    action_20.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    action_20.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    action_20.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    return_grasp = sub.add_parser("return_grasp_pose", help="同 2.0: 回到保存的夹取成功位")
    return_grasp.add_argument("--preset", default="glass_good", help="夹取成功预设名")
    return_grasp.add_argument("--preset-file", default="", help="预设 JSON 路径")
    return_grasp.add_argument("--no-gripper", action="store_true", help="只回机械臂位姿，不恢复夹爪")
    return_grasp.add_argument("--gripper-before-motion", action="store_true", help="先恢复夹爪，再移动机械臂")
    return_grasp.add_argument("--move-timeout", type=float, default=60.0, help="等待每段关节运动到位的最长秒数")
    return_grasp.add_argument("--joint-tolerance", type=float, default=0.01, help="关节到位误差 rad")
    return_grasp.add_argument("--command-delay", type=float, default=0.3, help="夹爪 force/width 写入间隔秒数")
    return_grasp.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    return_grasp.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    return_grasp.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    action_21 = sub.add_parser("2.1", help="夹取: 先上移，再弧线向上向后，停顿后原路放回")
    action_21.add_argument("--lift", type=float, default=None, help="兼容参数: 总上移距离 m；默认由 vertical-lift + arc-lift 决定")
    action_21.add_argument("--vertical-lift", type=float, default=0.05, help="先垂直上移距离 m")
    action_21.add_argument("--arc-lift", type=float, default=0.05, help="弧线段继续上移距离 m")
    action_21.add_argument("--back-offset", type=float, default=0.10, help="弧线段向后偏移距离 m")
    action_21.add_argument("--back-axis", choices=("x", "y"), default="x", help="向后使用的基坐标轴")
    action_21.add_argument("--back-sign", type=int, choices=(-1, 1), default=-1, help="向后方向: -1 为坐标负向，1 为坐标正向")
    action_21.add_argument("--arc-segments", type=int, default=4, help="弧线段 waypoint 数，越大越平滑")
    action_21.add_argument("--pause", type=float, default=3.0, help="上方停顿秒数")
    action_21.add_argument("--linear-acc", type=float, default=0.03, help="线加速度 m/s^2")
    action_21.add_argument("--linear-vel", type=float, default=0.01, help="线速度 m/s")
    action_21.add_argument("--move-timeout", type=float, default=20.0, help="等待每段 TCP 运动到位的最长秒数")
    action_21.add_argument("--position-tolerance", type=float, default=0.005, help="TCP xyz 到位误差 m")
    action_21.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    action_21.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    action_21.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    lift_return = sub.add_parser("lift_return", help="同 2.1: 当前夹爪状态下上移后仰、停顿、回原位")
    lift_return.add_argument("--lift", type=float, default=None, help="兼容参数: 总上移距离 m；默认由 vertical-lift + arc-lift 决定")
    lift_return.add_argument("--vertical-lift", type=float, default=0.05, help="先垂直上移距离 m")
    lift_return.add_argument("--arc-lift", type=float, default=0.05, help="弧线段继续上移距离 m")
    lift_return.add_argument("--back-offset", type=float, default=0.10, help="弧线段向后偏移距离 m")
    lift_return.add_argument("--back-axis", choices=("x", "y"), default="x", help="向后使用的基坐标轴")
    lift_return.add_argument("--back-sign", type=int, choices=(-1, 1), default=-1, help="向后方向: -1 为坐标负向，1 为坐标正向")
    lift_return.add_argument("--arc-segments", type=int, default=4, help="弧线段 waypoint 数，越大越平滑")
    lift_return.add_argument("--pause", type=float, default=3.0, help="上方停顿秒数")
    lift_return.add_argument("--linear-acc", type=float, default=0.03, help="线加速度 m/s^2")
    lift_return.add_argument("--linear-vel", type=float, default=0.01, help="线速度 m/s")
    lift_return.add_argument("--move-timeout", type=float, default=20.0, help="等待每段 TCP 运动到位的最长秒数")
    lift_return.add_argument("--position-tolerance", type=float, default=0.005, help="TCP xyz 到位误差 m")
    lift_return.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    lift_return.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    lift_return.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    action_30 = sub.add_parser("3.0", help="松爪并回到收缩位姿")
    action_30.add_argument("--pose", default="tucked", help="收缩位姿名")
    action_30.add_argument("--pose-file", default="", help="机械臂位姿 JSON 路径")
    action_30.add_argument("--open-width", type=int, default=100, help="松爪张开幅度 0-100")
    action_30.add_argument("--open-force", type=int, default=15, help="松爪时夹爪力度 0-100")
    action_30.add_argument("--release-delay", type=float, default=1.0, help="松爪后等待秒数")
    action_30.add_argument("--move-timeout", type=float, default=30.0, help="等待关节运动到位的最长秒数")
    action_30.add_argument("--joint-tolerance", type=float, default=0.01, help="关节到位误差 rad")
    action_30.add_argument("--command-delay", type=float, default=0.3, help="夹爪 force/width 写入间隔秒数")
    action_30.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    action_30.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    action_30.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    release_tuck = sub.add_parser("release_and_tuck", help="同 3.0: 松爪并回到收缩位姿")
    release_tuck.add_argument("--pose", default="tucked", help="收缩位姿名")
    release_tuck.add_argument("--pose-file", default="", help="机械臂位姿 JSON 路径")
    release_tuck.add_argument("--open-width", type=int, default=100, help="松爪张开幅度 0-100")
    release_tuck.add_argument("--open-force", type=int, default=15, help="松爪时夹爪力度 0-100")
    release_tuck.add_argument("--release-delay", type=float, default=1.0, help="松爪后等待秒数")
    release_tuck.add_argument("--move-timeout", type=float, default=30.0, help="等待关节运动到位的最长秒数")
    release_tuck.add_argument("--joint-tolerance", type=float, default=0.01, help="关节到位误差 rad")
    release_tuck.add_argument("--command-delay", type=float, default=0.3, help="夹爪 force/width 写入间隔秒数")
    release_tuck.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    release_tuck.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    release_tuck.add_argument("--confirm-motion", default="", help=f"真实运动确认文本: {CONFIRM}")

    action_40 = sub.add_parser("4.0", help="夹爪从张开变成最小闭合")
    action_40.add_argument("--force", type=int, default=-1, help="可选力度 0-100")
    action_40.add_argument("--speed", type=int, default=-1, help="可选速度 0-100")
    action_40.add_argument("--wait", action="store_true", help="等待夹爪 done 状态")
    action_40.add_argument("--command-delay", type=float, default=0.3, help="连续写寄存器之间的等待秒数")
    action_40.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    action_40.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    action_40.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    action_40.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    action_40.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    action_40.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    action_40.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    close_gripper = sub.add_parser("close_gripper", help="同 4.0: 夹爪最小闭合")
    close_gripper.add_argument("--force", type=int, default=-1, help="可选力度 0-100")
    close_gripper.add_argument("--speed", type=int, default=-1, help="可选速度 0-100")
    close_gripper.add_argument("--wait", action="store_true", help="等待夹爪 done 状态")
    close_gripper.add_argument("--command-delay", type=float, default=0.3, help="连续写寄存器之间的等待秒数")
    close_gripper.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    close_gripper.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    close_gripper.add_argument("--no-verify", action="store_true", help="真实执行后不读取反馈验证")
    close_gripper.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    close_gripper.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    close_gripper.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    close_gripper.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    glass = sub.add_parser("pick_glass", help="低夹力夹取玻璃杯并沿基坐标 Z 方向抬高")
    glass.add_argument("--force", type=int, default=18, help="夹爪力度 0-100，玻璃杯建议从 15-20 开始")
    glass.add_argument("--close-position", type=int, default=55, help="最终夹爪位置 0-100，0 全闭，100 全开")
    glass.add_argument("--lift", type=float, default=0.05, help="最终抬高距离 m")
    glass.add_argument("--test-lift", type=float, default=0.005, help="正式抬高前的试提距离 m")
    glass.add_argument("--linear-acc", type=float, default=0.03, help="线加速度 m/s^2")
    glass.add_argument("--linear-vel", type=float, default=0.01, help="线速度 m/s")
    glass.add_argument("--close-delay", type=float, default=0.35, help="每次夹爪闭合后的等待秒数")
    glass.add_argument("--settle-delay", type=float, default=0.8, help="夹紧/试提后的稳定等待秒数")
    glass.add_argument("--grip-only", action="store_true", help="只低夹力夹住杯子，不做试提/抬高")
    glass.add_argument("--modbus-device", default="", help="AUBO 控制器里的 Modbus 设备名")
    glass.add_argument("--slave", type=int, default=0, help="Modbus 从机地址")
    glass.add_argument("--function-code", type=lambda x: int(x, 0), default=0x06, help="0x06 或 0x10")
    glass.add_argument("--confirm-motion", default="", help=f"真实动作确认文本: {CONFIRM}")

    sub.add_parser("keyboard", help="启动键盘控制")
    sub.add_parser("keyboard_tcp", help="启动末端位姿键盘控制")
    sub.add_parser("keyboard_gripper", help="启动夹爪键盘控制")
    return parser


def print_menu() -> None:
    print("=" * 64)
    print("AUBO ES3 机械臂动作库")
    print("=" * 64)
    print("1. 读取当前关节角")
    print("1.0. 夹爪从闭合变成最大张开 dry-run")
    print("2. 读取当前 TCP 位姿")
    print("2.0. 回到保存的夹取成功位 dry-run")
    print("2.1. 夹取: 上移后弧线向上向后，停顿后原路回原位 dry-run")
    print("3. 小步移动关节 dry-run")
    print("3.0. 松爪并回到收缩位姿 dry-run")
    print("4.0. 夹爪从张开变成最小闭合 dry-run")
    print("4. 关节键盘控制 dry-run")
    print("5. 末端位姿键盘控制 dry-run")
    print("6. 夹爪键盘控制 dry-run")
    print("7. 玻璃杯夹取抬高 dry-run")
    print("0. 退出")


def interactive(config_path: str | None) -> int:
    print_menu()
    choice = input("请输入功能编号: ").strip()
    if choice == "0":
        print("已退出，未发送运动命令。")
        return 0
    if choice == "4":
        cmd = [sys.executable, "scripts/keyboard_sdk_control.py"]
        if config_path:
            cmd.extend(["--config", config_path])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)
    if choice == "5":
        cmd = [sys.executable, "scripts/keyboard_tcp_control.py"]
        if config_path:
            cmd.extend(["--config", config_path])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)
    if choice == "6":
        cmd = [sys.executable, "scripts/keyboard_gripper_control.py"]
        if config_path:
            cmd.extend(["--config", config_path])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)

    config = load_config(config_path)
    with AuboSdkClient(config) as client:
        if choice == "1":
            result = run_action("current_joints", client)
        elif choice == "1.0":
            result = run_action("1.0", client, execute=False)
        elif choice == "2":
            result = run_action("current_pose", client)
        elif choice == "2.0":
            result = run_action("2.0", client, execute=False)
        elif choice == "2.1":
            result = run_action("2.1", client, execute=False)
        elif choice == "3":
            joint = int(input("关节编号 0-5: ").strip())
            delta = float(input("增量 rad，例如 0.02: ").strip())
            result = run_action("jog_joint", client, joint=joint, delta=delta, execute=False)
        elif choice == "3.0":
            result = run_action("3.0", client, execute=False)
        elif choice == "4.0":
            result = run_action("4.0", client, execute=False)
        elif choice == "7":
            result = run_action("pick_glass", client, execute=False)
        else:
            print("无效选择。", file=sys.stderr)
            return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        return interactive(args.config)
    if args.command == "keyboard":
        cmd = [sys.executable, "scripts/keyboard_sdk_control.py"]
        if args.config:
            cmd.extend(["--config", args.config])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)
    if args.command == "keyboard_tcp":
        cmd = [sys.executable, "scripts/keyboard_tcp_control.py"]
        if args.config:
            cmd.extend(["--config", args.config])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)
    if args.command == "keyboard_gripper":
        cmd = [sys.executable, "scripts/keyboard_gripper_control.py"]
        if args.config:
            cmd.extend(["--config", args.config])
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parent)
    if args.command == "list":
        print("\n".join(sorted(ACTIONS)))
        return 0

    config = load_config(args.config)
    kwargs = {}
    if args.command == "jog_joint":
        kwargs = {
            "joint": args.joint,
            "delta": args.delta,
            "execute": args.confirm_motion == CONFIRM,
        }
    elif args.command == "jog_tcp":
        kwargs = {
            "axis": args.axis,
            "delta": args.delta,
            "execute": args.confirm_motion == CONFIRM,
            "linear_acc": args.linear_acc,
            "linear_vel": args.linear_vel,
        }
    elif args.command == "gripper_position":
        kwargs = {
            "position": args.position,
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.command == "gripper_force":
        kwargs = {
            "force": args.force,
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.command == "gripper_speed":
        kwargs = {
            "speed": args.speed,
            "execute": args.confirm_motion == CONFIRM,
            "persist": args.persist,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.command == "gripper_move":
        kwargs = {
            "width": args.width,
            "force": args.force,
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
    elif args.command == "gripper_feedback":
        kwargs = {"device": args.modbus_device, "slave": args.slave}
    elif args.command == "gripper_find_stroke":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.command == "gripper_disable_auto_find_stroke":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "persist": args.persist,
            "device": args.modbus_device,
            "slave": args.slave,
            "function_code": args.function_code,
        }
    elif args.command == "gripper_status":
        kwargs = {"device": args.modbus_device, "slave": args.slave}
    elif args.command in ("1.0", "open_gripper", "4.0", "close_gripper"):
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "force": args.force,
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
    elif args.command in ("2.0", "return_grasp_pose"):
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
    elif args.command in ("2.1", "lift_return"):
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
    elif args.command in ("3.0", "release_and_tuck"):
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
    elif args.command == "pick_glass":
        kwargs = {
            "execute": args.confirm_motion == CONFIRM,
            "force": args.force,
            "close_position": args.close_position,
            "lift": args.lift,
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
            result = run_action(args.command, client, **kwargs)
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
