"""离线验证点头轨迹插值、回零、软限位和动作速度硬上限。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client.config import SafetyConfig
from aubo_sdk_client.safety import SafetyError
from host_tools.nod_action import (
    MAX_ACTION_ACCELERATION_RAD_S2,
    MAX_ACTION_VELOCITY_RAD_S,
    MAX_BODY_SCALE,
    NORMAL_ACTION_ACCELERATION_RAD_S2,
    NORMAL_ACTION_VELOCITY_RAD_S,
    build_full_body_nod_waypoints,
    build_nod_waypoints,
    build_wrist_nod_waypoints,
    build_parser,
    validate_motion_parameters,
)


# 测试夹具只用于纯计算轨迹生成，不会调用 AUBO SDK。
SAFETY = SafetyConfig(
    joint_count=6,
    joint_min_rad=(-3.0,) * 6,
    joint_max_rad=(3.0,) * 6,
    max_delta_rad=(0.05,) * 6,
    max_velocity_rad_s=0.1,
    max_acceleration_rad_s2=0.1,
    startup_wait_s=0,
    power_on_wait_s=0,
)


class NodActionTests(unittest.TestCase):
    """检查全身与单腕两种点头风格的安全不变量。"""

    def test_full_body_nod_returns_to_initial_pose(self):
        """确认全身点头的最后一个 waypoint 回到起始姿态。"""

        current = (0.1, -0.2, 0.3, -0.4, 0.5, -0.6)
        waypoints = build_full_body_nod_waypoints(current, SAFETY)
        self.assertGreater(len(waypoints), 10)
        for actual, expected in zip(waypoints[-1].target_rad, current):
            self.assertAlmostEqual(actual, expected)

    def test_full_body_nod_moves_all_six_joints(self):
        """确认全身模式确实让六个关节都参与动作。"""

        current = (0.0,) * 6
        waypoints = build_nod_waypoints(current, SAFETY, style="full-body")
        for joint_index in range(6):
            maximum_offset = max(abs(wp.target_rad[joint_index]) for wp in waypoints)
            self.assertGreater(maximum_offset, 0.02, f"J{joint_index + 1} did not move")

    def test_every_interpolated_step_stays_below_configured_limit(self):
        """确认插值后的每一步都保留单步限制余量。"""

        current = (0.0,) * 6
        waypoints = build_full_body_nod_waypoints(current, SAFETY)
        previous = current
        for waypoint in waypoints:
            for index, (target, start) in enumerate(zip(waypoint.target_rad, previous)):
                self.assertLessEqual(
                    abs(target - start),
                    SAFETY.max_delta_rad[index] * 0.8 + 1e-9,
                )
            previous = waypoint.target_rad

    def test_rejects_excessive_full_body_scale(self):
        """确认超过全身动作倍率硬上限时拒绝生成轨迹。"""

        with self.assertRaises(SafetyError):
            build_full_body_nod_waypoints(
                (0.0,) * 6, SAFETY, scale=MAX_BODY_SCALE + 0.01
            )

    def test_rejects_motion_near_joint_limit(self):
        """确认起点靠近软限位且动作会越界时被拒绝。"""

        current = (0.0, 0.0, 0.0, 0.0, 2.95, 0.0)
        with self.assertRaises(SafetyError):
            build_full_body_nod_waypoints(current, SAFETY)

    def test_multiple_cycles_return_to_start(self):
        """确认重复多个周期后仍回到同一起始姿态。"""

        current = (0.0,) * 6
        one_cycle = build_full_body_nod_waypoints(current, SAFETY, count=1)
        three_cycles = build_full_body_nod_waypoints(current, SAFETY, count=3)
        self.assertEqual(len(three_cycles), len(one_cycle) * 3)
        self.assertEqual(three_cycles[-1].target_rad, current)

    def test_original_wrist_style_remains_available(self):
        """确认单腕兼容模式只移动指定关节。"""

        current = (0.0,) * 6
        waypoints = build_wrist_nod_waypoints(
            current, SAFETY, joint_number=5, amplitude_rad=0.04
        )
        self.assertEqual(waypoints[-1].target_rad, current)
        for waypoint in waypoints:
            for index in (0, 1, 2, 3, 5):
                self.assertAlmostEqual(waypoint.target_rad[index], 0.0)

    def test_normal_speed_defaults_are_used(self):
        """确认命令行采用点头动作专用的默认速度参数。"""

        args = build_parser().parse_args([])
        self.assertEqual(args.velocity, NORMAL_ACTION_VELOCITY_RAD_S)
        self.assertEqual(args.acceleration, NORMAL_ACTION_ACCELERATION_RAD_S2)
        self.assertGreater(args.velocity, SAFETY.max_velocity_rad_s)
        self.assertGreater(args.acceleration, SAFETY.max_acceleration_rad_s2)

    def test_action_speed_has_a_hard_upper_bound(self):
        """确认点头速度和加速度不能超过代码硬上限。"""

        with self.assertRaises(SafetyError):
            validate_motion_parameters(MAX_ACTION_VELOCITY_RAD_S + 0.01, 0.7)
        with self.assertRaises(SafetyError):
            validate_motion_parameters(0.35, MAX_ACTION_ACCELERATION_RAD_S2 + 0.01)

    def test_normal_action_has_short_holds(self):
        """确认全身动作的关键姿态停顿总时长保持较短。"""

        waypoints = build_full_body_nod_waypoints((0.0,) * 6, SAFETY)
        self.assertLessEqual(sum(waypoint.hold_s for waypoint in waypoints), 0.60)

if __name__ == "__main__":
    unittest.main()
