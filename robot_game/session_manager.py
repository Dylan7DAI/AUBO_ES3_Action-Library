"""创建并持有唯一活跃实验会话，负责装配各层组件。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import StudyConfig
from .event_logger import EventLogger, safe_id
from .models import Condition, SessionState, WozCommand
from .robot_adapter import RobotExecutor, build_adapter
from .safety_validator import SafetyValidator
from .state_machine import GameStateMachine
from .strategies import build_strategy


class SessionManagerError(RuntimeError):
    pass


class SessionManager:
    """Web 层与单个 GameStateMachine 之间的会话生命周期管理器。"""

    def __init__(
        self,
        config: StudyConfig,
        robot_config_path: str | Path,
        broadcast: Callable[[dict[str, Any]], Awaitable[None]],
    ):
        self.config = config
        self.robot_config_path = robot_config_path
        self.broadcast = broadcast
        self.machine: GameStateMachine | None = None
        self._start_lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        if self.machine is None:
            return {
                "active": False,
                "robot_mode": self.config.robot_mode,
                "robot_connected": False,
                "robot_operating_mode": "unknown",
                "robot_safety_mode": "unknown",
                "config_version": self.config.version,
                "config_hash": self.config.config_hash,
            }
        return {"active": True, **self.machine.state.public_dict(),
                "robot_mode": self.config.robot_mode, "config_version": self.config.version}

    async def start_session(
        self, participant_id: str, session_id: str, condition_value: str, formal_study: bool
    ) -> dict[str, Any]:
        # 加锁避免两个浏览器几乎同时创建会话，导致共享同一台机械臂。
        async with self._start_lock:
            if self.machine and self.machine.state.stage.value not in {"FINISHED", "ERROR"}:
                raise SessionManagerError("Another session is active")
            if self.machine:
                try:
                    await self.machine.executor.close()
                finally:
                    self.machine.logger.close()
                self.machine = None
            participant = safe_id(participant_id)
            session = safe_id(session_id)
            try:
                condition = Condition(condition_value)
            except ValueError as exc:
                raise SessionManagerError("condition must be task, rule, or llm") from exc
            if formal_study != self.config.formal_study:
                raise SessionManagerError(
                    "Requested formal_study does not match the locked runtime configuration"
                )
            # 正式实验的条件由预注册表决定，页面传入值不能覆盖实验分组。
            if formal_study:
                assigned = self.config.formal_condition_for(participant, session)
                if assigned is None:
                    raise SessionManagerError(
                        "No pre-registered formal condition for this participant/session"
                    )
                if condition.value != assigned:
                    raise SessionManagerError(
                        f"Formal condition is locked to {assigned!r} for this participant/session"
                    )
            loop = asyncio.get_running_loop()

            # EventLogger 可能从工作线程写事件，因此通过线程安全入口交回 asyncio 循环。
            def publish_event(record: dict[str, Any]) -> None:
                payload = {
                    "type": "system.event",
                    "payload": {
                        "sequence": record["sequence"],
                        "timestamp_utc": record["timestamp_utc"],
                        "level": record["level"],
                        "category": record["category"],
                        "event": record["event"],
                        "round_id": record["round_id"],
                        "data": record["data"],
                    },
                }
                loop.call_soon_threadsafe(asyncio.create_task, self.broadcast(payload))

            logger = EventLogger(
                self.config.log_root, session,
                {"participant_id": participant, "session_id": session,
                 "condition": condition.value, "config_version": self.config.version},
                event_sink=publish_event,
            )
            try:
                # 在一个位置完成依赖装配：条件策略 → 硬件适配器 → 校验器 → 执行器 → 状态机。
                strategy = build_strategy(condition, self.config, logger)
                adapter, gripper = build_adapter(self.config, self.robot_config_path)
                validator = SafetyValidator(self.config, logger)
                executor = RobotExecutor(adapter, gripper, self.config, logger, validator)
                state = SessionState(participant, session, condition, formal_study)
                machine = GameStateMachine(
                    state, self.config, logger, executor, validator, strategy, self.broadcast
                )
                self.machine = machine
                await machine.start()
                return machine.state.public_dict()
            except Exception:
                logger.close()
                self.machine = None
                raise

    async def handle(self, command: WozCommand) -> dict[str, Any]:
        if self.machine is None:
            raise SessionManagerError("No active session")
        return await self.machine.handle(command)
