"""实验条件、状态、轮次、动作计划和 WoZ 命令的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Condition(str, Enum):
    TASK_ONLY = "task"
    RULE_BASED = "rule"
    LLM_BASED = "llm"


class Stage(str, Enum):
    SESSION_INIT = "SESSION_INIT"
    SESSION_OPEN = "SESSION_OPEN"
    WAIT_USER_SETUP = "WAIT_USER_SETUP"
    PRE_TASK_EXPRESSION = "PRE_TASK_EXPRESSION"
    WAIT_CUP_SELECTION = "WAIT_CUP_SELECTION"
    TASK_PICK = "TASK_PICK"
    WAIT_RESULT = "WAIT_RESULT"
    TASK_PLACE = "TASK_PLACE"
    POST_RESULT_EXPRESSION = "POST_RESULT_EXPRESSION"
    ROUND_CLOSE = "ROUND_CLOSE"
    INTER_ROUND_WAIT = "INTER_ROUND_WAIT"
    SESSION_CLOSE = "SESSION_CLOSE"
    FINISHED = "FINISHED"
    ERROR = "ERROR"


class Outcome(str, Enum):
    WIN = "win"
    LOSS = "loss"


@dataclass(frozen=True)
class ActionPlan:
    function: str
    variant: str = "default"
    parameters: dict[str, Any] = field(default_factory=dict)
    affective_state: dict[str, float] = field(default_factory=dict)
    history_factors: tuple[str, ...] = ()
    source: str = "system"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["history_factors"] = list(self.history_factors)
        return payload


@dataclass(frozen=True)
class RoundRecord:
    round_id: int
    selected_cup: int
    outcome: Outcome
    user_response_labels: tuple[str, ...] = ()
    action_function: str | None = None
    action_variant: str | None = None
    invalid_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["outcome"] = self.outcome.value
        payload["user_response_labels"] = list(self.user_response_labels)
        return payload


@dataclass
class SessionState:
    """一场会话的内存状态；history 跨轮保留，pending 字段每轮重置。"""

    participant_id: str
    session_id: str
    condition: Condition
    formal_study: bool
    stage: Stage = Stage.SESSION_INIT
    round_id: int = 1
    end_requested: bool = False
    selected_cup: int | None = None
    pending_outcome: Outcome | None = None
    pending_user_response: tuple[str, ...] = ()
    history: list[RoundRecord] = field(default_factory=list)
    robot_connected: bool = False
    robot_operating_mode: str = "unknown"
    robot_safety_mode: str = "unknown"
    current_action: str | None = None
    last_action: ActionPlan | None = None
    error: str | None = None

    @property
    def wins(self) -> int:
        return sum(item.outcome is Outcome.WIN for item in self.history)

    @property
    def losses(self) -> int:
        return sum(item.outcome is Outcome.LOSS for item in self.history)

    def streak(self, outcome: Outcome) -> int:
        count = 0
        for item in reversed(self.history):
            if item.outcome is not outcome:
                break
            count += 1
        return count

    def public_dict(self) -> dict[str, Any]:
        return {
            "participant_id": self.participant_id,
            "session_id": self.session_id,
            "condition": self.condition.value,
            "formal_study": self.formal_study,
            "stage": self.stage.value,
            "round_id": self.round_id,
            "end_requested": self.end_requested,
            "selected_cup": self.selected_cup,
            "pending_outcome": self.pending_outcome.value if self.pending_outcome else None,
            "history": [item.as_dict() for item in self.history],
            "wins": self.wins,
            "losses": self.losses,
            "robot_connected": self.robot_connected,
            "robot_operating_mode": self.robot_operating_mode,
            "robot_safety_mode": self.robot_safety_mode,
            "current_action": self.current_action,
            "last_action": self.last_action.as_dict() if self.last_action else None,
            "error": self.error,
        }


@dataclass(frozen=True)
class WozCommand:
    """浏览器发来的命令信封；request_id 用于关联 ACK，round_id 防止旧命令串轮。"""

    command: str
    request_id: str
    payload: dict[str, Any]
    session_id: str | None = None
    round_id: int | None = None
    timestamp: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> "WozCommand":
        if not isinstance(value, dict):
            raise ValueError("message must be a JSON object")
        if value.get("type") != "woz.command":
            raise ValueError("type must be 'woz.command'")
        command = value.get("command")
        request_id = value.get("request_id")
        payload = value.get("payload", {})
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        round_id = value.get("round_id")
        if round_id is not None and (not isinstance(round_id, int) or round_id < 1):
            raise ValueError("round_id must be a positive integer")
        return cls(
            command=command,
            request_id=request_id,
            payload=payload,
            session_id=value.get("session_id"),
            round_id=round_id,
            timestamp=value.get("timestamp"),
        )
