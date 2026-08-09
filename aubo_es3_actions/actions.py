from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import GripperConfig
from .gripper import LebaiGripper
from .safety import make_jog_target
from .sdk_client import AuboSdkClient, JOINT_NAMES


ActionFunc = Callable[..., Any]
ACTIONS: Dict[str, ActionFunc] = {}
DEFAULT_PRESET_FILE = Path(__file__).resolve().parents[1] / "config" / "gripper_presets.json"
DEFAULT_ARM_POSE_FILE = Path(__file__).resolve().parents[1] / "config" / "arm_poses.json"


def action(name: str) -> Callable[[ActionFunc], ActionFunc]:
    def decorator(func: ActionFunc) -> ActionFunc:
        ACTIONS[name] = func
        return func
    return decorator


@action("current_joints")
def current_joints(client: AuboSdkClient) -> Dict[str, float]:
    values = client.current_joints()
    return dict(zip(JOINT_NAMES, values))


@action("current_pose")
def current_pose(client: AuboSdkClient) -> Dict[str, Any]:
    pose = client.current_pose()
    result: Dict[str, Any] = {
        "tcp_pose": pose,
        "format": "SDK getTcpPose 返回值: [x, y, z, rx, ry, rz]",
    }
    if len(pose) == 6:
        result["position_xyz_m"] = {"x": pose[0], "y": pose[1], "z": pose[2]}
        result["orientation_rpy_rad"] = {"rx": pose[3], "ry": pose[4], "rz": pose[5]}
    return result


@action("jog_tcp")
def jog_tcp(
    client: AuboSdkClient,
    axis: int,
    delta: float,
    execute: bool = False,
    linear_acc: float = 0.05,
    linear_vel: float = 0.02,
) -> Dict[str, Any]:
    if execute:
        target = client.jog_tcp(
            axis,
            delta,
            linear_acc_m_s2=linear_acc,
            linear_vel_m_s=linear_vel,
            prepare=True,
        )
        moved = True
    else:
        target = client.make_tcp_jog_target(axis, delta)
        ik_target = client.inverse_kinematics(target)
        moved = False
    return {
        "axis": axis,
        "axis_name": ["x", "y", "z", "rx", "ry", "rz"][axis],
        "delta": delta,
        "executed": moved,
        "target_tcp_pose": target,
        "ik_target_joints": dict(zip(JOINT_NAMES, ik_target)) if not moved else None,
    }


@action("jog_joint")
def jog_joint(client: AuboSdkClient, joint: int, delta: float, execute: bool = False) -> Dict[str, Any]:
    if execute:
        target = client.jog_joint(joint, delta, prepare=True)
        moved = True
    else:
        current = client.current_joints()
        target = make_jog_target(current, joint, delta, client.config.safety)
        moved = False
    return {
        "joint": joint,
        "delta_rad": delta,
        "executed": moved,
        "target_joints": dict(zip(JOINT_NAMES, target)),
    }


@action("gripper_status")
def gripper_status(client: AuboSdkClient, device: str = "", slave: int = 0) -> Dict[str, Any]:
    return LebaiGripper(client, _make_gripper_config(client, device, slave)).status()


@action("gripper_position")
def gripper_position(
    client: AuboSdkClient,
    position: int,
    execute: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.set_position(position, execute=execute, function_code=function_code)


@action("gripper_force")
def gripper_force(
    client: AuboSdkClient,
    force: int,
    execute: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.set_force(force, execute=execute, function_code=function_code)


@action("gripper_speed")
def gripper_speed(
    client: AuboSdkClient,
    speed: int,
    execute: bool = False,
    persist: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.set_speed(speed, execute=execute, persist=persist, function_code=function_code)


@action("gripper_move")
def gripper_move(
    client: AuboSdkClient,
    width: int,
    force: int = -1,
    speed: int = -1,
    execute: bool = False,
    wait: bool = False,
    command_delay: float = 0.3,
    tolerance: int = 3,
    verify: bool = True,
    verify_timeout: float = 4.0,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.move(
        width,
        force=None if force < 0 else force,
        speed=None if speed < 0 else speed,
        execute=execute,
        wait=wait,
        command_delay_s=command_delay,
        position_tolerance=tolerance,
        verify=verify,
        verify_timeout_s=verify_timeout,
        function_code=function_code,
    )


@action("1.0")
@action("open_gripper")
def open_gripper(
    client: AuboSdkClient,
    execute: bool = False,
    force: int = -1,
    speed: int = -1,
    wait: bool = False,
    command_delay: float = 0.3,
    tolerance: int = 3,
    verify: bool = True,
    verify_timeout: float = 4.0,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    return _move_gripper_to_limit(
        client,
        action_code="1.0",
        action_name="夹爪从闭合变成最大张开",
        target_width=100,
        execute=execute,
        force=force,
        speed=speed,
        wait=wait,
        command_delay=command_delay,
        tolerance=tolerance,
        verify=verify,
        verify_timeout=verify_timeout,
        device=device,
        slave=slave,
        function_code=function_code,
    )


@action("4.0")
@action("close_gripper")
def close_gripper(
    client: AuboSdkClient,
    execute: bool = False,
    force: int = -1,
    speed: int = -1,
    wait: bool = False,
    command_delay: float = 0.3,
    tolerance: int = 3,
    verify: bool = True,
    verify_timeout: float = 4.0,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    return _move_gripper_to_limit(
        client,
        action_code="4.0",
        action_name="夹爪从张开变成最小闭合",
        target_width=0,
        execute=execute,
        force=force,
        speed=speed,
        wait=wait,
        command_delay=command_delay,
        tolerance=tolerance,
        verify=verify,
        verify_timeout=verify_timeout,
        device=device,
        slave=slave,
        function_code=function_code,
    )


@action("gripper_feedback")
def gripper_feedback(client: AuboSdkClient, device: str = "", slave: int = 0) -> Dict[str, Any]:
    return LebaiGripper(client, _make_gripper_config(client, device, slave)).feedback()


@action("gripper_find_stroke")
def gripper_find_stroke(
    client: AuboSdkClient,
    execute: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.find_stroke(execute=execute, function_code=function_code)


@action("gripper_disable_auto_find_stroke")
def gripper_disable_auto_find_stroke(
    client: AuboSdkClient,
    execute: bool = False,
    persist: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    return gripper.disable_auto_find_stroke(execute=execute, persist=persist, function_code=function_code)


@action("2.1")
@action("lift_return")
def lift_return(
    client: AuboSdkClient,
    execute: bool = False,
    lift: Optional[float] = None,
    vertical_lift: float = 0.05,
    arc_lift: float = 0.05,
    back_offset: float = 0.10,
    back_axis: str = "x",
    back_sign: int = -1,
    arc_segments: int = 4,
    pause: float = 3.0,
    linear_acc: float = 0.03,
    linear_vel: float = 0.01,
    move_timeout: float = 20.0,
    position_tolerance: float = 0.005,
    device: str = "",
    slave: int = 0,
) -> Dict[str, Any]:
    if lift is not None:
        if lift <= 0:
            raise ValueError("lift 必须大于 0")
        arc_lift = float(lift) - float(vertical_lift)
    if vertical_lift <= 0:
        raise ValueError("vertical_lift 必须大于 0")
    if arc_lift < 0:
        raise ValueError("arc_lift 不能小于 0；如果使用 --lift，总上升高度必须不小于 vertical_lift")
    if back_offset < 0:
        raise ValueError("back_offset 不能小于 0")
    if back_axis not in ("x", "y"):
        raise ValueError("back_axis 只能是 x 或 y")
    if int(back_sign) not in (-1, 1):
        raise ValueError("back_sign 只能是 -1 或 1")
    if int(arc_segments) < 1:
        raise ValueError("arc_segments 必须大于等于 1")
    if pause < 0:
        raise ValueError("pause 不能小于 0")
    if move_timeout <= 0:
        raise ValueError("move_timeout 必须大于 0")
    if position_tolerance <= 0:
        raise ValueError("position_tolerance 必须大于 0")

    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    start_pose = client.current_pose()
    start_joints = client.current_joints()
    outbound_waypoints = _body_lift_waypoints(
        start_pose,
        vertical_lift_m=vertical_lift,
        arc_lift_m=arc_lift,
        back_offset_m=back_offset,
        back_axis=back_axis,
        back_sign=int(back_sign),
        arc_segments=int(arc_segments),
    )
    return_waypoints = list(reversed(outbound_waypoints[:-1])) + [start_pose]
    all_waypoints = outbound_waypoints + return_waypoints

    start_gripper = gripper.feedback()
    waypoint_previews = []
    max_joint_delta = 0.0
    max_wrist_delta = 0.0
    for index, waypoint in enumerate(all_waypoints, start=1):
        joints = client.inverse_kinematics(waypoint)
        joint_delta = [float(target) - float(current) for target, current in zip(joints, start_joints)]
        wrist_delta = joint_delta[3:]
        max_joint_delta = max(max_joint_delta, max(abs(value) for value in joint_delta))
        max_wrist_delta = max(max_wrist_delta, max(abs(value) for value in wrist_delta))
        waypoint_previews.append(
            {
                "step": index,
                "phase": _lift_waypoint_phase(index, len(outbound_waypoints), len(all_waypoints)),
                "tcp_pose": waypoint,
                "ik_joints": dict(zip(JOINT_NAMES, joints)),
                "ik_joint_delta_rad": dict(zip(JOINT_NAMES, joint_delta)),
                "near_tool_joint_delta_rad": dict(zip(JOINT_NAMES[3:], wrist_delta)),
            }
        )
    return_joints = client.inverse_kinematics(start_pose)

    plan: Dict[str, Any] = {
        "action_code": "2.1",
        "action_name": "夹取后上移后仰并放回原位",
        "executed": bool(execute),
        "gripper_note": "本动作不改变夹爪，只沿用当前夹爪夹持状态。",
        "start_gripper_feedback": start_gripper,
        "start_tcp_pose": start_pose,
        "start_joints": dict(zip(JOINT_NAMES, start_joints)),
        "vertical_lift_m": vertical_lift,
        "arc_lift_m": arc_lift,
        "total_lift_m": vertical_lift + arc_lift,
        "back_offset_m": back_offset,
        "back_axis": back_axis,
        "back_sign": int(back_sign),
        "arc_segments": int(arc_segments),
        "move_sequence": [
            "先 TCP 垂直上移",
            "保持末端姿态不变，按小段 waypoint 近似弧线向上并向后",
            "停顿",
            "沿刚才 waypoint 逆向回到垂直上移点",
            "再垂直下放回启动 TCP 位姿",
        ],
        "waypoints": waypoint_previews,
        "pause_s": pause,
        "linear_acc_m_s2": linear_acc,
        "linear_vel_m_s": linear_vel,
        "move_timeout_s": move_timeout,
        "position_tolerance_m": position_tolerance,
        "max_ik_joint_delta_rad": max_joint_delta,
        "max_near_tool_ik_joint_delta_rad": max_wrist_delta,
        "ik_branch_warning": (
            "某个 waypoint 逆解与当前关节差值较大；执行前建议先用更小幅度 dry-run，或在示教器确认路径。"
            if max_joint_delta > 1.0
            else ""
        ),
        "near_tool_joint_note": "TCP 姿态保持不变，尽量减少近末端 wrist 关节动作；实际关节分配由控制器逆解决定，请观察 dry-run 中 near_tool_joint_delta_rad。",
        "return_ik_joints": dict(zip(JOINT_NAMES, return_joints)),
        "safety_checks": [
            "确认杯子已夹稳，上方和后方运动路径没有障碍。",
            "保持急停可触达；若杯子滑动或碰撞，立即急停。",
            "本动作结束后会回到启动时记录的 TCP 位姿。",
        ],
    }
    if not execute:
        plan["dry_run"] = "未发送运动命令；已读取当前夹爪反馈，并检查所有 waypoint 逆解。"
        return plan

    client.prepare_for_motion()
    move_results = []
    for index, waypoint in enumerate(outbound_waypoints, start=1):
        command_result = client.move_to_tcp_pose(
            waypoint,
            linear_acc_m_s2=linear_acc,
            linear_vel_m_s=linear_vel,
            prepare=False,
        )
        wait_result = _wait_until_tcp_position(
            client,
            waypoint,
            timeout_s=move_timeout,
            position_tolerance_m=position_tolerance,
        )
        move_results.append({
            "step": index,
            "phase": _lift_waypoint_phase(index, len(outbound_waypoints), len(all_waypoints)),
            "move_command_result": command_result,
            "wait_result": wait_result,
        })
    time.sleep(pause)
    for offset, waypoint in enumerate(return_waypoints, start=1):
        step = len(outbound_waypoints) + offset
        command_result = client.move_to_tcp_pose(
            waypoint,
            linear_acc_m_s2=linear_acc,
            linear_vel_m_s=linear_vel,
            prepare=False,
        )
        wait_result = _wait_until_tcp_position(
            client,
            waypoint,
            timeout_s=move_timeout,
            position_tolerance_m=position_tolerance,
        )
        move_results.append({
            "step": step,
            "phase": _lift_waypoint_phase(step, len(outbound_waypoints), len(all_waypoints)),
            "move_command_result": command_result,
            "wait_result": wait_result,
        })
    plan["move_wait_result"] = move_results
    plan["end_gripper_feedback"] = gripper.feedback()
    plan["motion_result"] = "已垂直上移、弧线向上后退、停顿，并沿原路径返回原位"
    return plan


@action("2.0")
@action("return_grasp_pose")
def return_grasp_pose(
    client: AuboSdkClient,
    execute: bool = False,
    preset: str = "glass_good",
    preset_file: str = "",
    apply_gripper: bool = True,
    gripper_after_motion: bool = True,
    move_timeout: float = 30.0,
    joint_tolerance: float = 0.01,
    command_delay: float = 0.3,
    device: str = "",
    slave: int = 0,
) -> Dict[str, Any]:
    if move_timeout <= 0:
        raise ValueError("move_timeout 必须大于 0")
    if joint_tolerance <= 0:
        raise ValueError("joint_tolerance 必须大于 0")

    preset_path = Path(preset_file).expanduser() if preset_file else DEFAULT_PRESET_FILE
    data = _load_grasp_preset(preset_path, preset)
    target_joints = _preset_joint_list(data)
    target_tcp_pose = [float(v) for v in data["robot_state"]["tcp_pose"]]
    target_width = _clamp_percent(data.get("width", 0))
    target_force = _clamp_percent(data.get("force", 0))

    current_joints = client.current_joints()
    current_pose = client.current_pose()
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    current_gripper = gripper.feedback()
    staged_targets = _tool_to_base_joint_stages(current_joints, target_joints)
    joint_delta = [float(target) - float(current) for target, current in zip(target_joints, current_joints)]
    max_joint_delta = max(abs(value) for value in joint_delta)

    plan: Dict[str, Any] = {
        "action_code": "2.0",
        "action_name": "回到夹取成功位",
        "executed": bool(execute),
        "preset": preset,
        "preset_file": str(preset_path),
        "move_mode": "recorded_joint_positions_staged",
        "move_order": "tool_to_base",
        "apply_gripper": bool(apply_gripper),
        "gripper_timing": "after_motion" if gripper_after_motion else "before_motion",
        "current_tcp_pose": current_pose,
        "current_joints": dict(zip(JOINT_NAMES, current_joints)),
        "current_gripper_feedback": current_gripper,
        "target_tcp_pose": target_tcp_pose,
        "target_joints": dict(zip(JOINT_NAMES, target_joints)),
        "staged_targets": [
            {
                "stage": index,
                "changed_joints": changed,
                "target_joints": dict(zip(JOINT_NAMES, joints)),
            }
            for index, changed, joints in staged_targets
        ],
        "target_gripper": {
            "width": target_width,
            "force": target_force,
        },
        "joint_delta_rad": dict(zip(JOINT_NAMES, joint_delta)),
        "max_joint_delta_rad": max_joint_delta,
        "move_timeout_s": move_timeout,
        "joint_tolerance_rad": joint_tolerance,
        "safety_checks": [
            "从任意位置回到记录关节角不等于自动避障，执行前确认路径安全。",
            "从当前高位回到桌面夹取位时，会先调整 wrist1/wrist2/wrist3，再移动 shoulder/upperArm/foreArm。",
            "确认夹爪和杯子不会在回位过程中碰到桌面、支架或机械臂本体。",
            "如当前位置离目标很远，建议先用示教器靠近，再执行本动作。",
        ],
    }
    if max_joint_delta > 1.5:
        plan["joint_delta_warning"] = "当前关节离记录夹取位较远，建议先确认运动路径或先手动靠近。"

    if not execute:
        plan["dry_run"] = "未发送运动或夹爪命令；已读取当前状态并加载目标夹取位。"
        return plan

    gripper_result = None
    if apply_gripper and not gripper_after_motion:
        gripper_result = gripper.move(
            target_width,
            force=target_force,
            execute=True,
            command_delay_s=command_delay,
            function_code=0x06,
        )

    stage_results = []
    for index, changed, joints in staged_targets:
        command_result = client.move_to_joints(joints, prepare=(index == 1))
        try:
            wait_result = _wait_until_joints(
                client,
                joints,
                timeout_s=move_timeout,
                joint_tolerance_rad=joint_tolerance,
            )
        except ValueError as exc:
            raise ValueError(f"2.0 第 {index} 段关节运动未到位 ({', '.join(changed)}): {exc}") from exc
        stage_results.append(
            {
                "stage": index,
                "changed_joints": changed,
                "move_command_result": command_result,
                "wait_result": wait_result,
            }
        )
    plan["move_wait_result"] = stage_results

    if apply_gripper and gripper_after_motion:
        gripper_result = gripper.move(
            target_width,
            force=target_force,
            execute=True,
            command_delay_s=command_delay,
            function_code=0x06,
        )

    plan["gripper_result"] = gripper_result
    plan["end_tcp_pose"] = client.current_pose()
    plan["end_gripper_feedback"] = gripper.feedback()
    plan["motion_result"] = "已回到保存的夹取成功位，并按预设恢复夹爪参数" if apply_gripper else "已回到保存的夹取成功位，未改变夹爪"
    return plan


@action("3.0")
@action("release_and_tuck")
def release_and_tuck(
    client: AuboSdkClient,
    execute: bool = False,
    pose: str = "tucked",
    pose_file: str = "",
    open_width: int = 100,
    open_force: int = 15,
    release_delay: float = 1.0,
    move_timeout: float = 30.0,
    joint_tolerance: float = 0.01,
    command_delay: float = 0.3,
    device: str = "",
    slave: int = 0,
) -> Dict[str, Any]:
    if release_delay < 0:
        raise ValueError("release_delay 不能小于 0")
    if move_timeout <= 0:
        raise ValueError("move_timeout 必须大于 0")
    if joint_tolerance <= 0:
        raise ValueError("joint_tolerance 必须大于 0")

    pose_path = Path(pose_file).expanduser() if pose_file else DEFAULT_ARM_POSE_FILE
    pose_data = _load_arm_pose(pose_path, pose)
    target_joints = _pose_joint_list(pose_data)
    current_joints = client.current_joints()
    current_pose = client.current_pose()
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    current_gripper = gripper.feedback()
    staged_targets = _base_to_tool_joint_stages(current_joints, target_joints)
    joint_delta = [float(target) - float(current) for target, current in zip(target_joints, current_joints)]
    max_joint_delta = max(abs(value) for value in joint_delta)

    plan: Dict[str, Any] = {
        "action_code": "3.0",
        "action_name": "松爪和回到收缩位姿",
        "executed": bool(execute),
        "pose": pose,
        "pose_file": str(pose_path),
        "release_gripper": {
            "width": _clamp_percent(open_width),
            "force": _clamp_percent(open_force),
            "delay_s": release_delay,
        },
        "current_tcp_pose": current_pose,
        "current_joints": dict(zip(JOINT_NAMES, current_joints)),
        "current_gripper_feedback": current_gripper,
        "target_joints": dict(zip(JOINT_NAMES, target_joints)),
        "move_order": "base_to_tool",
        "staged_targets": [
            {
                "stage": index,
                "changed_joints": changed,
                "target_joints": dict(zip(JOINT_NAMES, joints)),
            }
            for index, changed, joints in staged_targets
        ],
        "joint_delta_rad": dict(zip(JOINT_NAMES, joint_delta)),
        "max_joint_delta_rad": max_joint_delta,
        "move_timeout_s": move_timeout,
        "joint_tolerance_rad": joint_tolerance,
        "safety_checks": [
            "夹爪会先完全张开，确认杯子或物体可以安全释放。",
            "机械臂会先移动 shoulder/upperArm/foreArm，再移动 wrist1/wrist2/wrist3，以减少末端先扫到桌面的风险。",
            "分阶段关节运动不等于自动避障，确认路径不会碰到桌面、支架或本体。",
            "首次真实执行建议先用较低速度设置，并在示教器旁观察。",
        ],
    }
    if max_joint_delta > 1.5:
        plan["joint_delta_warning"] = "当前关节离收缩位姿较远，执行前请确认路径安全。"

    if not execute:
        plan["dry_run"] = "未发送夹爪或运动命令；已加载收缩位姿并读取当前状态。"
        return plan

    plan["gripper_result"] = gripper.move(
        _clamp_percent(open_width),
        force=_clamp_percent(open_force),
        execute=True,
        command_delay_s=command_delay,
        function_code=0x06,
    )
    time.sleep(release_delay)
    stage_results = []
    for index, changed, joints in staged_targets:
        client.move_to_joints(joints, prepare=(index == 1))
        wait_result = _wait_until_joints(
            client,
            joints,
            timeout_s=move_timeout,
            joint_tolerance_rad=joint_tolerance,
        )
        stage_results.append(
            {
                "stage": index,
                "changed_joints": changed,
                "wait_result": wait_result,
            }
        )
    plan["move_wait_result"] = stage_results
    plan["end_tcp_pose"] = client.current_pose()
    plan["end_gripper_feedback"] = gripper.feedback()
    plan["motion_result"] = "已张开夹爪并回到收缩位姿"
    return plan


@action("pick_glass")
def pick_glass(
    client: AuboSdkClient,
    execute: bool = False,
    force: int = 18,
    close_position: int = 55,
    lift: float = 0.05,
    test_lift: float = 0.005,
    linear_acc: float = 0.03,
    linear_vel: float = 0.01,
    close_delay: float = 0.35,
    settle_delay: float = 0.8,
    grip_only: bool = False,
    device: str = "",
    slave: int = 0,
    function_code: int = 0x06,
) -> Dict[str, Any]:
    """Grip a manually aligned glass cup gently, test-lift, then lift higher.

    The gripper currently has no reliable force/contact feedback in this project, so this
    action uses conservative force and staged closing. The operator should place the TCP
    around the glass before running it and watch the first few trials closely.
    """
    if not grip_only and lift <= 0:
        raise ValueError("lift 必须大于 0")
    if not grip_only and test_lift <= 0:
        raise ValueError("test_lift 必须大于 0")
    if not grip_only and test_lift > lift:
        raise ValueError("test_lift 不能大于 lift")

    force = _clamp_percent(force)
    close_position = _clamp_percent(close_position)
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))

    start_pose = client.current_pose()
    test_pose = _offset_tcp_z(start_pose, test_lift)
    lift_pose = _offset_tcp_z(start_pose, lift)

    if not grip_only:
        client.inverse_kinematics(test_pose)
        client.inverse_kinematics(lift_pose)

    close_sequence = _glass_close_sequence(close_position)
    plan: Dict[str, Any] = {
        "executed": bool(execute),
        "operator_setup": "运行前请先用键盘或示教把夹爪放到玻璃杯外侧，杯壁位于两指中间。",
        "force_note": "默认夹力偏低；若杯子滑动，优先微调 close_position，再每次只把 force 增加 2-3。",
        "force_percent": force,
        "close_position_percent": close_position,
        "close_sequence_percent": close_sequence,
        "start_tcp_pose": start_pose,
        "test_lift_m": test_lift,
        "test_lift_tcp_pose": test_pose,
        "final_lift_m": lift,
        "final_lift_tcp_pose": lift_pose,
        "linear_acc_m_s2": linear_acc,
        "linear_vel_m_s": linear_vel,
        "grip_only": bool(grip_only),
        "gripper_device": gripper.config.modbus_device,
        "function_code": function_code,
        "safety_checks": [
            "第一次请不要用真正玻璃杯，先用塑料杯或空杯标定。",
            "保持急停可触达，确认杯子上方 lift 高度内没有障碍。",
            "如果杯子变形、异响或滑动，立即急停或停止动作，降低 force 或调整 close_position。",
        ],
    }

    if not execute:
        if grip_only:
            plan["dry_run"] = "未发送夹爪命令；grip_only 模式只预览夹爪动作，不检查抬高逆解。"
        else:
            plan["dry_run"] = "未发送夹爪或运动命令；已连接机器人并检查当前位姿、试提和抬高目标的逆解。"
        plan["preview_gripper_commands"] = [
            gripper.command_preview(gripper.config.force_register, force, function_code=function_code),
            *[
                gripper.command_preview(gripper.config.position_register, pos, function_code=function_code)
                for pos in close_sequence
            ],
        ]
        return plan

    results: List[Dict[str, Any]] = []
    for pos in close_sequence:
        results.append(gripper.move(
            pos,
            force=force if not results else None,
            speed=None,
            execute=True,
            wait=False,
            command_delay_s=close_delay,
            position_tolerance=4,
            verify=True,
            function_code=function_code,
        ))
        time.sleep(close_delay)
    time.sleep(settle_delay)

    if grip_only:
        plan["gripper_results"] = results
        plan["motion_result"] = "已执行低夹力分阶段夹紧，未抬高"
        return plan

    client.prepare_for_motion()
    client.move_to_tcp_pose(
        test_pose,
        linear_acc_m_s2=linear_acc,
        linear_vel_m_s=linear_vel,
        prepare=False,
    )
    time.sleep(settle_delay)
    client.move_to_tcp_pose(
        lift_pose,
        linear_acc_m_s2=linear_acc,
        linear_vel_m_s=linear_vel,
        prepare=False,
    )

    plan["gripper_results"] = results
    plan["motion_result"] = "已执行夹紧、试提和抬高"
    return plan


def _make_gripper_config(client: AuboSdkClient, device: str = "", slave: int = 0) -> GripperConfig:
    base = client.config.gripper
    return GripperConfig(
        modbus_device=device or base.modbus_device,
        slave_id=slave or base.slave_id,
        baudrate=base.baudrate,
        data_bits=base.data_bits,
        parity=base.parity,
        stop_bits=base.stop_bits,
        position_register=base.position_register,
        force_register=base.force_register,
        current_position_register=base.current_position_register,
        torque_register=base.torque_register,
        done_register=base.done_register,
        find_stroke_register=base.find_stroke_register,
        stroke_not_found_register=base.stroke_not_found_register,
        speed_register=base.speed_register,
        speed_save_register=base.speed_save_register,
        auto_find_stroke_register=base.auto_find_stroke_register,
        set_address_register=base.set_address_register,
    )


def _move_gripper_to_limit(
    client: AuboSdkClient,
    *,
    action_code: str,
    action_name: str,
    target_width: int,
    execute: bool,
    force: int,
    speed: int,
    wait: bool,
    command_delay: float,
    tolerance: int,
    verify: bool,
    verify_timeout: float,
    device: str,
    slave: int,
    function_code: int,
) -> Dict[str, Any]:
    gripper = LebaiGripper(client, _make_gripper_config(client, device, slave))
    start_feedback = gripper.feedback()
    move_result = gripper.move(
        target_width,
        force=None if force < 0 else force,
        speed=None if speed < 0 else speed,
        execute=execute,
        wait=wait,
        command_delay_s=command_delay,
        position_tolerance=tolerance,
        verify=verify,
        verify_timeout_s=verify_timeout,
        function_code=function_code,
    )
    plan: Dict[str, Any] = {
        "action_code": action_code,
        "action_name": action_name,
        "executed": bool(execute),
        "target_width": target_width,
        "target_state": "fully_open" if target_width == 100 else "fully_closed",
        "start_gripper_feedback": start_feedback,
        "move_result": move_result,
    }
    if not execute:
        plan["dry_run"] = "未发送夹爪命令；只生成目标寄存器写入预览，并读取当前夹爪反馈。"
    return plan


def _clamp_percent(value: int) -> int:
    value = int(value)
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def _offset_tcp_z(pose: List[float], delta_z: float) -> List[float]:
    if len(pose) != 6:
        raise ValueError(f"TCP 位姿长度不是 6: {pose}")
    target = [float(v) for v in pose]
    target[2] += float(delta_z)
    return target


def _body_lift_waypoints(
    start_pose: List[float],
    *,
    vertical_lift_m: float,
    arc_lift_m: float,
    back_offset_m: float,
    back_axis: str,
    back_sign: int,
    arc_segments: int,
) -> List[List[float]]:
    if len(start_pose) != 6:
        raise ValueError(f"TCP 位姿长度不是 6: {start_pose}")
    axis_index = {"x": 0, "y": 1}[back_axis]
    start = [float(v) for v in start_pose]
    vertical_pose = [float(v) for v in start]
    vertical_pose[2] += float(vertical_lift_m)

    waypoints = [vertical_pose]
    for segment in range(1, int(arc_segments) + 1):
        theta = (math.pi / 2.0) * (segment / float(arc_segments))
        waypoint = [float(v) for v in vertical_pose]
        waypoint[axis_index] += float(back_sign) * float(back_offset_m) * (1.0 - math.cos(theta))
        waypoint[2] += float(arc_lift_m) * math.sin(theta)
        waypoints.append(waypoint)
    return waypoints


def _lift_waypoint_phase(step: int, outbound_count: int, total_count: int) -> str:
    if step == 1:
        return "vertical_up"
    if step <= outbound_count:
        return "arc_up_back"
    if step == total_count:
        return "vertical_down"
    return "reverse_arc_forward_down"


def _wait_until_tcp_position(
    client: AuboSdkClient,
    target_pose: List[float],
    *,
    timeout_s: float,
    position_tolerance_m: float,
    poll_s: float = 0.2,
) -> Dict[str, Any]:
    deadline = time.time() + float(timeout_s)
    last_pose: List[float] = []
    while time.time() < deadline:
        last_pose = client.current_pose()
        distance = _tcp_position_distance(last_pose, target_pose)
        if distance <= position_tolerance_m:
            return {
                "reached": True,
                "distance_m": distance,
                "actual_tcp_pose": last_pose,
            }
        time.sleep(poll_s)
    raise ValueError(
        "等待 TCP 到位超时: "
        f"目标 xyz={target_pose[:3]}, 当前 xyz={last_pose[:3] if last_pose else 'unknown'}"
    )


def _wait_until_joints(
    client: AuboSdkClient,
    target_joints: List[float],
    *,
    timeout_s: float,
    joint_tolerance_rad: float,
    poll_s: float = 0.2,
) -> Dict[str, Any]:
    deadline = time.time() + float(timeout_s)
    last_joints: List[float] = []
    while time.time() < deadline:
        last_joints = client.current_joints()
        max_error = max(abs(float(a) - float(b)) for a, b in zip(last_joints, target_joints))
        if max_error <= joint_tolerance_rad:
            return {
                "reached": True,
                "max_error_rad": max_error,
                "actual_joints": dict(zip(JOINT_NAMES, last_joints)),
            }
        time.sleep(poll_s)
    raise ValueError(
        "等待关节到位超时: "
        f"最大误差={max(abs(float(a) - float(b)) for a, b in zip(last_joints, target_joints)) if last_joints else 'unknown'}"
    )


def _tcp_position_distance(a: List[float], b: List[float]) -> float:
    if len(a) < 3 or len(b) < 3:
        raise ValueError(f"TCP 位姿长度不足，无法比较 xyz: {a}, {b}")
    return sum((float(a[idx]) - float(b[idx])) ** 2 for idx in range(3)) ** 0.5


def _load_grasp_preset(path: Path, name: str) -> Dict[str, Any]:
    if not path.exists():
        raise ValueError(f"未找到预设文件: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    presets = raw.get("presets", {})
    if name not in presets:
        available = ", ".join(sorted(presets)) or "无"
        raise ValueError(f"未找到预设 {name!r}，可用预设: {available}")
    preset = presets[name]
    if "robot_state" not in preset:
        raise ValueError(f"预设 {name!r} 没有 robot_state，请先记录机械臂位姿")
    return preset


def _preset_joint_list(preset: Dict[str, Any]) -> List[float]:
    joints = preset["robot_state"].get("joint_positions_rad", {})
    missing = [name for name in JOINT_NAMES if name not in joints]
    if missing:
        raise ValueError(f"预设缺少关节角: {', '.join(missing)}")
    return [float(joints[name]) for name in JOINT_NAMES]


def _load_arm_pose(path: Path, name: str) -> Dict[str, Any]:
    if not path.exists():
        raise ValueError(f"未找到机械臂位姿文件: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    poses = raw.get("poses", {})
    if name not in poses:
        available = ", ".join(sorted(poses)) or "无"
        raise ValueError(f"未找到机械臂位姿 {name!r}，可用位姿: {available}")
    return poses[name]


def _pose_joint_list(pose: Dict[str, Any]) -> List[float]:
    joints = pose.get("joint_positions_rad", {})
    missing = [name for name in JOINT_NAMES if name not in joints]
    if missing:
        raise ValueError(f"机械臂位姿缺少关节角: {', '.join(missing)}")
    return [float(joints[name]) for name in JOINT_NAMES]


def _base_to_tool_joint_stages(current_joints: List[float], target_joints: List[float]) -> List[Any]:
    """Build staged targets that move base-side joints before wrist joints."""
    base_stage = [float(value) for value in current_joints]
    for index in range(3):
        base_stage[index] = float(target_joints[index])

    tool_stage = [float(value) for value in target_joints]
    return [
        (1, JOINT_NAMES[:3], base_stage),
        (2, JOINT_NAMES[3:], tool_stage),
    ]


def _tool_to_base_joint_stages(current_joints: List[float], target_joints: List[float]) -> List[Any]:
    """Build staged targets that align wrist joints before moving base-side joints."""
    tool_stage = [float(value) for value in current_joints]
    for index in range(3, 6):
        tool_stage[index] = float(target_joints[index])

    base_stage = [float(value) for value in target_joints]
    return [
        (1, JOINT_NAMES[3:], tool_stage),
        (2, JOINT_NAMES[:3], base_stage),
    ]


def _glass_close_sequence(close_position: int) -> List[int]:
    if close_position >= 85:
        return [close_position]
    sequence = [85, 75, 65, close_position]
    result: List[int] = []
    for pos in sequence:
        pos = _clamp_percent(pos)
        if pos >= close_position and pos not in result:
            result.append(pos)
    if result[-1] != close_position:
        result.append(close_position)
    return result


def run_action(name: str, client: AuboSdkClient, **kwargs) -> Any:
    if name not in ACTIONS:
        available = ", ".join(sorted(ACTIONS))
        raise KeyError(f"未知动作: {name}. 可用动作: {available}")
    return ACTIONS[name](client, **kwargs)
