"""受严格限制的小范围关节运动测试工具。

默认只连接并预览目标，不上电、不启动、不运动。真实运动必须同时提供
非零 ``--delta``、``--execute`` 和固定确认文本；执行前后还会重复读取
当前位置并检查单步增量与配置软限位。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# 允许从仓库根目录直接执行脚本并导入 src 下的客户端包。
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client import (
    AuboClient,
    AuboClientError,
    ConfigError,
    SafetyError,
    load_config,
    preview_relative_joint_move,
)

CONFIRMATION_TEXT = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"


def parse_delta(text: str) -> tuple[float, ...]:
    """把逗号分隔的 J1～J6 弧度增量解析为六元组。"""

    try:
        values = tuple(float(part.strip()) for part in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--delta 必须是逗号分隔的 6 个弧度值。") from exc
    if len(values) != 6:
        raise argparse.ArgumentTypeError("--delta 必须正好包含 6 个弧度值。")
    return values


def build_parser() -> argparse.ArgumentParser:
    """定义 dry-run 与真实运动所需的命令行参数。"""

    parser = argparse.ArgumentParser(
        description="AUBO ES3 小范围关节测试。默认只预览，不运动。"
    )
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "robot.local.json"),
        help="本地 JSON 配置文件路径。",
    )
    parser.add_argument(
        "--delta",
        required=True,
        type=parse_delta,
        metavar="D1,D2,D3,D4,D5,D6",
        help="相对当前关节角的增量，单位 rad。例如 0,0,0,0,0,0.02。",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="允许真实运动；如果没有该参数，只做 dry-run。",
    )
    parser.add_argument(
        "--confirm-motion",
        default="",
        help=f"真实运动必须填入固定确认文本：{CONFIRMATION_TEXT}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """先生成安全预览；仅在双重显式确认后执行一次关节运动。"""

    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        with AuboClient(config.robot) as client:
            before = client.snapshot()
            preview = preview_relative_joint_move(
                before.joint_positions_rad, args.delta, config.safety
            )
            print(preview.as_text())
            print(
                f"速度上限：{config.safety.max_velocity_rad_s:.3f} rad/s；"
                f"加速度上限：{config.safety.max_acceleration_rad_s2:.3f} rad/s²"
            )

            # dry-run 到这里即结束：之前的 snapshot 只是只读状态查询。
            if not args.execute:
                print("DRY-RUN：未传入 --execute，不会给机器人上电、启动或发送运动命令。")
                return 0

            if args.confirm_motion != CONFIRMATION_TEXT:
                print(
                    "拒绝运动：确认文本不正确。请先检查现场、急停和目标，再使用：\n"
                    f"  --confirm-motion {CONFIRMATION_TEXT}",
                    file=sys.stderr,
                )
                return 3

            print("真实运动已显式授权。正在执行上电/启动前置步骤……")
            # 只有固定确认文本校验通过后，代码才会进入上电/启动步骤。
            client.prepare_for_motion(
                config.safety.power_on_wait_s, config.safety.startup_wait_s
            )

            # Re-read after startup because the state may have changed while waiting.
            # 上电阶段可能改变状态，因此以最新关节角重新计算并校验目标。
            current = client.snapshot()
            preview = preview_relative_joint_move(
                current.joint_positions_rad, args.delta, config.safety
            )
            print("启动后重新校验通过：")
            print(preview.as_text())
            result = client.move_joint(
                preview.target_rad,
                config.safety.max_acceleration_rad_s2,
                config.safety.max_velocity_rad_s,
            )
            print(f"moveJoint 返回：{result!r}")
            print("命令已下发。请持续观察机械臂和控制器安全状态。")
            return 0
    except (ConfigError, SafetyError, AuboClientError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
