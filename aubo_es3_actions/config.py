from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATTACHMENT_CONFIG = Path(
    "/home/jetson/.codex/attachments/bc1fc8d1-c3c1-47f1-b93c-80d5a5b9e176/robot.local.json"
)


@dataclass(frozen=True)
class SafetyConfig:
    joint_count: int
    joint_min_rad: List[float]
    joint_max_rad: List[float]
    max_delta_rad: List[float]
    max_velocity_rad_s: float
    max_acceleration_rad_s2: float
    power_on_wait_s: float
    startup_wait_s: float


@dataclass(frozen=True)
class GripperConfig:
    modbus_device: str
    slave_id: int
    baudrate: int
    data_bits: int
    parity: str
    stop_bits: int
    position_register: int
    force_register: int
    current_position_register: int
    torque_register: int
    done_register: int
    find_stroke_register: int
    stroke_not_found_register: int
    speed_register: int
    speed_save_register: int
    auto_find_stroke_register: int
    set_address_register: int


@dataclass(frozen=True)
class RobotConfig:
    ip: str
    port: int
    username: str
    password: str
    request_timeout_ms: int
    safety: SafetyConfig
    gripper: GripperConfig
    source_path: Path


def default_config_paths() -> List[Path]:
    paths: List[Path] = []
    env_path = os.environ.get("AUBO_ROBOT_CONFIG")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(PROJECT_ROOT / "config" / "robot.local.json")
    paths.append(ATTACHMENT_CONFIG)
    return paths


def find_config_path(explicit_path: Optional[str] = None) -> Path:
    candidates = [Path(explicit_path).expanduser()] if explicit_path else default_config_paths()
    for path in candidates:
        if path.exists():
            return path
    searched = "\n".join(f"  - {p}" for p in candidates)
    raise FileNotFoundError(f"未找到机器人配置文件，已查找:\n{searched}")


def load_config(path: Optional[str] = None) -> RobotConfig:
    config_path = find_config_path(path)
    raw_text = config_path.read_text(encoding="utf-8-sig")
    data = json.loads(raw_text)

    robot = data["robot"]
    safety = data["safety"]
    gripper = data.get("gripper", {})
    safety_config = SafetyConfig(
        joint_count=int(safety["joint_count"]),
        joint_min_rad=[float(v) for v in safety["joint_min_rad"]],
        joint_max_rad=[float(v) for v in safety["joint_max_rad"]],
        max_delta_rad=[float(v) for v in safety["max_delta_rad"]],
        max_velocity_rad_s=float(safety["max_velocity_rad_s"]),
        max_acceleration_rad_s2=float(safety["max_acceleration_rad_s2"]),
        power_on_wait_s=float(safety["power_on_wait_s"]),
        startup_wait_s=float(safety["startup_wait_s"]),
    )
    gripper_config = GripperConfig(
        modbus_device=str(gripper.get("modbus_device", "Modbus_0")),
        slave_id=int(gripper.get("slave_id", 1)),
        baudrate=int(gripper.get("baudrate", 115200)),
        data_bits=int(gripper.get("data_bits", 8)),
        parity=str(gripper.get("parity", "N")),
        stop_bits=int(gripper.get("stop_bits", 1)),
        position_register=int(gripper.get("position_register", 0x9C40)),
        force_register=int(gripper.get("force_register", 0x9C41)),
        current_position_register=int(gripper.get("current_position_register", 0x9C45)),
        torque_register=int(gripper.get("torque_register", 0x9C46)),
        done_register=int(gripper.get("done_register", 0x9C47)),
        find_stroke_register=int(gripper.get("find_stroke_register", 0x9C48)),
        stroke_not_found_register=int(gripper.get("stroke_not_found_register", 0x9C49)),
        speed_register=int(gripper.get("speed_register", 0x9C4A)),
        speed_save_register=int(gripper.get("speed_save_register", 0x9C4B)),
        auto_find_stroke_register=int(gripper.get("auto_find_stroke_register", 0x9C9A)),
        set_address_register=int(gripper.get("set_address_register", 0x9C9B)),
    )
    return RobotConfig(
        ip=str(robot["ip"]),
        port=int(robot["port"]),
        username=str(robot["username"]),
        password=str(robot["password"]),
        request_timeout_ms=int(robot["request_timeout_ms"]),
        safety=safety_config,
        gripper=gripper_config,
        source_path=config_path,
    )
