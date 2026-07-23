"""AUBO SDK 安全访问包的公共接口。

集中导出配置加载、RPC 客户端、状态快照和关节安全校验类型，使上层工具
无需依赖各内部模块的具体组织方式。
"""

from .client import AuboClient, AuboClientError, RobotSnapshot
from .config import AppConfig, ConfigError, load_config
from .safety import (
    MotionPreview,
    SafetyError,
    preview_relative_joint_move,
    validate_absolute_joint_target,
)

# 明确公共 API，避免调用方误用内部辅助函数。
__all__ = [
    "AppConfig",
    "AuboClient",
    "AuboClientError",
    "ConfigError",
    "MotionPreview",
    "RobotSnapshot",
    "SafetyError",
    "load_config",
    "preview_relative_joint_move",
    "validate_absolute_joint_target",
]
