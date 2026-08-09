#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.gripper import CONFIRM, LebaiGripper, feedback_value
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


DEFAULT_PRESET_PATH = PROJECT_ROOT / "config" / "gripper_presets.json"

HELP = """
玻璃杯夹爪调参

o / c    打开到 100 / 闭合到 0
[ / ]    width 减/加小步
{ / }    width 减/加大步
- / +    force 减/加
m        执行当前 width/force
p        读取反馈
v        保存当前反馈为预设
h        显示帮助
q        退出
"""


def clamp(value: int) -> int:
    return max(0, min(100, int(value)))


def load_presets(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"presets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_presets(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def show_result(result: Any) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def save_current_preset(path: Path, name: str, width: int, force: int, feedback: Dict[str, Any], note: str) -> Dict[str, Any]:
    data = load_presets(path)
    presets = data.setdefault("presets", {})
    actual_width = feedback_value(feedback, "position")
    torque = feedback_value(feedback, "torque")
    preset = {
        "name": name,
        "command_width": clamp(width),
        "feedback_width": None if actual_width is None else clamp(actual_width),
        "width": clamp(actual_width if actual_width is not None else width),
        "force": clamp(force),
        "torque": torque,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "feedback": feedback,
    }
    presets[name] = preset
    save_presets(path, data)
    return {"saved": name, "preset_file": str(path), "preset": preset}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="交互式调节乐白夹爪，夹稳杯子后一键保存参数")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--preset-file", default=str(DEFAULT_PRESET_PATH), help="预设保存 JSON 路径")
    parser.add_argument("--name", default="glass_good", help="保存预设名称")
    parser.add_argument("--note", default="glass cup tuned by keyboard", help="保存备注")
    parser.add_argument("--width", type=int, default=70, help="初始 width 0-100，0 闭合，100 打开")
    parser.add_argument("--force", type=int, default=15, help="初始 force 0-100")
    parser.add_argument("--small-step", type=int, default=2, help="width 小步调节")
    parser.add_argument("--large-step", type=int, default=10, help="width 大步调节")
    parser.add_argument("--force-step", type=int, default=2, help="force 调节步长")
    parser.add_argument("--command-delay", type=float, default=0.3, help="写 force/width 之间的等待秒数")
    parser.add_argument("--tolerance", type=int, default=3, help="位置反馈允许误差")
    parser.add_argument("--verify-timeout", type=float, default=4.0, help="执行后读取反馈验证的最长等待秒数")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写夹爪寄存器")
    parser.add_argument("--confirm-motion", default="", help=f"真实控制必须填入: {CONFIRM}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    execute = not args.dry_run and args.confirm_motion == CONFIRM
    if not execute:
        print("DRY-RUN：只打印命令，不写夹爪寄存器。")
        print(f"真实调参需要添加: --confirm-motion {CONFIRM}")

    width = clamp(args.width)
    force = clamp(args.force)
    preset_path = Path(args.preset_file).expanduser()
    old_settings = termios.tcgetattr(sys.stdin)

    config = load_config(args.config)
    print(HELP)
    print(f"width={width}, force={force}, preset={args.name}")

    try:
        with AuboSdkClient(config) as client:
            gripper = LebaiGripper(client)
            tty.setcbreak(sys.stdin.fileno())
            while True:
                key = sys.stdin.read(1)
                try:
                    if key == "q":
                        print("\n退出调参")
                        break
                    if key == "h":
                        print(HELP)
                    elif key == "[":
                        width = clamp(width - int(args.small_step))
                        print(f"\nwidth={width}")
                    elif key == "]":
                        width = clamp(width + int(args.small_step))
                        print(f"\nwidth={width}")
                    elif key == "{":
                        width = clamp(width - int(args.large_step))
                        print(f"\nwidth={width}")
                    elif key == "}":
                        width = clamp(width + int(args.large_step))
                        print(f"\nwidth={width}")
                    elif key == "-":
                        force = clamp(force - int(args.force_step))
                        print(f"\nforce={force}")
                    elif key in ("+", "="):
                        force = clamp(force + int(args.force_step))
                        print(f"\nforce={force}")
                    elif key == "o":
                        width = 100
                        print("\n打开到 100")
                        show_result(gripper.move(width, force=force, execute=execute, command_delay_s=args.command_delay, position_tolerance=args.tolerance, verify_timeout_s=args.verify_timeout))
                    elif key == "c":
                        width = 0
                        print("\n闭合到 0")
                        show_result(gripper.move(width, force=force, execute=execute, command_delay_s=args.command_delay, position_tolerance=args.tolerance, verify_timeout_s=args.verify_timeout))
                    elif key == "m":
                        print(f"\n执行 width={width}, force={force}")
                        show_result(gripper.move(width, force=force, execute=execute, command_delay_s=args.command_delay, position_tolerance=args.tolerance, verify_timeout_s=args.verify_timeout))
                    elif key == "p":
                        print("\n反馈")
                        show_result(gripper.feedback())
                    elif key == "v":
                        feedback = gripper.feedback()
                        print(f"\n保存预设 {args.name}")
                        show_result(save_current_preset(preset_path, args.name, width, force, feedback, args.note))
                except (AuboSdkError, ValueError) as exc:
                    print(f"\n错误: {exc}")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
