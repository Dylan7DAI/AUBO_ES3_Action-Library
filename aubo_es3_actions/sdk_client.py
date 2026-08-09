from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, List, Optional

from .config import RobotConfig
from .safety import make_jog_target, validate_joint_vector


JOINT_NAMES = [
    "shoulder_joint",
    "upperArm_joint",
    "foreArm_joint",
    "wrist1_joint",
    "wrist2_joint",
    "wrist3_joint",
]


class AuboSdkError(RuntimeError):
    pass


@dataclass(frozen=True)
class SdkSnapshot:
    robot_name: str
    power_on: bool
    joint_positions_rad: List[float]
    tcp_pose: List[float]


class AuboSdkClient:
    def __init__(self, config: RobotConfig):
        self.config = config
        self.rpc: Any = None
        self.robot: Any = None
        self.robot_name: Optional[str] = None

    def connect(self) -> "AuboSdkClient":
        try:
            import pyaubo_sdk
        except ImportError as exc:
            raise AuboSdkError(
                "未安装 pyaubo_sdk。Ubuntu20 上建议在已有 AUBO SDK/Conda 环境或 AUBO Docker 容器中运行。"
            ) from exc

        rpc = pyaubo_sdk.RpcClient()
        try:
            rpc.setRequestTimeout(self.config.request_timeout_ms)
            rpc.connect(self.config.ip, self.config.port)
            if not rpc.hasConnected():
                raise RuntimeError("SDK connect 后仍显示未连接")
            rpc.login(self.config.username, self.config.password)
            if not rpc.hasLogined():
                raise RuntimeError("SDK login 后仍显示未登录")
            names = list(rpc.getRobotNames())
            if not names:
                raise RuntimeError("控制器中没有发现机器人")
        except Exception as exc:
            try:
                rpc.disconnect()
            except Exception:
                pass
            raise AuboSdkError(f"连接 AUBO 失败: {self.config.ip}:{self.config.port}: {exc}") from exc

        self.rpc = rpc
        self.robot_name = str(names[0])
        self.robot = rpc.getRobotInterface(names[0])
        return self

    def close(self) -> None:
        rpc = self.rpc
        self.rpc = None
        self.robot = None
        self.robot_name = None
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

    def __enter__(self) -> "AuboSdkClient":
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _require_robot(self):
        if self.robot is None:
            raise AuboSdkError("尚未连接机器人")
        return self.robot

    def snapshot(self) -> SdkSnapshot:
        robot = self._require_robot()
        try:
            state = robot.getRobotState()
            return SdkSnapshot(
                robot_name=self.robot_name or "unknown",
                power_on=bool(state.isPowerOn()),
                joint_positions_rad=[float(v) for v in state.getJointPositions()],
                tcp_pose=[float(v) for v in state.getTcpPose()],
            )
        except Exception as exc:
            raise AuboSdkError(f"读取机器人状态失败: {exc}") from exc

    def current_joints(self) -> List[float]:
        return self.snapshot().joint_positions_rad

    def current_pose(self) -> List[float]:
        return self.snapshot().tcp_pose

    def make_tcp_jog_target(self, axis: int, delta: float) -> List[float]:
        pose = [float(v) for v in self.current_pose()]
        if len(pose) != 6:
            raise AuboSdkError(f"SDK 返回的 TCP 位姿长度不是 6: {pose}")
        if axis < 0 or axis >= 6:
            raise AuboSdkError("TCP 轴编号必须在 0 到 5 之间")
        pose[axis] += float(delta)
        return pose

    def inverse_kinematics(self, target_pose: List[float]) -> List[float]:
        robot = self._require_robot()
        current = self.current_joints()
        try:
            algorithm = robot.getRobotAlgorithm()
            result = algorithm.inverseKinematics(current, list(target_pose))
        except Exception as exc:
            raise AuboSdkError(f"逆解失败: {exc}") from exc

        if not isinstance(result, tuple) or len(result) != 2:
            raise AuboSdkError(f"逆解返回值格式异常: {result!r}")
        joints, code = result
        if int(code) != 0:
            raise AuboSdkError(f"逆解失败，SDK 返回码: {code}")
        return validate_joint_vector(joints, self.config.safety)

    def prepare_for_motion(self) -> None:
        robot = self._require_robot()
        try:
            state = robot.getRobotState()
            manage = robot.getRobotManage()

            if not state.isPowerOn():
                # 真正未上电时仍保留完整安全等待。
                manage.poweron()
                time.sleep(self.config.safety.power_on_wait_s)
                manage.startup()
                time.sleep(self.config.safety.startup_wait_s)
                return

            # 已经上电时复用当前状态，减少每次动作前的空等。
            manage.startup()
            time.sleep(
                min(
                    0.20,
                    self.config.safety.startup_wait_s,
                )
            )
        except Exception as exc:
            raise AuboSdkError(
                f"机器人上电或启动失败: {exc}"
            ) from exc

    def move_to_joints(self, target_joints: List[float], *, prepare: bool = True) -> Any:
        target = validate_joint_vector(target_joints, self.config.safety)
        if prepare:
            self.prepare_for_motion()
        robot = self._require_robot()
        try:
            motion = robot.getMotionControl()
            return motion.moveJoint(
                list(target),
                self.config.safety.max_acceleration_rad_s2,
                self.config.safety.max_velocity_rad_s,
                0,
                0,
            )
        except Exception as exc:
            raise AuboSdkError(f"moveJoint 下发失败: {exc}") from exc
    def move_joint_spline(
        self,
        waypoints: List[List[float]],
        *,
        acceleration_rad_s2: float = 0.35,
        velocity_rad_s: float = 0.35,
        prepare: bool = True,
    ) -> Any:
        """
        连续播放多个关节路点。

        waypoints:
            多个六关节目标角，例如 [center, left, right, center]
        acceleration_rad_s2:
            关节加速度，单位 rad/s²
        velocity_rad_s:
            关节速度，单位 rad/s
        """
        if len(waypoints) < 3:
            raise AuboSdkError("样条运动至少需要3个关节路点")

        validated_waypoints = [
            list(validate_joint_vector(point, self.config.safety))
            for point in waypoints
        ]

        if prepare:
            self.prepare_for_motion()

        robot = self._require_robot()

        try:
            motion = robot.getMotionControl()

            if not hasattr(motion, "moveSpline"):
                raise AuboSdkError(
                    "当前SDK没有moveSpline接口，请检查SDK和控制器版本"
                )

            # 依次把路点加入样条序列
            for point in validated_waypoints:
                result = motion.moveSpline(
                    point,
                    acceleration_rad_s2,
                    velocity_rad_s,
                    0,
                )

                if isinstance(result, int) and result != 0:
                    raise AuboSdkError(
                        f"加入样条路点失败，返回码：{result}"
                    )

            # 空数组表示开始执行已经加入的样条路点
            result = motion.moveSpline(
                [],
                acceleration_rad_s2,
                velocity_rad_s,
                0,
            )

            if isinstance(result, int) and result != 0:
                raise AuboSdkError(
                    f"启动样条运动失败，返回码：{result}"
                )

            return result

        except AuboSdkError:
            raise
        except Exception as exc:
            raise AuboSdkError(
                f"moveSpline下发失败：{exc}"
            ) from exc
    def jog_joint(self, joint_index: int, delta: float, *, prepare: bool = True) -> List[float]:
        target = make_jog_target(self.current_joints(), joint_index, delta, self.config.safety)
        self.move_to_joints(target, prepare=prepare)
        return target

    def move_to_tcp_pose(
        self,
        target_pose: List[float],
        *,
        linear_acc_m_s2: float = 0.05,
        linear_vel_m_s: float = 0.02,
        prepare: bool = True,
    ) -> Any:
        if len(target_pose) != 6:
            raise AuboSdkError("TCP 目标位姿必须包含 6 个值: x,y,z,rx,ry,rz")
        self.inverse_kinematics(target_pose)
        if prepare:
            self.prepare_for_motion()
        robot = self._require_robot()
        try:
            motion = robot.getMotionControl()
            return motion.moveLine(
                [float(v) for v in target_pose],
                float(linear_acc_m_s2),
                float(linear_vel_m_s),
                0,
                0,
            )
        except Exception as exc:
            raise AuboSdkError(f"moveLine 下发失败: {exc}") from exc

    def jog_tcp(
        self,
        axis: int,
        delta: float,
        *,
        linear_acc_m_s2: float = 0.05,
        linear_vel_m_s: float = 0.02,
        prepare: bool = True,
    ) -> List[float]:
        target = self.make_tcp_jog_target(axis, delta)
        self.move_to_tcp_pose(
            target,
            linear_acc_m_s2=linear_acc_m_s2,
            linear_vel_m_s=linear_vel_m_s,
            prepare=prepare,
        )
        return target
