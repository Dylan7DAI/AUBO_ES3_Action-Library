"""离线验证水杯往返轨迹、速度上限、错误码和 TCP 误差计算。"""

import sys
import unittest
from math import isfinite, pi
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client.config import SafetyConfig
from aubo_sdk_client.safety import SafetyError
from host_tools.cup_action import (
    CUP_JOINTS_RAD,
    CUP_TCP_POSE,
    DEFAULT_CUP_HOLD_S,
    MAX_ACTION_ACCELERATION_RAD_S2,
    MAX_ACTION_VELOCITY_RAD_S,
    MOTION_RPC_TIMEOUT_MS,
    NORMAL_ACTION_ACCELERATION_RAD_S2,
    NORMAL_ACTION_VELOCITY_RAD_S,
    build_cup_round_trip,
    build_parser,
    move_error_description,
    tcp_pose_error,
    validate_motion_parameters,
)


# 测试使用宽松的 ±π 软限位，不连接真实控制器。
SAFETY = SafetyConfig(
    joint_count=6,
    joint_min_rad=(-pi,) * 6,
    joint_max_rad=(pi,) * 6,
    max_delta_rad=(0.05,) * 6,
    max_velocity_rad_s=0.1,
    max_acceleration_rad_s2=0.1,
    startup_wait_s=0,
    power_on_wait_s=0,
)


class CupActionTests(unittest.TestCase):
    """确保水杯动作保持“两步往返、只在杯位停顿”的约束。"""

    def test_supplied_cup_data_are_six_finite_values(self):
        """确认水杯关节目标和参考 TCP 都是六个有限数。"""

        self.assertEqual(len(CUP_JOINTS_RAD), 6)
        self.assertEqual(len(CUP_TCP_POSE), 6)
        self.assertTrue(all(isfinite(value) for value in CUP_JOINTS_RAD + CUP_TCP_POSE))

    def test_plan_contains_only_cup_then_original_normal_pose(self):
        """确认计划只包含前往水杯和返回原姿态两步。"""

        normal = (0.10, -0.20, 0.30, -0.40, 0.50, -0.60)
        steps = build_cup_round_trip(normal, SAFETY)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0].target_rad, CUP_JOINTS_RAD)
        self.assertEqual(steps[1].target_rad, normal)

    def test_only_cup_step_has_a_deliberate_hold(self):
        """确认主动停顿只发生在水杯位置。"""

        steps = build_cup_round_trip((0.0,) * 6, SAFETY, cup_hold_s=2.5)
        self.assertEqual(steps[0].hold_s, 2.5)
        self.assertEqual(steps[1].hold_s, 0.0)

    def test_cup_target_is_inside_configured_soft_limits(self):
        """确认默认水杯目标位于测试软限位内。"""

        steps = build_cup_round_trip((0.0,) * 6, SAFETY)
        self.assertEqual(steps[0].target_rad, CUP_JOINTS_RAD)

    def test_rejects_cup_target_when_soft_limit_is_tighter(self):
        """确认收紧软限位后会拒绝越界的水杯目标。"""

        tight = SafetyConfig(
            joint_count=6,
            joint_min_rad=(-1.0,) * 6,
            joint_max_rad=(1.0,) * 6,
            max_delta_rad=(0.05,) * 6,
            max_velocity_rad_s=0.1,
            max_acceleration_rad_s2=0.1,
            startup_wait_s=0,
            power_on_wait_s=0,
        )
        with self.assertRaises(SafetyError):
            build_cup_round_trip((0.0,) * 6, tight)

    def test_normal_speed_defaults_and_hard_upper_bounds(self):
        """确认默认速度合理且超过硬上限会被拒绝。"""

        args = build_parser().parse_args([])
        self.assertEqual(args.velocity, NORMAL_ACTION_VELOCITY_RAD_S)
        self.assertEqual(args.acceleration, NORMAL_ACTION_ACCELERATION_RAD_S2)
        self.assertEqual(args.cup_hold, DEFAULT_CUP_HOLD_S)
        with self.assertRaises(SafetyError):
            validate_motion_parameters(MAX_ACTION_VELOCITY_RAD_S + 0.01, 0.7)
        with self.assertRaises(SafetyError):
            validate_motion_parameters(0.35, MAX_ACTION_ACCELERATION_RAD_S2 + 0.01)

    def test_motion_rpc_timeout_is_long_enough_for_large_move(self):
        """确认运动 RPC 超时覆盖较长关节动作所需时间。"""

        self.assertGreaterEqual(MOTION_RPC_TIMEOUT_MS, 30_000)

    def test_return_code_four_is_reported_as_timeout(self):
        """确认 SDK 返回码 4 会被解释为运动超时。"""

        self.assertIn("超时", move_error_description(4))
        self.assertIn("参数", move_error_description(5))

    def test_tcp_pose_error_handles_equivalent_rotation_vectors(self):
        """确认等价旋转向量不会产生虚假的姿态误差。"""

        position_error, orientation_error = tcp_pose_error(
            CUP_TCP_POSE, CUP_TCP_POSE
        )
        self.assertAlmostEqual(position_error, 0.0)
        self.assertAlmostEqual(orientation_error, 0.0)


if __name__ == "__main__":
    unittest.main()

