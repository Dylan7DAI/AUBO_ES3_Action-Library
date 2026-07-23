#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AUBO 项目的唯一根目录入口。

无参数运行时显示中文菜单，由操作者选择只读诊断、安全小步测试、点头
动作或水杯往返动作。本文件只负责交互和参数转发；设备连接、安全校验、
轨迹生成及 SDK 调用仍分别由 ``host_tools`` 和 ``src/aubo_sdk_client`` 完成。

也支持命令行转发，例如：``python main.py diagnose --json``。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence

from host_tools.cup_action import (
    CONFIRMATION_TEXT as CUP_CONFIRMATION_TEXT,
    main as cup_main,
)
from host_tools.diagnose_robot import main as diagnose_main
from host_tools.nod_action import (
    CONFIRMATION_TEXT as NOD_CONFIRMATION_TEXT,
    main as nod_main,
)
from host_tools.safe_joint_test import (
    CONFIRMATION_TEXT as JOINT_CONFIRMATION_TEXT,
    main as joint_main,
)

InputFunction = Callable[[str], str]


def configure_windows_console() -> None:
    """在 Windows 控制台中启用 UTF-8，避免中文菜单乱码。"""

    if os.name != "nt":
        return
    os.system("chcp 65001 >nul")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                # IDE 或测试框架提供的替代输出流可能不支持重新配置。
                pass


def print_help() -> None:
    """打印统一入口支持的子命令和常用示例。"""

    print(
        """用法：
  python main.py                         打开交互菜单
  python main.py diagnose [参数]         只读诊断并读取当前位姿
  python main.py joint [参数]            小范围关节测试
  python main.py nod [参数]              点头动作
  python main.py cup [参数]              水杯往返动作

常用示例：
  python main.py diagnose --json
  python main.py joint --delta 0,0,0,0,0,0.02
  python main.py nod --scale 1.0
  python main.py cup --cup-hold 2.0

各子命令的完整参数：
  python main.py diagnose --help
  python main.py joint --help
  python main.py nod --help
  python main.py cup --help
"""
    )


def dispatch_cli(argv: Sequence[str]) -> int:
    """把统一入口后的参数转交给对应的 ``host_tools`` 工具。"""

    if not argv or argv[0] in {"-h", "--help", "help"}:
        print_help()
        return 0

    command, *tool_args = argv
    tools = {
        "diagnose": diagnose_main,
        "joint": joint_main,
        "nod": nod_main,
        "cup": cup_main,
    }
    tool = tools.get(command.lower())
    if tool is None:
        print(f"未知功能：{command}", file=sys.stderr)
        print("运行 python main.py --help 查看可用功能。", file=sys.stderr)
        return 2
    return int(tool(tool_args))


def _prompt(input_fn: InputFunction, message: str, default: str) -> str:
    """读取一项菜单参数；直接回车时返回显示的默认值。"""

    value = input_fn(f"{message} [默认 {default}]：").strip()
    return value or default


def _confirm_motion(
    input_fn: InputFunction,
    action_name: str,
    dry_run_description: str,
) -> bool:
    """显示统一真机安全检查；只有输入 ``MOVE`` 才返回执行许可。"""

    print()
    print(f"当前选择：{action_name}")
    print(f"直接回车：{dry_run_description}")
    print("输入 MOVE：允许进入真机运动流程。")
    print("真机执行前必须确认：")
    print("  1. 工作空间内无人且无障碍物；")
    print("  2. 工具、夹具、桌面和线缆不会发生碰撞；")
    print("  3. 急停按钮保持可达；")
    print("  4. config/robot.local.json 中的软限位已经按现场核实。")
    answer = input_fn("请选择，直接回车或输入 MOVE：").strip().upper()
    if answer == "MOVE":
        return True
    if answer:
        print("未识别为 MOVE，将按非运动模式继续。")
    return False


def _run_diagnose_menu(input_fn: InputFunction) -> int:
    """收集只读诊断选项并调用诊断工具。"""

    output_json = input_fn("是否输出 JSON？输入 Y 确认，直接回车使用普通文本：").strip()
    args = ["--json"] if output_json.upper() == "Y" else []
    return diagnose_main(args)


def _run_joint_menu(input_fn: InputFunction) -> int:
    """收集关节增量，并根据确认结果选择预览或真实执行。"""

    delta = _prompt(input_fn, "输入 J1～J6 相对增量(rad)，用逗号分隔", "0,0,0,0,0,0.02")
    execute = _confirm_motion(
        input_fn,
        "小范围关节测试",
        "只读连接机器人、读取当前位置并预览目标，不上电、不运动",
    )
    args = ["--delta", delta]
    if execute:
        args.extend(["--execute", "--confirm-motion", JOINT_CONFIRMATION_TEXT])
    return joint_main(args)


def _run_nod_menu(input_fn: InputFunction) -> int:
    """收集点头风格和幅度，并调用点头轨迹工具。"""

    print("点头风格：1=J1～J6 全身协调；2=单腕小幅动作")
    style_choice = input_fn("请选择 [默认 1]：").strip() or "1"
    args: list[str]
    if style_choice == "2":
        joint = _prompt(input_fn, "动作关节编号", "5")
        amplitude = _prompt(input_fn, "单腕动作幅度(rad)", "0.04")
        count = _prompt(input_fn, "动作次数", "1")
        args = [
            "--style",
            "wrist",
            "--joint",
            joint,
            "--amplitude",
            amplitude,
            "--count",
            count,
        ]
    else:
        if style_choice != "1":
            print("未识别该风格，将使用默认的全身协调点头。")
        scale = _prompt(input_fn, "全身动作倍率", "1.0")
        count = _prompt(input_fn, "动作次数", "1")
        args = ["--style", "full-body", "--scale", scale, "--count", count]

    execute = _confirm_motion(input_fn, "点头动作", "使用全零示例姿态离线生成轨迹，不连接机器人")
    if execute:
        args.extend(["--execute", "--confirm-motion", NOD_CONFIRMATION_TEXT])
    return nod_main(args)


def _run_cup_menu(input_fn: InputFunction) -> int:
    """收集水杯停顿时间，并调用两步往返动作工具。"""

    cup_hold = _prompt(input_fn, "到达水杯位置后的停顿时间(s)", "2.0")
    execute = _confirm_motion(
        input_fn,
        "水杯位置往返",
        "使用全零示例姿态离线检查两步计划，不连接机器人",
    )
    args = ["--cup-hold", cup_hold]
    if execute:
        args.extend(["--execute", "--confirm-motion", CUP_CONFIRMATION_TEXT])
    return cup_main(args)


def run_interactive(input_fn: InputFunction = input) -> int:
    """显示一次主菜单，并执行操作者选择的功能。"""

    configure_windows_console()
    print("=" * 68)
    print("AUBO ES3 统一控制入口")
    print("=" * 68)
    print("1. 只读连接诊断 + 当前位姿读取（关节角和 TCP 位姿，不发送运动命令）")
    print("2. 小范围关节测试（默认只读连接并预览，不运动）")
    print("3. 点头动作（默认完全离线预演）")
    print("4. 水杯位置往返（默认完全离线预演）")
    print("0. 退出")
    print()

    choice = input_fn("请输入功能编号：").strip()
    actions = {
        "1": _run_diagnose_menu,
        "2": _run_joint_menu,
        "3": _run_nod_menu,
        "4": _run_cup_menu,
    }
    if choice == "0":
        print("已退出，未连接或移动机械臂。")
        return 0
    action = actions.get(choice)
    if action is None:
        print("无效选择，未执行任何操作。", file=sys.stderr)
        return 1
    return action(input_fn)


def main(argv: Sequence[str] | None = None) -> int:
    """无参数时打开菜单，有参数时按统一子命令进行转发。"""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        configure_windows_console()
        return dispatch_cli(arguments)
    return run_interactive()


if __name__ == "__main__":
    interactive = len(sys.argv) == 1
    try:
        exit_code = main()
    except KeyboardInterrupt:
        print("\n已由用户取消。若机械臂仍在运动，请立即使用控制器停止或急停。", file=sys.stderr)
        exit_code = 130
    except Exception as exc:
        print(f"未处理错误：{exc}", file=sys.stderr)
        exit_code = 2

    # 双击 main.py 时保留结果窗口；命令行子命令执行后不额外等待。
    if interactive:
        try:
            input("\n按回车键关闭窗口……")
        except EOFError:
            pass
    raise SystemExit(exit_code)
