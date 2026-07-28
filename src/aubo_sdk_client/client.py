"""对 ``pyaubo_sdk.RpcClient`` 的小型显式封装。

统一处理连接、登录、机器人发现、状态读取、运动前准备和异常转换。
厂商 SDK 采用延迟导入，因此离线配置测试不要求安装或连接真机。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .config import RobotConfig


class AuboClientError(RuntimeError):
    """SDK 连接、认证、状态读取或运动调用失败时抛出。"""


@dataclass(frozen=True)
class RobotSnapshot:
    """一次只读状态采样；关节单位为 rad，TCP 沿用 SDK 的米/弧度。"""

    robot_name: str
    power_on: bool
    joint_positions_rad: tuple[float, ...]
    tcp_pose: tuple[float, ...]
    sdk_version: str = "unknown"
    robot_mode: str = "unknown"
    safety_mode: str = "unknown"
    steady: bool | None = None
    collision_occurred: bool | None = None
    within_safety_limits: bool | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)


class AuboClient:
    """可用 ``with`` 管理生命周期的 AUBO RPC 客户端。
    
    ``pyaubo_sdk`` 在真正连接时才导入，使没有厂商扩展的机器仍可运行配置
    和安全逻辑测试；退出上下文时会尽力注销并断开连接。
    """

    def __init__(self, config: RobotConfig):
        """保存连接配置；构造对象本身不会访问网络或导入厂商 SDK。"""

        self.config = config
        self.rpc: Any | None = None
        self.robot: Any | None = None
        self.robot_name: str | None = None

    def connect(self) -> "AuboClient":
        """连接并登录控制器，选择发现到的第一台机器人。"""

        try:
            import pyaubo_sdk
        except ImportError as exc:
            raise AuboClientError(
                "未安装 pyaubo_sdk。请使用项目记录的 aubo Conda 环境运行。"
            ) from exc

        # 只有进入连接路径才创建厂商 RPC 对象，便于其余模块离线使用。
        rpc = pyaubo_sdk.RpcClient()
        try:
            rpc.setRequestTimeout(self.config.request_timeout_ms)
            connect_result = rpc.connect(self.config.ip, self.config.port)
            if not rpc.hasConnected():
                raise RuntimeError(
                    f"connect 返回 {connect_result!r}，但 SDK 未进入已连接状态"
                )
            login_result = rpc.login(self.config.username, self.config.password)
            if not rpc.hasLogined():
                raise RuntimeError(
                    f"login 返回 {login_result!r}，但 SDK 未进入已登录状态"
                )
            names = list(rpc.getRobotNames())
        except Exception as exc:
            try:
                rpc.disconnect()
            except Exception:
                pass
            raise AuboClientError(
                f"连接或登录 AUBO 失败（{self.config.ip}:{self.config.port}）：{exc}"
            ) from exc

        if not names:
            try:
                rpc.logout()
                rpc.disconnect()
            except Exception:
                pass
            raise AuboClientError("已连接控制器，但没有发现机器人。")

        self.rpc = rpc
        self.robot_name = str(names[0])
        self.robot = rpc.getRobotInterface(names[0])
        return self

    def close(self) -> None:
        """尽力注销和断开连接；清理失败不会掩盖原始业务异常。"""

        rpc = self.rpc
        # 先清空本地引用，再尽力释放远端会话。
        self.robot = None
        self.robot_name = None
        self.rpc = None
        if rpc is None:
            return
        try:
            if rpc.hasLogined():
                rpc.logout()
        except Exception:
            pass
        try:
            if rpc.hasConnected():
                rpc.disconnect()
        except Exception:
            pass

    def __enter__(self) -> "AuboClient":
        """进入 ``with`` 块时建立连接。"""

        return self.connect()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """离开 ``with`` 块时始终释放 RPC 资源。"""

        self.close()

    def _require_robot(self) -> Any:
        """返回已连接的机器人接口，未连接时给出统一错误。"""

        if self.robot is None or self.robot_name is None:
            raise AuboClientError("尚未连接机器人。")
        return self.robot

    def set_request_timeout(self, timeout_ms: int) -> None:
        """调整后续 RPC 的超时，供可能持续较久的运动调用使用。"""

        rpc = self.rpc
        if rpc is None:
            raise AuboClientError("尚未连接机器人。")
        timeout_value = int(timeout_ms)
        if timeout_value <= 0:
            raise AuboClientError("RPC 请求超时必须大于 0 ms。")
        try:
            result = rpc.setRequestTimeout(timeout_value)
        except Exception as exc:
            raise AuboClientError(f"设置 RPC 请求超时失败：{exc}") from exc
        if type(result) is int and result != 0:
            raise AuboClientError(
                f"设置 RPC 请求超时为 {timeout_value} ms 时返回错误码 {result}。"
            )

    def snapshot(self) -> RobotSnapshot:
        """读取并冻结状态及所有可用的软件安全遥测。

        不同控制器/SDK 版本公开的状态字段并不完全一致。基础字段失败会让
        整次采样失败；扩展安全字段逐项读取并把不支持项记为 ``None``，让
        上层日志既完整又不会因旧固件缺少单个接口而中断。
        """

        robot = self._require_robot()
        try:
            state = robot.getRobotState()
            def read(method_name: str) -> Any:
                method = getattr(state, method_name, None)
                if method is None:
                    return None
                try:
                    value = method()
                    if isinstance(value, (list, tuple)):
                        return [float(item) for item in value]
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        return value
                    return str(value)
                except Exception as exc:  # Preserve unsupported/read-error detail in logs.
                    return {"read_error": str(exc)}

            try:
                sdk_version = version("pyaubo-sdk")
            except PackageNotFoundError:
                sdk_version = "unknown"

            telemetry_methods = {
                "joint_speeds_rad_s": "getJointSpeeds",
                "joint_accelerations_rad_s2": "getJointAccelerations",
                "joint_torque_sensors": "getJointTorqueSensors",
                "joint_contact_torques": "getJointContactTorques",
                "joint_currents_a": "getJointCurrents",
                "joint_voltages_v": "getJointVoltages",
                "joint_temperatures_c": "getJointTemperatures",
                "tcp_speed": "getTcpSpeed",
                "tcp_force": "getTcpForce",
                "tcp_force_sensors": "getTcpForceSensors",
                "control_box_temperature_c": "getControlBoxTemperature",
                "control_box_humidity_percent": "getControlBoxHumidity",
                "main_voltage_v": "getMainVoltage",
                "main_current_a": "getMainCurrent",
                "robot_voltage_v": "getRobotVoltage",
                "robot_current_a": "getRobotCurrent",
                "slow_down_level": "getSlowDownLevel",
                "teach_pendant_enabled": "isTeachPendantEnabled",
                "tool_flange_enabled": "isToolFlangeEnabled",
            }
            telemetry = {name: read(method) for name, method in telemetry_methods.items()}
            robot_mode = read("getRobotModeType")
            safety_mode = read("getSafetyModeType")
            steady = read("isSteady")
            collision = read("isCollisionOccurred")
            within_limits = read("isWithinSafetyLimits")
            return RobotSnapshot(
                robot_name=self.robot_name or "unknown",
                power_on=bool(state.isPowerOn()),
                joint_positions_rad=tuple(float(v) for v in state.getJointPositions()),
                tcp_pose=tuple(float(v) for v in state.getTcpPose()),
                sdk_version=sdk_version,
                robot_mode=str(robot_mode),
                safety_mode=str(safety_mode),
                steady=steady if isinstance(steady, bool) else None,
                collision_occurred=collision if isinstance(collision, bool) else None,
                within_safety_limits=within_limits if isinstance(within_limits, bool) else None,
                telemetry=telemetry,
            )
        except Exception as exc:
            raise AuboClientError(f"读取机器人状态失败：{exc}") from exc

    def prepare_for_motion(self, power_on_wait_s: float, startup_wait_s: float) -> None:
        """在调用方完成显式确认后，按需上电并启动机器人。"""
        import time

        robot = self._require_robot()
        try:
            state = robot.getRobotState()
            manage = robot.getRobotManage()
            if not state.isPowerOn():
                manage.poweron()
                time.sleep(power_on_wait_s)
            manage.startup()
            time.sleep(startup_wait_s)
        except Exception as exc:
            raise AuboClientError(f"机器人上电或启动失败：{exc}") from exc

    def move_joint(
        self,
        target_rad: tuple[float, ...],
        acceleration_rad_s2: float,
        velocity_rad_s: float,
    ) -> Any:
        """向 SDK 发送绝对关节目标；安全校验必须由调用方先完成。"""

        robot = self._require_robot()
        try:
            motion = robot.getMotionControl()
            return motion.moveJoint(
                list(target_rad),
                acceleration_rad_s2,
                velocity_rad_s,
                0,
                0,
            )
        except Exception as exc:
            raise AuboClientError(f"moveJoint 下发失败：{exc}") from exc

    def stop_motion(self, deceleration_rad_s2: float = 1.0) -> Any:
        """立即请求关节空间停止；此软件调用不能替代实体急停。"""

        robot = self._require_robot()
        deceleration = float(deceleration_rad_s2)
        if deceleration <= 0:
            raise AuboClientError("停止减速度必须大于 0。")
        try:
            motion = robot.getMotionControl()
            result = motion.stopJoint(deceleration)
        except Exception as exc:
            raise AuboClientError(f"stopJoint 下发失败：{exc}") from exc
        if type(result) is int and result != 0:
            raise AuboClientError(f"stopJoint 返回错误码 {result}。")
        return result

    def set_standard_digital_output(self, index: int, value: bool) -> Any:
        """Set one controller standard digital output for a configured gripper adapter."""

        robot = self._require_robot()
        try:
            result = robot.getIoControl().setStandardDigitalOutput(int(index), bool(value))
        except Exception as exc:
            raise AuboClientError(f"设置标准数字输出 DO{index} 失败：{exc}") from exc
        if type(result) is int and result != 0:
            raise AuboClientError(f"设置标准数字输出 DO{index} 返回错误码 {result}。")
        return result

    def get_standard_digital_input(self, index: int) -> bool:
        """Read one controller standard digital input used as optional gripper feedback."""

        robot = self._require_robot()
        try:
            return bool(robot.getIoControl().getStandardDigitalInput(int(index)))
        except Exception as exc:
            raise AuboClientError(f"读取标准数字输入 DI{index} 失败：{exc}") from exc

