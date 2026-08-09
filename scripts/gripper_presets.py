#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.gripper import CONFIRM, LebaiGripper, feedback_value
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


DEFAULT_PRESET_PATH = PROJECT_ROOT / "config" / "gripper_presets.json"


def load_presets(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"presets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_presets(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="保存和复用乐白夹爪标定参数")
    parser.add_argument("command", choices=["save", "list", "show", "apply", "delete"])
    parser.add_argument("name", nargs="?", help="预设名称，例如 glass_good")
    parser.add_argument("--config", help="robot.local.json 路径")
    parser.add_argument("--preset-file", default=str(DEFAULT_PRESET_PATH), help="预设保存 JSON 路径")
    parser.add_argument("--force", type=int, default=None, help="保存/应用时使用的夹力 0-100")
    parser.add_argument("--width", type=int, default=None, help="手动指定保存/应用的 width 0-100；不填则保存当前反馈 position")
    parser.add_argument("--note", default="", help="保存备注")
    parser.add_argument("--command-delay", type=float, default=0.3, help="apply 时连续写寄存器之间的等待秒数")
    parser.add_argument("--tolerance", type=int, default=3, help="apply 时位置反馈允许误差")
    parser.add_argument("--confirm-motion", default="", help=f"apply 真实控制必须填入: {CONFIRM}")
    return parser


def require_name(args) -> str:
    if not args.name:
        raise ValueError("这个命令需要预设名称")
    return args.name


def main() -> int:
    args = build_parser().parse_args()
    preset_path = Path(args.preset_file).expanduser()
    data = load_presets(preset_path)
    presets = data.setdefault("presets", {})

    try:
        if args.command == "list":
            print(json.dumps({"preset_file": str(preset_path), "names": sorted(presets)}, ensure_ascii=False, indent=2))
            return 0

        name = require_name(args)

        if args.command == "show":
            if name not in presets:
                raise ValueError(f"未找到预设: {name}")
            print(json.dumps(presets[name], ensure_ascii=False, indent=2))
            return 0

        if args.command == "delete":
            if name not in presets:
                raise ValueError(f"未找到预设: {name}")
            removed = presets.pop(name)
            save_presets(preset_path, data)
            print(json.dumps({"deleted": name, "preset": removed}, ensure_ascii=False, indent=2))
            return 0

        config = load_config(args.config)
        with AuboSdkClient(config) as client:
            gripper = LebaiGripper(client)

            if args.command == "save":
                feedback = gripper.feedback()
                width = args.width
                if width is None:
                    width = feedback_value(feedback, "position")
                if width is None:
                    raise ValueError("无法从 feedback 读取 position，请用 --width 手动指定")
                preset = {
                    "name": name,
                    "width": int(max(0, min(100, width))),
                    "force": None if args.force is None else int(max(0, min(100, args.force))),
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                    "note": args.note,
                    "feedback": feedback,
                }
                presets[name] = preset
                save_presets(preset_path, data)
                print(json.dumps({"saved": name, "preset_file": str(preset_path), "preset": preset}, ensure_ascii=False, indent=2))
                return 0

            if args.command == "apply":
                if name not in presets:
                    raise ValueError(f"未找到预设: {name}")
                preset = presets[name]
                width = args.width if args.width is not None else int(preset["width"])
                force = args.force if args.force is not None else preset.get("force")
                execute = args.confirm_motion == CONFIRM
                if not execute:
                    print("DRY-RUN：只预览预设应用，不写夹爪寄存器。", file=sys.stderr)
                result = gripper.move(
                    width,
                    force=None if force is None else int(force),
                    execute=execute,
                    command_delay_s=args.command_delay,
                    position_tolerance=args.tolerance,
                    function_code=0x06,
                )
                print(json.dumps({"applied": name, "preset": preset, "result": result}, ensure_ascii=False, indent=2))
                return 0

        raise ValueError(f"未知命令: {args.command}")
    except (AuboSdkError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
