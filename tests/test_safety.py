"""验证相对关节运动的长度、增量、软限位和零运动检查。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client.config import SafetyConfig
from aubo_sdk_client.safety import SafetyError, preview_relative_joint_move


# 使用确定的六关节边界测试纯安全计算，不连接机器人。
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


class SafetyTests(unittest.TestCase):
    """覆盖允许的小步运动与必须拒绝的危险或无效目标。"""

    def test_accepts_small_relative_move(self):
        """确认软限位内的小幅相对运动可以生成预览。"""

        preview = preview_relative_joint_move([0] * 6, [0, 0, 0, 0, 0, 0.02], SAFETY)
        self.assertAlmostEqual(preview.target_rad[5], 0.02)

    def test_rejects_large_delta(self):
        """确认超过单步增量上限的请求会被拒绝。"""

        with self.assertRaises(SafetyError):
            preview_relative_joint_move([0] * 6, [0.06, 0, 0, 0, 0, 0], SAFETY)

    def test_rejects_target_beyond_limit(self):
        """确认相加后的目标越过软限位时会被拒绝。"""

        with self.assertRaises(SafetyError):
            preview_relative_joint_move([2.99, 0, 0, 0, 0, 0], [0.02, 0, 0, 0, 0, 0], SAFETY)

    def test_rejects_zero_motion(self):
        """确认全零增量不会被误认为可执行运动。"""

        with self.assertRaises(SafetyError):
            preview_relative_joint_move([0] * 6, [0] * 6, SAFETY)


if __name__ == "__main__":
    unittest.main()
