#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".runtime"

STOP_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.stop"
)

PID_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.pid"
)

READY_FILE = (
    RUNTIME_DIR
    / "anticipation_breathing.ready"
)

LOG_FILE = (
    ROOT
    / "logs"
    / "anticipation_breathing.log"
)


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    return True


def read_pid() -> Optional[int]:
    try:
        return int(
            PID_FILE.read_text(
                encoding="utf-8"
            ).strip()
        )
    except (
        OSError,
        ValueError,
    ):
        return None


def clean_runtime_files() -> None:
    for path in (
        READY_FILE,
        PID_FILE,
        STOP_FILE,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "停止腕部呼吸循环，"
            "等待当前一轮结束并回到低头中心。"
        )
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="最长等待秒数，默认10秒",
    )

    args = parser.parse_args()

    if not 3.0 <= args.timeout <= 30.0:
        print(
            "timeout必须在3秒到30秒之间。"
        )
        return 1

    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pid = read_pid()

    if pid is None:
        print("当前没有检测到腕部呼吸进程。")
        clean_runtime_files()
        return 0

    if not process_is_running(pid):
        print(
            "检测到失效的呼吸进程记录，"
            "已清理。"
        )
        clean_runtime_files()
        return 0

    STOP_FILE.write_text(
        "stop\n",
        encoding="utf-8",
    )

    print(
        "已发送停止信号，"
        "等待当前呼吸循环结束并回中……"
    )

    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        if not process_is_running(pid):
            clean_runtime_files()
            print(
                "腕部呼吸动作已停止，"
                "机器人连接已释放。"
            )
            return 0

        time.sleep(0.10)

    print()
    print(
        "等待停止超时，"
        "暂时不要启动下一条机械臂运动指令。"
    )
    print(f"请查看日志：{LOG_FILE}")
    print(
        "机械臂仍在异常运动时，"
        "请按实体Stop按钮。"
    )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

