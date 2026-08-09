#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.emergency_stop import EmergencyStopMonitor
from aubo_es3_actions.sdk_client import AuboSdkClient


STOP_FILE = ROOT / ".runtime" / "llm_expressive_emergency.stop"
CLEAR_CONFIRMATION = "I_CONFIRMED_THE_PHYSICAL_AREA_IS_SAFE"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="锁存LLM情感动作软件急停；不能替代实体急停"
    )
    parser.add_argument("--direct", action="store_true", help="同时连接控制器调用stopJoint/stopLine")
    parser.add_argument("--config", default="config/robot.curiosity_fast.json")
    parser.add_argument("--clear", action="store_true", help="清除已锁存的软件急停")
    parser.add_argument("--confirm-clear", default="")
    args = parser.parse_args()

    if args.clear:
        if args.confirm_clear != CLEAR_CONFIRMATION:
            print(
                "拒绝清除。确认实体急停状态、人员和机械臂现场安全后添加：\n"
                f"--confirm-clear {CLEAR_CONFIRMATION}",
                file=sys.stderr,
            )
            return 2
        STOP_FILE.unlink(missing_ok=True)
        print(f"已清除软件急停锁存：{STOP_FILE}")
        return 0

    monitor = EmergencyStopMonitor(STOP_FILE)
    monitor.request("manual emergency stop command")
    print(f"已锁存软件急停：{STOP_FILE}")

    if args.direct:
        try:
            config = load_config(args.config)
            with AuboSdkClient(config) as client:
                client.stop_motion()
            print("已向控制器发送stopJoint/stopLine。")
        except Exception as exc:
            print(
                f"直接SDK停止失败：{exc}。请立即使用实体急停。",
                file=sys.stderr,
            )
            return 8
    else:
        print("运行中的情感动作程序将由20ms监视器调用SDK停止。")
    print("软件停止不具备安全等级，异常运动时必须使用实体急停。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

