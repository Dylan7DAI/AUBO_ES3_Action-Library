"""验证配置文件加载、环境变量覆盖和必填凭据检查。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aubo_sdk_client.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    """使用临时 JSON 文件隔离测试配置解析逻辑。"""

    def setUp(self):
        """保存环境并清除 AUBO 覆盖项，避免本机变量污染测试。"""

        self.original_env = os.environ.copy()
        for key in (
            "AUBO_ROBOT_IP",
            "AUBO_ROBOT_PORT",
            "AUBO_USERNAME",
            "AUBO_PASSWORD",
            "AUBO_REQUEST_TIMEOUT_MS",
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        """完整恢复测试开始前的环境变量。"""

        os.environ.clear()
        os.environ.update(self.original_env)

    def write_config(self, payload):
        """写入一次性配置文件，并登记测试结束后的清理动作。"""

        temp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        with temp:
            json.dump(payload, temp)
        self.addCleanup(lambda: Path(temp.name).unlink(missing_ok=True))
        return temp.name

    def valid_payload(self):
        """返回可按需修改的最小有效六关节配置。"""

        return {
            "robot": {
                "ip": "127.0.0.1",
                "port": 30004,
                "username": "user",
                "password": "secret",
            },
            "safety": {
                "joint_count": 6,
                "joint_min_rad": [-3] * 6,
                "joint_max_rad": [3] * 6,
                "max_delta_rad": [0.05] * 6,
                "max_velocity_rad_s": 0.1,
                "max_acceleration_rad_s2": 0.1,
                "power_on_wait_s": 0,
                "startup_wait_s": 0,
            },
        }

    def test_load_valid_config(self):
        """确认最小有效配置能够加载为结构化对象。"""

        config = load_config(self.write_config(self.valid_payload()))
        self.assertEqual(config.robot.ip, "127.0.0.1")
        self.assertEqual(config.safety.joint_count, 6)

    def test_environment_overrides_secret(self):
        """确认环境变量中的密码优先于 JSON 配置。"""

        os.environ["AUBO_PASSWORD"] = "from-env"
        config = load_config(self.write_config(self.valid_payload()))
        self.assertEqual(config.robot.password, "from-env")

    def test_rejects_missing_password(self):
        """确认缺少密码时在连接前立即拒绝配置。"""

        payload = self.valid_payload()
        payload["robot"]["password"] = ""
        with self.assertRaises(ConfigError):
            load_config(self.write_config(payload))


if __name__ == "__main__":
    unittest.main()
