"""Mock and real AUBO adapters plus a single serialized motion executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from math import ceil
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.aubo_sdk_client import AuboClient, RobotSnapshot, load_config
from src.aubo_sdk_client.safety import validate_absolute_joint_target

from .config import StudyConfig
from .event_logger import EventLogger
from .models import ActionPlan
from .safety_validator import SafetyValidator


class RobotAdapterError(RuntimeError):
    pass


class RobotAdapter(Protocol):
    mode: str

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def snapshot(self) -> RobotSnapshot: ...
    def move_joint(self, target: Sequence[float], acceleration: float, velocity: float) -> Any: ...
    def emergency_stop(self, deceleration: float) -> Any: ...


class MockRobotAdapter:
    mode = "mock"

    def __init__(self, initial_joints: Sequence[float] = (0, 0, 0, 0, 0, 0)):
        self.joints = tuple(float(item) for item in initial_joints)
        self.connected = False
        self.stopped = False

    def connect(self) -> None:
        self.connected = True
        self.stopped = False

    def close(self) -> None:
        self.connected = False

    def snapshot(self) -> RobotSnapshot:
        if not self.connected:
            raise RobotAdapterError("Mock robot is not connected")
        return RobotSnapshot(
            robot_name="mock-es3", power_on=True, joint_positions_rad=self.joints,
            tcp_pose=(0.4, 0.0, 0.3, 0.0, 0.0, 0.0), sdk_version="mock-1.0",
            robot_mode="Running", safety_mode="Normal", steady=True,
            collision_occurred=False, within_safety_limits=True,
            telemetry={"simulated": True, "emergency_stopped": self.stopped},
        )

    def move_joint(self, target: Sequence[float], acceleration: float, velocity: float) -> int:
        if self.stopped:
            raise RobotAdapterError("Mock robot is emergency-stopped")
        if len(target) != 6:
            raise RobotAdapterError("Mock target must contain six joints")
        self.joints = tuple(float(item) for item in target)
        time.sleep(0.001)
        return 0

    def emergency_stop(self, deceleration: float) -> int:
        self.stopped = True
        return 0


class AuboRobotAdapter:
    mode = "real"

    def __init__(self, robot_config_path: str | Path):
        self.app_config = load_config(robot_config_path)
        self.client = AuboClient(self.app_config.robot)

    def connect(self) -> None:
        self.client.connect()
        self.client.prepare_for_motion(
            self.app_config.safety.power_on_wait_s, self.app_config.safety.startup_wait_s
        )

    def close(self) -> None:
        self.client.close()

    def snapshot(self) -> RobotSnapshot:
        return self.client.snapshot()

    def move_joint(self, target: Sequence[float], acceleration: float, velocity: float) -> Any:
        checked = validate_absolute_joint_target(target, self.app_config.safety)
        return self.client.move_joint(checked, acceleration, velocity)

    def emergency_stop(self, deceleration: float) -> Any:
        return self.client.stop_motion(deceleration)


class GripperAdapter(Protocol):
    def open(self) -> Any: ...
    def close(self) -> Any: ...


class MockGripperAdapter:
    def __init__(self) -> None:
        self.closed = False

    def open(self) -> int:
        self.closed = False
        return 0

    def close(self) -> int:
        self.closed = True
        return 0


class UnsupportedGripperAdapter:
    def open(self) -> None:
        raise RobotAdapterError("No real GripperAdapter is configured")

    def close(self) -> None:
        raise RobotAdapterError("No real GripperAdapter is configured")


class DigitalOutputGripperAdapter:
    """Generic two-state gripper driven by one controller DO and optional DI feedback."""

    def __init__(self, adapter: AuboRobotAdapter, settings: dict[str, Any]):
        self.adapter = adapter
        self.output_index = int(settings["output_index"])
        self.closed_output_value = bool(settings.get("closed_output_value", True))
        feedback = settings.get("closed_feedback_input")
        self.feedback_index = int(feedback) if feedback is not None else None
        self.settle_s = float(settings.get("settle_s", 0.5))

    def open(self) -> Any:
        result = self.adapter.client.set_standard_digital_output(
            self.output_index, not self.closed_output_value
        )
        time.sleep(self.settle_s)
        if self.feedback_index is not None and self.adapter.client.get_standard_digital_input(
            self.feedback_index
        ):
            raise RobotAdapterError("Gripper open command did not clear closed feedback")
        return result

    def close(self) -> Any:
        result = self.adapter.client.set_standard_digital_output(
            self.output_index, self.closed_output_value
        )
        time.sleep(self.settle_s)
        if self.feedback_index is not None and not self.adapter.client.get_standard_digital_input(
            self.feedback_index
        ):
            raise RobotAdapterError("Gripper close command did not assert closed feedback")
        return result

class RobotExecutor:
    """The only component allowed to issue motion; regular commands are serialized."""

    def __init__(
        self,
        adapter: RobotAdapter,
        gripper: GripperAdapter,
        config: StudyConfig,
        logger: EventLogger,
        validator: SafetyValidator,
    ):
        self.adapter = adapter
        self.gripper = gripper
        self.config = config
        self.logger = logger
        self.validator = validator
        self._motion_lock = asyncio.Lock()
        self._stopped = False
        task_pose_names = [
            name for name in sorted(self.config.workcell.get("joint_poses_rad", {}))
            if name == "home" or name.startswith("cup_") or name.startswith("place")
        ]
        task_poses = {
            name: self.config.workcell["joint_poses_rad"][name] for name in task_pose_names
        }
        task_path_json = json.dumps(task_poses, sort_keys=True, separators=(",", ":"))
        self.task_path_hash = hashlib.sha256(task_path_json.encode("utf-8")).hexdigest()
        self.logger.event(
            "action", "fixed_task_library_registered",
            data={"task_path_hash": self.task_path_hash, "pose_names": task_pose_names,
                  "shared_across_conditions": True, "config_version": self.config.version},
        )

    async def connect(self) -> RobotSnapshot:
        await asyncio.to_thread(self.adapter.connect)
        snapshot = await asyncio.to_thread(self.adapter.snapshot)
        self.logger.event("robot", "connected", data=self._snapshot_data(snapshot))
        self.validator.validate_robot_snapshot(snapshot, 0)
        return snapshot

    async def close(self) -> None:
        await asyncio.to_thread(self.adapter.close)
        self.logger.event("robot", "disconnected")

    async def emergency_stop(self, reason: str, round_id: int) -> None:
        """Bypasses the normal motion queue so it can stop the active action."""
        self._stopped = True
        self.logger.event(
            "safety", "emergency_stop_requested", level="CRITICAL", round_id=round_id,
            data={"reason": reason, "bypassed_motion_queue": True},
        )
        deceleration = float(self.config.safety["stop_deceleration_rad_s2"])
        try:
            result = await asyncio.to_thread(self.adapter.emergency_stop, deceleration)
            self.logger.event(
                "robot", "emergency_stop_result", level="CRITICAL", round_id=round_id,
                data={"sdk_result": repr(result), "deceleration_rad_s2": deceleration},
            )
        except Exception as exc:
            self.logger.event(
                "robot", "emergency_stop_failed", level="CRITICAL", round_id=round_id,
                data={"error": str(exc), "physical_estop_required": True},
            )
            raise

    async def execute_expression(self, plan: ActionPlan, round_id: int) -> None:
        if self._stopped:
            raise RobotAdapterError("Motion is disabled after emergency stop")
        async with self._motion_lock:
            self._log_calibration_gate("expressive", round_id)
            started = time.monotonic()
            before = await asyncio.to_thread(self.adapter.snapshot)
            self.validator.validate_robot_snapshot(before, round_id)
            self.logger.event(
                "action", "expression_started", round_id=round_id,
                data={"plan": plan.as_dict(), "robot_before": self._snapshot_data(before)},
            )
            try:
                if plan.function == "neutral_wait":
                    await asyncio.sleep(
                        float(plan.parameters["duration_s"]) * self.config.time_scale
                    )
                else:
                    targets = self._expression_targets(before.joint_positions_rad, plan)
                    self.logger.event(
                        "action", "motion_rendered", round_id=round_id,
                        data={"function": plan.function, "variant": plan.variant,
                              "validated_parameters": plan.parameters,
                              "rendered_joint_targets_rad": targets,
                              "template_version": self.config.version},
                    )
                    for index, target in enumerate(targets):
                        await self._move(target, plan.parameters, round_id, f"{plan.function}.{index + 1}")
                        if index == 0:
                            hold = float(plan.parameters.get("hold_s", plan.parameters.get("dwell_s", 0)))
                            if hold:
                                await asyncio.sleep(hold * self.config.time_scale)
                after = await asyncio.to_thread(self.adapter.snapshot)
                self.logger.event(
                    "action", "expression_completed", round_id=round_id,
                    data={"plan": plan.as_dict(), "duration_s": time.monotonic() - started,
                          "robot_after": self._snapshot_data(after)},
                )
            except Exception as exc:
                self.logger.event(
                    "action", "expression_failed", level="ERROR", round_id=round_id,
                    data={"plan": plan.as_dict(), "error": str(exc),
                          "duration_s": time.monotonic() - started},
                )
                raise

    async def execute_pick(self, cup_id: int, round_id: int) -> None:
        async with self._motion_lock:
            self._log_calibration_gate("task", round_id)
            sequence = [f"cup_{cup_id}_pre", f"cup_{cup_id}_pick"]
            self.logger.event("action", "task_pick_started", round_id=round_id, data={"cup_id": cup_id})
            for pose_name in sequence:
                await self._move_named_pose(pose_name, round_id)
            result = await asyncio.to_thread(self.gripper.close)
            self.logger.event("robot", "gripper_close", round_id=round_id, data={"result": repr(result)})
            await self._move_named_pose(f"cup_{cup_id}_lift", round_id)
            self.logger.event("action", "task_pick_completed", round_id=round_id, data={"cup_id": cup_id})

    async def execute_place(self, round_id: int) -> None:
        async with self._motion_lock:
            self._log_calibration_gate("task", round_id)
            self.logger.event("action", "task_place_started", round_id=round_id)
            for pose_name in ("place_pre", "place"):
                await self._move_named_pose(pose_name, round_id)
            result = await asyncio.to_thread(self.gripper.open)
            self.logger.event("robot", "gripper_open", round_id=round_id, data={"result": repr(result)})
            for pose_name in ("place_retreat", "home"):
                await self._move_named_pose(pose_name, round_id)
            self.logger.event("action", "task_place_completed", round_id=round_id)

    async def _move_named_pose(self, pose_name: str, round_id: int) -> None:
        poses = self.config.workcell.get("joint_poses_rad", {})
        target = poses.get(pose_name)
        found = isinstance(target, list) and len(target) == 6
        self.logger.safety_check(
            "named_pose_registered", found, requested=pose_name,
            limits={"registered_poses": sorted(poses)}, validated=target if found else None,
            round_id=round_id,
        )
        if not found:
            raise RobotAdapterError(f"Missing calibrated named pose: {pose_name}")
        await self._move(tuple(float(item) for item in target), {}, round_id, pose_name)

    async def _move(
        self, target: Sequence[float], parameters: dict[str, Any], round_id: int, label: str
    ) -> None:
        snapshot = await asyncio.to_thread(self.adapter.snapshot)
        self.validator.validate_robot_snapshot(snapshot, round_id)
        target_values = tuple(float(item) for item in target)
        finite_and_six = len(target_values) == 6 and all(abs(item) < float("inf") for item in target_values)
        self.logger.safety_check(
            "joint_target_shape_and_finiteness", finite_and_six,
            requested={"label": label, "target_rad": target_values},
            limits={"joint_count": 6, "finite": True}, validated=target_values if finite_and_six else None,
            round_id=round_id,
        )
        if not finite_and_six:
            raise RobotAdapterError("Invalid joint target")
        joint_min = tuple(float(item) for item in self.config.safety["joint_min_rad"])
        joint_max = tuple(float(item) for item in self.config.safety["joint_max_rad"])
        max_delta = tuple(float(item) for item in self.config.safety["max_command_delta_rad"])
        current = tuple(float(item) for item in snapshot.joint_positions_rad)
        per_joint = [
            {
                "joint": index + 1,
                "current_rad": current[index],
                "target_rad": target_values[index],
                "delta_rad": target_values[index] - current[index],
                "min_rad": joint_min[index],
                "max_rad": joint_max[index],
                "max_command_delta_rad": max_delta[index],
                "passed": joint_min[index] <= current[index] <= joint_max[index]
                and joint_min[index] <= target_values[index] <= joint_max[index]
                and abs(target_values[index] - current[index]) <= max_delta[index],
            }
            for index in range(6)
        ]
        joint_limits_ok = all(item["passed"] for item in per_joint)
        self.logger.safety_check(
            "joint_soft_limits_and_command_delta",
            joint_limits_ok,
            requested={"label": label, "current_rad": current, "target_rad": target_values},
            limits={
                "joint_min_rad": joint_min,
                "joint_max_rad": joint_max,
                "max_command_delta_rad": max_delta,
            },
            validated=per_joint,
            round_id=round_id,
        )
        if not joint_limits_ok:
            raise RobotAdapterError("Joint target violates soft limits or command-delta limits")
        base_velocity = float(self.config.safety["base_velocity_rad_s"])
        base_acceleration = float(self.config.safety["base_acceleration_rad_s2"])
        velocity = base_velocity * float(parameters.get("speed_scale", 0.35))
        acceleration = base_acceleration * float(parameters.get("accel_scale", 0.35))
        maximum_v = float(self.config.safety["max_velocity_rad_s"])
        maximum_a = float(self.config.safety["max_acceleration_rad_s2"])
        passed = 0 < velocity <= maximum_v and 0 < acceleration <= maximum_a
        self.logger.safety_check(
            "physical_speed_acceleration", passed,
            requested={"velocity_rad_s": velocity, "acceleration_rad_s2": acceleration},
            limits={"max_velocity_rad_s": maximum_v, "max_acceleration_rad_s2": maximum_a},
            validated={"velocity_rad_s": velocity, "acceleration_rad_s2": acceleration} if passed else None,
            round_id=round_id,
        )
        if not passed:
            raise RobotAdapterError("Physical velocity or acceleration exceeds configured limit")
        started = time.monotonic()
        result = await asyncio.to_thread(self.adapter.move_joint, target_values, acceleration, velocity)
        self.logger.event(
            "robot", "move_joint_result", round_id=round_id,
            data={"label": label, "target_rad": target_values, "velocity_rad_s": velocity,
                  "acceleration_rad_s2": acceleration, "sdk_result": repr(result),
                  "duration_s": time.monotonic() - started,
                  "all_safety_parameters": self.config.safety},
        )
        if type(result) is int and result != 0:
            raise RobotAdapterError(f"moveJoint returned error code {result}")

    def _expression_targets(self, start: Sequence[float], plan: ActionPlan) -> list[tuple[float, ...]]:
        templates = self.config.workcell.get("expressive_joint_offsets_rad", {})
        offset = templates.get(plan.function)
        if not isinstance(offset, list) or len(offset) != 6:
            raise RobotAdapterError(f"No calibrated joint template for {plan.function}")
        intensity = float(plan.parameters.get("intensity", 0.5))
        spatial_values = [
            float(plan.parameters[name]) / maximum
            for name, maximum in (
                ("lift_m", 0.15), ("sink_m", 0.15), ("retreat_m", 0.12),
                ("sway_rad", 0.21), ("oscillation_rad", 0.21), ("wrist_rad", 0.21),
            )
            if name in plan.parameters
        ]
        spatial_scale = sum(abs(item) for item in spatial_values) / len(spatial_values) if spatial_values else 0.5
        scale = min(1.0, max(0.2, 0.35 + 0.35 * intensity + 0.3 * spatial_scale))
        primary_values = [float(a) + float(b) * scale for a, b in zip(start, offset)]
        yaw = float(plan.parameters.get(
            "user_yaw_rad", plan.parameters.get(
                "orient_user_rad", -plan.parameters.get("turn_away_rad", 0.0)
            )
        ))
        wrist = float(plan.parameters.get("wrist_rad", 0.0))
        primary_values[0] += yaw * 0.22
        primary_values[3] += yaw * 0.12
        primary_values[4] += wrist * 0.25
        primary_values[5] -= yaw * 0.18
        primary = tuple(primary_values)
        inverse = tuple(float(a) - (float(b) - float(a)) * 0.35 for a, b in zip(start, primary))
        count = int(plan.parameters.get("sway_count", plan.parameters.get("recheck_count", 0)))
        targets: list[tuple[float, ...]] = []
        if plan.parameters.get("path_style") == "arc":
            arc_mid = [float(a) + (float(b) - float(a)) * 0.5 for a, b in zip(start, primary)]
            arc_mid[3] += 0.02 * scale
            targets.append(tuple(arc_mid))
        targets.append(primary)
        for _ in range(count):
            targets.extend((inverse, primary))
        targets.append(tuple(float(item) for item in start))
        return targets

    @staticmethod
    def _snapshot_data(snapshot: RobotSnapshot) -> dict[str, Any]:
        return asdict(snapshot)

    def _log_calibration_gate(self, motion_kind: str, round_id: int) -> None:
        configured = (
            self.config.expressive_calibrated
            if motion_kind == "expressive"
            else self.config.calibrated
        )
        passed = self.config.robot_mode == "mock" or configured
        self.logger.safety_check(
            f"{motion_kind}_calibration_gate",
            passed,
            requested={"robot_mode": self.config.robot_mode, "motion_kind": motion_kind},
            limits={"real_mode_requires_calibrated": True},
            validated={"calibrated": configured, "mock_simulation": self.config.robot_mode == "mock"},
            round_id=round_id,
        )
        if not passed:
            raise RobotAdapterError(f"{motion_kind} motion is not calibrated")


def build_adapter(config: StudyConfig, robot_config_path: str | Path) -> tuple[RobotAdapter, GripperAdapter]:
    if config.robot_mode == "mock":
        return MockRobotAdapter(config.workcell.get("joint_poses_rad", {}).get("home", (0,) * 6)), MockGripperAdapter()
    adapter = AuboRobotAdapter(robot_config_path)
    gripper_settings = config.workcell.get("gripper", {})
    if gripper_settings.get("type") == "standard_digital_output":
        return adapter, DigitalOutputGripperAdapter(adapter, gripper_settings)
    return adapter, UnsupportedGripperAdapter()
