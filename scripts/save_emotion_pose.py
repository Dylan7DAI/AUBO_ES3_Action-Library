#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录：~/aubo_project1
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config, run_action
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError


def main() -> int:
    parser = argparse.ArgumentParser(description="保存当前机械臂情感动作点位")
    parser.add_argument("name", help="点位名称，例如 anticipation_ready")
    parser.add_argument(
        "--description",
        default="",
        help="点位说明",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="可选：机器人配置文件路径",
    )
    args = parser.parse_args()

    output_path = ROOT / "config" / "emotion_poses_new_es3.json"

    try:
        config = load_config(args.config)

        with AuboSdkClient(config) as client:
            joints = run_action("current_joints", client)
            pose_result = run_action("current_pose", client)

    except (AuboSdkError, ValueError, KeyError) as exc:
        print(f"读取机械臂失败：{exc}", file=sys.stderr)
        return 1

    tcp_pose = pose_result.get("tcp_pose")
    if not isinstance(tcp_pose, list) or len(tcp_pose) != 6:
        print("没有读取到有效的TCP位姿。", file=sys.stderr)
        return 2

    if output_path.exists():
        data = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        data = {"poses": {}}

    data.setdefault("poses", {})

    data["poses"][args.name] = {
        "name": args.name,
        "description": args.description,
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tcp_pose": tcp_pose,
        "joint_positions_rad": joints,
    }

    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"保存成功：{args.name}")
    print(f"文件：{output_path}")
    print(json.dumps(data["poses"][args.name], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())