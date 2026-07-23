"""读取并校验 AUBO 项目的连接配置与软件安全边界。

本模块把 JSON 与环境变量合并为不可变配置对象，并在建立连接前拒绝
缺失凭据、无效端口、错误关节数量及不合理的软限位参数。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """本地配置缺失、格式错误或数值不安全时抛出。"""


@dataclass(frozen=True)
class RobotConfig:
    """连接 AUBO 控制器所需的网络、账号和 RPC 超时配置。"""

    ip: str
    port: int
    username: str
    password: str
    request_timeout_ms: int = 3000


@dataclass(frozen=True)
class SafetyConfig:
    """六关节软限位、单步增量上限及运动准备等待时间。"""

    joint_count: int
    joint_min_rad: tuple[float, ...]
    joint_max_rad: tuple[float, ...]
    max_delta_rad: tuple[float, ...]
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    startup_wait_s: float
    power_on_wait_s: float


@dataclass(frozen=True)
class AppConfig:
    """应用使用的完整配置，由连接配置和安全配置组成。"""

    robot: RobotConfig
    safety: SafetyConfig


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON，并把缺失文件或解析错误转换为 ``ConfigError``。"""

    if not path.exists():
        raise ConfigError(
            f"配置文件不存在：{path}。请复制 config/robot.example.json 为 "
            "config/robot.local.json，并填写本机配置。"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是有效 JSON：{path}: {exc}") from exc


def _env(name: str, default: Any) -> Any:
    """读取非空环境变量；未设置或空字符串时使用配置文件默认值。"""

    value = os.getenv(name)
    return default if value is None or value == "" else value


def _tuple_of_floats(value: Any, field: str, count: int) -> tuple[float, ...]:
    """把固定长度 JSON 数组转换为浮点元组，并报告具体字段名。"""

    if not isinstance(value, list) or len(value) != count:
        raise ConfigError(f"{field} 必须是长度为 {count} 的数组。")
    try:
        return tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} 必须只包含数字。") from exc


def load_config(path: str | Path = "config/robot.local.json") -> AppConfig:
    """加载项目配置，并验证所有连接参数和关节安全参数。
    
    环境变量可覆盖 IP、端口、账号、密码和请求超时，便于把敏感信息留在
    版本库之外。
    """

    config_path = Path(path).expanduser().resolve()
    raw = _read_json(config_path)
    # 环境变量优先于 JSON，便于把账号密码留在版本库之外。
    robot_raw: Mapping[str, Any] = raw.get("robot", {})
    safety_raw: Mapping[str, Any] = raw.get("safety", {})

    ip = str(_env("AUBO_ROBOT_IP", robot_raw.get("ip", ""))).strip()
    username = str(_env("AUBO_USERNAME", robot_raw.get("username", ""))).strip()
    password = str(_env("AUBO_PASSWORD", robot_raw.get("password", "")))

    if not ip:
        raise ConfigError("robot.ip 或环境变量 AUBO_ROBOT_IP 不能为空。")
    if not username:
        raise ConfigError("robot.username 或环境变量 AUBO_USERNAME 不能为空。")
    if not password:
        raise ConfigError("robot.password 或环境变量 AUBO_PASSWORD 不能为空。")

    try:
        port = int(_env("AUBO_ROBOT_PORT", robot_raw.get("port", 30004)))
        timeout_ms = int(
            _env(
                "AUBO_REQUEST_TIMEOUT_MS",
                robot_raw.get("request_timeout_ms", 3000),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError("robot.port 和 request_timeout_ms 必须是整数。") from exc

    if not 1 <= port <= 65535:
        raise ConfigError("robot.port 必须在 1～65535 之间。")
    if timeout_ms <= 0:
        raise ConfigError("request_timeout_ms 必须大于 0。")

    try:
        joint_count = int(safety_raw.get("joint_count", 6))
        max_velocity = float(safety_raw.get("max_velocity_rad_s", 0.10))
        max_acceleration = float(safety_raw.get("max_acceleration_rad_s2", 0.10))
        startup_wait = float(safety_raw.get("startup_wait_s", 2.0))
        power_on_wait = float(safety_raw.get("power_on_wait_s", 3.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("安全参数包含无效数字。") from exc

    if joint_count != 6:
        raise ConfigError("当前 M0 工具只允许 AUBO ES3 的 6 个关节。")
    if max_velocity <= 0 or max_acceleration <= 0:
        raise ConfigError("速度和加速度限制必须大于 0。")

    joint_min = _tuple_of_floats(
        safety_raw.get("joint_min_rad"), "safety.joint_min_rad", joint_count
    )
    joint_max = _tuple_of_floats(
        safety_raw.get("joint_max_rad"), "safety.joint_max_rad", joint_count
    )
    max_delta = _tuple_of_floats(
        safety_raw.get("max_delta_rad"), "safety.max_delta_rad", joint_count
    )

    # 对每个关节逐项检查，错误信息可直接定位到 J1～J6。
    for index, (minimum, maximum, delta) in enumerate(
        zip(joint_min, joint_max, max_delta), start=1
    ):
        if minimum >= maximum:
            raise ConfigError(f"关节 J{index} 的最小限位必须小于最大限位。")
        if delta <= 0:
            raise ConfigError(f"关节 J{index} 的单次最大增量必须大于 0。")

    return AppConfig(
        robot=RobotConfig(ip, port, username, password, timeout_ms),
        safety=SafetyConfig(
            joint_count,
            joint_min,
            joint_max,
            max_delta,
            max_velocity,
            max_acceleration,
            startup_wait,
            power_on_wait,
        ),
    )
