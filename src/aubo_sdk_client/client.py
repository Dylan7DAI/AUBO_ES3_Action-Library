"""对 ``pyaubo_sdk.RpcClient`` 的小型显式封装。

统一处理连接、登录、机器人发现、状态读取、运动前准备和异常转换。
厂商 SDK 采用延迟导入，因此离线配置测试不要求安装或连接真机。
"""

from __future__ import annotations

from dataclasses import dataclass
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
        """读取并冻结当前上电状态、关节角和 TCP 位姿。"""

        robot = self._require_robot()
        try:
            state = robot.getRobotState()
            return RobotSnapshot(
                robot_name=self.robot_name or "unknown",
                power_on=bool(state.isPowerOn()),
                joint_positions_rad=tuple(float(v) for v in state.getJointPositions()),
                tcp_pose=tuple(float(v) for v in state.getTcpPose()),
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



