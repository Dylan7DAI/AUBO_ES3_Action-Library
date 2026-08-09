#!/usr/bin/env python3
"""Execute the active branch's LLM expressive scheme with hard local gates."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aubo_es3_actions import load_config
from aubo_es3_actions.emergency_stop import (
    EmergencyStopMonitor,
    EmergencyStopRequested,
)
from aubo_es3_actions.expressive_common import (
    NEUTRAL_EMOTION_STATE,
    CompiledMotion,
    append_jsonl,
    call_structured_llm,
    expected_transition_type,
    normalize_result,
)
from aubo_es3_actions.expressive_safety import (
    load_expressive_limits,
    maximum_difference,
    sample_joint_keyframes,
    validate_joint_keyframes,
    validate_sampled_trajectory,
)
from aubo_es3_actions.sdk_client import AuboSdkClient, AuboSdkError
from scripts import llm_expressive_scheme as scheme


CONFIRM_MOTION = "I_UNDERSTAND_THIS_WILL_MOVE_THE_ROBOT"
STOP_FILE = ROOT / ".runtime" / "llm_expressive_emergency.stop"
POSE_FILE = ROOT / "config" / "emotion_poses_new_es3.json"
LIMITS_FILE = ROOT / "config" / "expressive_motion_limits.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"实机执行：{scheme.SCHEME_NAME}")
    parser.add_argument("--config", default="config/robot.curiosity_fast.json")
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    )
    parser.add_argument("--prompt-file", default=str(scheme.DEFAULT_PROMPT))
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--confirm-motion", default="")
    return parser


def wait_target(
    client: AuboSdkClient,
    target: Sequence[float],
    monitor: EmergencyStopMonitor,
    *,
    timeout_s: float,
    tolerance_rad: float = 0.02,
) -> None:
    deadline = time.monotonic() + timeout_s
    stable = 0
    while time.monotonic() < deadline:
        monitor.check()
        error = maximum_difference(client.current_joints(), target)
        if error <= tolerance_rad:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        time.sleep(0.025)
    client.stop_motion()
    raise AuboSdkError("等待目标姿态超时，已请求SDK停止")


def monitored_hold(monitor: EmergencyStopMonitor, seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        monitor.check()
        time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))


def execute_compiled_motion(
    client: AuboSdkClient,
    motion_plan: CompiledMotion,
    monitor: EmergencyStopMonitor,
) -> None:
    monitor.check()
    client.prepare_for_motion()
    monitor.check()
    motion = client._require_robot().getMotionControl()
    velocity = min(
        motion_plan.velocity_rad_s,
        client.config.safety.max_velocity_rad_s,
    )
    acceleration = min(
        motion_plan.acceleration_rad_s2,
        client.config.safety.max_acceleration_rad_s2,
    )
    for index, (target, hold_s) in enumerate(
        zip(motion_plan.keyframes, motion_plan.holds_s),
        start=1,
    ):
        monitor.check()
        distance = maximum_difference(client.current_joints(), target)
        if distance <= 0.02:
            monitored_hold(monitor, hold_s)
            continue
        result = motion.moveJoint(
            list(target),
            float(acceleration),
            float(velocity),
            0,
            0,
        )
        if result is False or (
            isinstance(result, int)
            and not isinstance(result, bool)
            and result != 0
        ):
            client.stop_motion()
            raise AuboSdkError(f"关键帧{index}下发失败：{result!r}")
        timeout = max(5.0, distance / max(velocity, 0.02) * 4.0 + 3.0)
        wait_target(client, target, monitor, timeout_s=timeout)
        monitored_hold(monitor, hold_s)


def validate_compiled_motion(
    client: AuboSdkClient,
    motion_plan: CompiledMotion,
    expressive_limits: Any,
) -> None:
    if (
        motion_plan.requires_cartesian_validation
        and expressive_limits.require_collision_model_for_cartesian
    ):
        raise ValueError(
            "该方案生成新的笛卡尔轨迹，但项目尚未集成经验证的碰撞模型。"
            "为避免把工作空间检查误当作碰撞检查，默认禁止实机执行。"
        )
    current = client.current_joints()
    keyframes = [current] + motion_plan.keyframes
    validated = validate_joint_keyframes(
        keyframes,
        client.config.safety,
        expressive_limits,
    )
    velocity = min(
        motion_plan.velocity_rad_s,
        client.config.safety.max_velocity_rad_s,
        expressive_limits.max_velocity_rad_s,
    )
    sampled = sample_joint_keyframes(
        validated,
        max_velocity_rad_s=velocity,
        sample_period_s=expressive_limits.sample_period_s,
    )
    validate_sampled_trajectory(
        sampled,
        client.config.safety,
        expressive_limits,
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.confirm_motion != CONFIRM_MOTION:
        print(f"当前为安全预览，不会连接机械臂。实机执行需添加：\n--confirm-motion {CONFIRM_MOTION}")
        return 0
    monitor = EmergencyStopMonitor(STOP_FILE)
    try:
        monitor.assert_clear()
        system_prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        pose_data = json.loads(POSE_FILE.read_text(encoding="utf-8"))
        expressive_limits = load_expressive_limits(LIMITS_FILE)
        config = load_config(args.config)
    except Exception as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 2

    history: list[str] = []
    previous_emotion_state: dict[str, Any] = dict(NEUTRAL_EMOTION_STATE)
    previous_plan: dict[str, Any] | None = None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "logs" / f"{scheme.SCHEME_ID}_robot_{timestamp}.jsonl"

    print(f"模式：{scheme.SCHEME_NAME}")
    print("实体急停必须保持可触达。软件急停命令：python3 scripts/emergency_stop.py")
    print("每轮仍需输入MOVE；计划或安全检查失败时不会写入历史。")

    try:
        with AuboSdkClient(config) as client:
            monitor.start(client.stop_motion)
            while not args.max_rounds or len(history) < args.max_rounds:
                monitor.check()
                text = input(f"\n第{len(history) + 1}轮结果 [1/0/e]：").strip()
                if text.lower() == "e":
                    break
                current_result = normalize_result(text)
                if current_result is None:
                    print("输入无效，请输入1、0或e。")
                    continue
                candidate_history = history + [current_result]
                base_input = {
                    "current_result": current_result,
                    "result_history": candidate_history,
                    "transition_type": expected_transition_type(candidate_history),
                    "previous_emotion_state": previous_emotion_state,
                    "previous_plan": previous_plan,
                }
                round_input = scheme.build_round_input(base_input)
                try:
                    plan = call_structured_llm(
                        plan_type=scheme.PlanModel,
                        model=args.model,
                        system_prompt=system_prompt,
                        round_input=round_input,
                        max_retries=args.max_retries,
                        validator=scheme.validate_plan,
                        temperature=scheme.TEMPERATURE,
                    )
                    compiled = scheme.compile_motion(
                        plan,
                        round_input=round_input,
                        pose_data=pose_data,
                        client=client,
                        expressive_limits=expressive_limits,
                    )
                    validate_compiled_motion(client, compiled, expressive_limits)
                except Exception as exc:
                    print(f"计划生成、编译或安全检查失败：{exc}", file=sys.stderr)
                    print("本轮不会运动，也不会写入历史。")
                    continue

                plan_dict = plan.model_dump(mode="json")
                print(json.dumps(scheme.build_preview(plan, round_input), ensure_ascii=False, indent=2))
                confirmation = input("确认现场安全后输入MOVE执行；其他输入取消：").strip()
                if confirmation != "MOVE":
                    print("已取消，本轮不写入历史。")
                    continue
                started_at = datetime.now(timezone.utc).isoformat()
                try:
                    execute_compiled_motion(client, compiled, monitor)
                except Exception:
                    try:
                        client.stop_motion()
                    except Exception:
                        pass
                    append_jsonl(
                        log_path,
                        {
                            "timestamp_utc": started_at,
                            "scheme": scheme.SCHEME_ID,
                            "input": round_input,
                            "plan": plan_dict,
                            "compiled_metadata": compiled.metadata,
                            "completed": False,
                        },
                    )
                    raise
                append_jsonl(
                    log_path,
                    {
                        "timestamp_utc": started_at,
                        "scheme": scheme.SCHEME_ID,
                        "input": round_input,
                        "plan": plan_dict,
                        "compiled_metadata": compiled.metadata,
                        "completed": True,
                    },
                )
                history = candidate_history
                previous_emotion_state = dict(plan_dict["emotion_state"])
                previous_plan = plan_dict
                print("本轮执行完成。")
    except (EmergencyStopRequested, KeyboardInterrupt) as exc:
        print(f"急停锁存：{exc}", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"实机程序停止：{exc}", file=sys.stderr)
        print("如机械臂仍在运动，请立即使用实体急停。", file=sys.stderr)
        return 8
    finally:
        monitor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
