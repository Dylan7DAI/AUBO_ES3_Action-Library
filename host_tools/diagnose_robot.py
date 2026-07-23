"""只读连接 AUBO 控制器并输出机器人状态。

该工具仅登录、发现机器人并读取上电状态、关节角和 TCP 位姿，不调用
上电、启动或运动接口，可用于检查网络、账号和 SDK 是否正常。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from math import degrees
from pathlib import Path
from typing import Sequence

# 允许直接执行本脚本，而无需先把 src 安装成 Python 包。
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client import (
    AuboClient,
    AuboClientError,
    ConfigError,
    RobotSnapshot,
    load_config,
)


def build_parser() -> argparse.ArgumentParser:
    """创建只读诊断命令的参数解析器。"""

    parser = argparse.ArgumentParser(
        description="只读连接 AUBO 控制器并打印状态；本工具不会上电或运动。"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "robot.local.json"),
        help="本地 JSON 配置文件路径。",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出状态。")
    return parser


def print_pose(snapshot: RobotSnapshot) -> None:
    """清晰打印当前关节位置和 TCP 位姿，便于现场记录与核对。"""

    joint_positions = snapshot.joint_positions_rad
    tcp_pose = snapshot.tcp_pose

    print("当前关节位置：")
    for index, value in enumerate(joint_positions, start=1):
        print(f"  J{index}: {value:.6f} rad ({degrees(value):.3f}°)")

    print("当前 TCP 位姿（SDK getTcpPose 返回值）：")
    if len(tcp_pose) == 6:
        x, y, z, rx, ry, rz = tcp_pose
        print(f"  位置 X/Y/Z (m)：{x:.6f}, {y:.6f}, {z:.6f}")
        print(f"  姿态 RX/RY/RZ (rad)：{rx:.6f}, {ry:.6f}, {rz:.6f}")
        print(
            "  姿态 RX/RY/RZ (deg)："
            f"{degrees(rx):.3f}, {degrees(ry):.3f}, {degrees(rz):.3f}"
        )
    else:
        # SDK 返回长度异常时仍保留原始数据，避免诊断信息被隐藏。
        print("  原始值：", [round(float(value), 6) for value in tcp_pose])
    print("  注：TCP 坐标系和工具定义以控制器当前配置为准。")


def main(argv: Sequence[str] | None = None) -> int:
    """加载配置、读取一次状态，并以文本或 JSON 形式输出。"""

    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        # 上下文管理器确保无论读取成功与否都会注销并断开 RPC。
        with AuboClient(config.robot) as client:
            snapshot = client.snapshot()
    except (ConfigError, AuboClientError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(asdict(snapshot), ensure_ascii=False, indent=2))
    else:
        print("连接成功（只读诊断，未发送运动命令）")
        print(f"机器人：{snapshot.robot_name}")
        print(f"已上电：{snapshot.power_on}")
        print_pose(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
