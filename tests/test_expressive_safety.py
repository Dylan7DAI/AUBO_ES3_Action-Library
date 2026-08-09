from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aubo_es3_actions.config import SafetyConfig
from aubo_es3_actions.emergency_stop import (
    EmergencyStopMonitor,
    EmergencyStopRequested,
)
from aubo_es3_actions.expressive_safety import (
    ExpressiveSafetyError,
    ExpressiveSafetyLimits,
    interpolate_vector,
    sample_joint_keyframes,
    load_expressive_limits,
    validate_tcp_pose,
    validate_joint_keyframes,
    validate_sequential_ik,
)


def robot_limits() -> SafetyConfig:
    return SafetyConfig(
        joint_count=6,
        joint_min_rad=[-3.0] * 6,
        joint_max_rad=[3.0] * 6,
        max_delta_rad=[0.1] * 6,
        max_velocity_rad_s=0.5,
        max_acceleration_rad_s2=1.0,
        power_on_wait_s=0.0,
        startup_wait_s=0.0,
    )


def expressive_limits() -> ExpressiveSafetyLimits:
    return ExpressiveSafetyLimits(
        max_segment_joint_delta_rad=1.0,
        max_sample_joint_delta_rad=0.05,
        max_velocity_rad_s=0.3,
        max_acceleration_rad_s2=1.0,
        max_jerk_rad_s3=10.0,
        sample_period_s=0.05,
        workspace_min_m=(-0.1, -0.4, 0.1),
        workspace_max_m=(0.6, 0.3, 0.7),
        max_orientation_residual_rad=0.35,
        max_ik_step_rad=0.12,
        require_collision_model_for_cartesian=True,
    )


class ExpressiveSafetyTests(unittest.TestCase):
    def test_interpolate_vector(self) -> None:
        self.assertEqual(
            interpolate_vector([0.0, 1.0], [2.0, 3.0], 0.5),
            [1.0, 2.0],
        )

    def test_joint_keyframe_span_is_rejected(self) -> None:
        with self.assertRaises(ExpressiveSafetyError):
            validate_joint_keyframes(
                [[0.0] * 6, [1.1] + [0.0] * 5],
                robot_limits(),
                expressive_limits(),
            )

    def test_quintic_sampler_preserves_endpoints(self) -> None:
        points = sample_joint_keyframes(
            [[0.0] * 6, [0.2] * 6],
            max_velocity_rad_s=0.3,
            sample_period_s=0.05,
        )
        self.assertEqual(points[0], [0.0] * 6)
        self.assertEqual(points[-1], [0.2] * 6)

    def test_sampler_stretches_small_motion_for_jerk(self) -> None:
        points = sample_joint_keyframes(
            [[0.0] * 6, [0.01] * 6],
            max_velocity_rad_s=0.3,
            sample_period_s=0.05,
            max_acceleration_rad_s2=0.6,
            max_jerk_rad_s3=4.0,
        )
        self.assertGreater(len(points), 9)

    def test_recorded_tcp_poses_fit_configured_workspace(self) -> None:
        limits = load_expressive_limits(
            Path("config/expressive_motion_limits.json")
        )
        pose_data = json.loads(
            Path("config/emotion_poses_new_es3.json").read_text(encoding="utf-8")
        )
        checked = 0
        expressive_references = (
            "anticipation_look_down",
            "joy_lift_max",
            "joy_shout_peak_max",
            "disappointment_turn_away",
            "disappointment_retreat_max",
        )
        for name in expressive_references:
            tcp_pose = pose_data["poses"][name].get("tcp_pose")
            if tcp_pose is not None:
                validate_tcp_pose(tcp_pose, limits)
                checked += 1
        self.assertGreater(checked, 0)

    def test_sequential_ik_branch_jump_is_rejected(self) -> None:
        with self.assertRaises(ExpressiveSafetyError):
            validate_sequential_ik(
                [[0.0] * 6, [0.13] + [0.0] * 5],
                expressive_limits(),
            )

    def test_stop_file_is_latched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "stop"
            monitor = EmergencyStopMonitor(stop_file)
            monitor.request("test")
            with self.assertRaises(EmergencyStopRequested):
                monitor.assert_clear()


if __name__ == "__main__":
    unittest.main()
