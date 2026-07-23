"""离线验证只读诊断能够清晰输出当前关节位置和 TCP 位姿。"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from host_tools.diagnose_robot import print_pose
from aubo_sdk_client import RobotSnapshot


class DiagnoseRobotTests(unittest.TestCase):
    """确保重要位姿数据的名称、数值和单位都出现在诊断输出中。"""

    def test_print_pose_contains_joint_and_tcp_coordinates(self):
        """确认文本模式同时输出六轴关节角和 TCP 六维位姿。"""

        snapshot = RobotSnapshot(
            robot_name="robot1",
            power_on=True,
            joint_positions_rad=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6),
            tcp_pose=(0.11, 0.22, 0.33, 0.01, 0.02, 0.03),
        )
        output = io.StringIO()

        with redirect_stdout(output):
            print_pose(snapshot)

        text = output.getvalue()
        self.assertIn("当前关节位置", text)
        self.assertIn("J1: 0.100000 rad", text)
        self.assertIn("J6: 0.600000 rad", text)
        self.assertIn("位置 X/Y/Z (m)：0.110000, 0.220000, 0.330000", text)
        self.assertIn("姿态 RX/RY/RZ (rad)：0.010000, 0.020000, 0.030000", text)


if __name__ == "__main__":
    unittest.main()