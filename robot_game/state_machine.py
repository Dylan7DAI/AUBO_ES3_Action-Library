"""Strict multi-round game state machine driven by explicit WoZ events."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .config import StudyConfig
from .event_logger import EventLogger
from .models import ActionPlan, Outcome, RoundRecord, SessionState, Stage, WozCommand
from .robot_adapter import RobotExecutor
from .safety_validator import DEFAULT_PARAMETERS, SafetyValidator, StudySafetyError
from .strategies import ConditionStrategy, LLMFailure


class StateTransitionError(RuntimeError):
    pass


Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


class GameStateMachine:
    def __init__(
        self,
        state: SessionState,
        config: StudyConfig,
        logger: EventLogger,
        executor: RobotExecutor,
        validator: SafetyValidator,
        strategy: ConditionStrategy,
        broadcast: Broadcast,
    ):
        self.state = state
        self.config = config
        self.logger = logger
        self.executor = executor
        self.validator = validator
        self.strategy = strategy
        self.broadcast = broadcast
        self._command_lock = asyncio.Lock()
        self._invalid_reason: str | None = None

    async def start(self) -> None:
        self.logger.event(
            "session", "started", data={
                "participant_id": self.state.participant_id,
                "session_id": self.state.session_id,
                "condition": self.state.condition.value,
                "formal_study": self.state.formal_study,
                "config_version": self.config.version,
                "config_hash": self.config.config_hash,
                "robot_mode": self.config.robot_mode,
            },
        )
        try:
            snapshot = await self.executor.connect()
            self.state.robot_connected = True
            self.state.robot_operating_mode = snapshot.robot_mode
            self.state.robot_safety_mode = snapshot.safety_mode
            await self._transition(Stage.SESSION_OPEN, "robot_ready")
            await self._expression(Stage.SESSION_OPEN)
            self._ensure_not_stopped()
            await self._transition(Stage.WAIT_USER_SETUP, "session_open_complete")
            self._round_started()
        except Exception as exc:
            await self._fail(exc, "session_start")
            raise

    async def handle(self, command: WozCommand) -> dict[str, Any]:
        if command.command in {"emergency_stop", "end_after_round"}:
            self._validate_envelope(command)
            self._log_command_received(command)
            try:
                if command.command == "emergency_stop":
                    result = await self._emergency(command)
                else:
                    result = await self._end_after_round(command)
                self._log_command_ack(command)
                return result
            except Exception as exc:
                self._log_command_error(command, exc)
                raise

        async with self._command_lock:
            self._validate_envelope(command)
            self._log_command_received(command)
            try:
                if command.command == "user_response":
                    result = await self._user_response(command)
                elif command.command == "user_setup_done":
                    result = await self._user_setup_done(command)
                elif command.command == "select_cup":
                    result = await self._select_cup(command)
                elif command.command == "result":
                    result = await self._result(command)
                elif command.command == "abort_session":
                    result = await self._abort(command)
                elif command.command == "reset_error":
                    result = await self._reset_error(command)
                elif command.command == "pilot_action":
                    result = await self._pilot_action(command)
                else:
                    raise StateTransitionError(f"Unknown or disabled command: {command.command}")
                self._log_command_ack(command)
                return result
            except Exception as exc:
                self._log_command_error(command, exc)
                if not isinstance(exc, StateTransitionError):
                    await self._fail(exc, command.command)
                raise

    def _log_command_received(self, command: WozCommand) -> None:
        self.logger.event(
            "woz", "command_received", round_id=self.state.round_id,
            request_id=command.request_id,
            data={"command": command.command, "payload": command.payload,
                  "operator_timestamp": command.timestamp, "stage": self.state.stage.value},
        )

    def _log_command_ack(self, command: WozCommand) -> None:
        self.logger.event(
            "woz", "command_ack", round_id=self.state.round_id,
            request_id=command.request_id,
            data={"command": command.command, "stage": self.state.stage.value},
        )

    def _log_command_error(self, command: WozCommand, exc: Exception) -> None:
        self.logger.event(
            "woz", "command_error", level="ERROR", round_id=self.state.round_id,
            request_id=command.request_id,
            data={"command": command.command, "stage": self.state.stage.value,
                  "error_type": type(exc).__name__, "error": str(exc)},
        )

    def _validate_envelope(self, command: WozCommand) -> None:
        if command.session_id and command.session_id != self.state.session_id:
            raise StateTransitionError("Command session_id does not match active session")
        if command.round_id is not None and command.round_id != self.state.round_id:
            raise StateTransitionError(
                f"Stale command round_id={command.round_id}; active round is {self.state.round_id}"
            )

    async def _user_response(self, command: WozCommand) -> dict[str, Any]:
        labels = command.payload.get("labels", [])
        allowed = {"encouragement", "hint", "help", "difficulty_reduction", "none"}
        if not isinstance(labels, list) or not labels or not all(item in allowed for item in labels):
            raise StateTransitionError(f"labels must be a non-empty subset of {sorted(allowed)}")
        if "none" in labels and len(labels) > 1:
            raise StateTransitionError("The 'none' label cannot be combined with other labels")
        self.state.pending_user_response = tuple(dict.fromkeys(labels))
        await self._publish()
        return self.state.public_dict()

    async def _user_setup_done(self, command: WozCommand) -> dict[str, Any]:
        self._require(Stage.WAIT_USER_SETUP)
        await self._transition(Stage.PRE_TASK_EXPRESSION, "user_setup_done")
        await self._expression(Stage.PRE_TASK_EXPRESSION)
        self._ensure_not_stopped()
        await self._transition(Stage.WAIT_CUP_SELECTION, "pre_task_complete")
        return self.state.public_dict()

    async def _select_cup(self, command: WozCommand) -> dict[str, Any]:
        self._require(Stage.WAIT_CUP_SELECTION)
        cup_id = command.payload.get("cup_id")
        if cup_id not in {1, 2, 3}:
            raise StateTransitionError("cup_id must be 1, 2, or 3")
        self.state.selected_cup = int(cup_id)
        await self._transition(Stage.TASK_PICK, f"cup_{cup_id}_selected")
        self.state.current_action = f"pick_cup_{cup_id}"
        await self._publish()
        try:
            await self.executor.execute_pick(int(cup_id), self.state.round_id)
        finally:
            self.state.current_action = None
            await self._publish()
        self._ensure_not_stopped()
        await self._transition(Stage.WAIT_RESULT, "pick_complete")
        return self.state.public_dict()

    async def _result(self, command: WozCommand) -> dict[str, Any]:
        self._require(Stage.WAIT_RESULT)
        try:
            outcome = Outcome(str(command.payload.get("outcome")))
        except ValueError as exc:
            raise StateTransitionError("outcome must be win or loss") from exc
        self.state.pending_outcome = outcome
        await self._transition(Stage.TASK_PLACE, f"result_{outcome.value}")
        self.state.current_action = "place_and_return_home"
        await self._publish()
        try:
            await self.executor.execute_place(self.state.round_id)
        finally:
            self.state.current_action = None
            await self._publish()
        self._ensure_not_stopped()
        await self._transition(Stage.POST_RESULT_EXPRESSION, "place_complete")
        await self._expression(Stage.POST_RESULT_EXPRESSION)
        self._ensure_not_stopped()
        await self._transition(Stage.ROUND_CLOSE, "post_result_complete")
        record = RoundRecord(
            round_id=self.state.round_id,
            selected_cup=self.state.selected_cup or 0,
            outcome=outcome,
            user_response_labels=self.state.pending_user_response,
            action_function=self.state.last_action.function if self.state.last_action else None,
            action_variant=self.state.last_action.variant if self.state.last_action else None,
            invalid_reason=self._invalid_reason,
        )
        self.state.history.append(record)
        round_data = record.as_dict()
        round_data.update({
            "win_streak": self.state.streak(Outcome.WIN),
            "loss_streak": self.state.streak(Outcome.LOSS),
            "wins_total": self.state.wins,
            "losses_total": self.state.losses,
        })
        self.logger.event("round", "completed", round_id=self.state.round_id, data=round_data)
        self._write_summary()
        if self.state.end_requested:
            await self._close_session("end_after_round")
        else:
            await self._transition(Stage.INTER_ROUND_WAIT, "round_recorded")
            await asyncio.sleep(self.config.inter_round_wait_s * self.config.time_scale)
            self.state.round_id += 1
            self.state.selected_cup = None
            self.state.pending_outcome = None
            self.state.pending_user_response = ()
            self.state.last_action = None
            self._invalid_reason = None
            await self._transition(Stage.WAIT_USER_SETUP, "inter_round_wait_complete")
            self._round_started()
        return self.state.public_dict()

    async def _expression(self, stage: Stage) -> None:
        try:
            requested = await self.strategy.plan(stage, self.state)
        except LLMFailure as exc:
            self._invalid_reason = f"LLM_FAILURE: {exc}"
            self.logger.event(
                "error", "llm_failure_neutral_fallback", level="ERROR", round_id=self.state.round_id,
                data={"error": str(exc), "condition_preserved": True, "round_invalid": True},
            )
            requested = ActionPlan(
                "neutral_wait", "llm_failure_safe", {"duration_s": 1.0}, source="llm.failure"
            )
        validated = self.validator.validate_plan(requested, stage, self.state.round_id)
        self.logger.event(
            "action", "plan_validated", round_id=self.state.round_id,
            data={"stage": stage.value, "requested": requested.as_dict(),
                  "validated": validated.as_dict(), "config_version": self.config.version},
        )
        self.state.current_action = validated.function
        await self._publish()
        try:
            await self.executor.execute_expression(validated, self.state.round_id)
            self.state.last_action = validated
        finally:
            self.state.current_action = None
            await self._publish()

    async def _end_after_round(self, command: WozCommand) -> dict[str, Any]:
        self._validate_envelope(command)
        if self.state.stage in {Stage.FINISHED, Stage.ERROR, Stage.SESSION_CLOSE}:
            raise StateTransitionError(f"Cannot request normal end during {self.state.stage.value}")
        self.state.end_requested = True
        self.logger.event(
            "woz", "end_after_round_queued", round_id=self.state.round_id,
            request_id=command.request_id, data={"stage": self.state.stage.value},
        )
        await self._publish()
        return self.state.public_dict()

    async def _emergency(self, command: WozCommand) -> dict[str, Any]:
        self._validate_envelope(command)
        reason = str(command.payload.get("reason", "researcher_requested"))[:500]
        try:
            await self.executor.emergency_stop(reason, self.state.round_id)
        finally:
            self.state.error = f"Emergency stop: {reason}"
            self.state.robot_safety_mode = "software_stop_requested"
            await self._transition(Stage.ERROR, "emergency_stop")
            self._invalid_reason = "EMERGENCY_STOP"
            self._write_summary()
        return self.state.public_dict()

    async def _abort(self, command: WozCommand) -> dict[str, Any]:
        reason = str(command.payload.get("reason", "session_aborted"))[:500]
        await self.executor.emergency_stop(reason, self.state.round_id)
        self.state.error = f"Aborted: {reason}"
        self.state.robot_safety_mode = "software_stop_requested"
        await self._transition(Stage.ERROR, "abort_session")
        self._write_summary()
        return self.state.public_dict()

    async def _reset_error(self, command: WozCommand) -> dict[str, Any]:
        self._require(Stage.ERROR)
        if self.state.formal_study:
            raise StateTransitionError("reset_error is disabled in formal-study mode")
        if self.config.robot_mode != "mock":
            raise StateTransitionError(
                "Real robot recovery requires a new session after physical inspection"
            )
        snapshot = await self.executor.connect()
        self.state.robot_connected = True
        self.state.robot_operating_mode = snapshot.robot_mode
        self.state.robot_safety_mode = snapshot.safety_mode
        self.executor._stopped = False
        self.state.error = None
        self._invalid_reason = "PILOT_MANUAL_RESET"
        await self._transition(Stage.WAIT_USER_SETUP, "pilot_manual_reset")
        self._round_started()
        return self.state.public_dict()

    async def _pilot_action(self, command: WozCommand) -> dict[str, Any]:
        if self.state.formal_study:
            raise StateTransitionError("pilot_action is disabled in formal-study mode")
        if self.state.stage not in {
            Stage.WAIT_USER_SETUP, Stage.WAIT_CUP_SELECTION, Stage.WAIT_RESULT
        }:
            raise StateTransitionError("pilot_action requires a stable waiting stage")
        function = str(command.payload.get("function", ""))
        stage_for_function = {
            "greet": Stage.SESSION_OPEN,
            "observe_hesitate": Stage.PRE_TASK_EXPRESSION,
            "positive_reaction": Stage.POST_RESULT_EXPRESSION,
            "negative_reaction": Stage.POST_RESULT_EXPRESSION,
            "farewell": Stage.SESSION_CLOSE,
            "neutral_wait": Stage.PRE_TASK_EXPRESSION,
        }.get(function)
        if stage_for_function is None:
            raise StateTransitionError("pilot_action function is not registered")
        requested = ActionPlan(
            function=function,
            variant="pilot_fixed",
            parameters=dict(DEFAULT_PARAMETERS[function]),
            source="woz.pilot",
        )
        validated = self.validator.validate_plan(
            requested, stage_for_function, self.state.round_id
        )
        self._invalid_reason = "PILOT_MANUAL_ACTION"
        self.logger.event(
            "woz", "pilot_action_started", level="WARNING", round_id=self.state.round_id,
            request_id=command.request_id,
            data={"stable_stage": self.state.stage.value, "plan": validated.as_dict(),
                  "round_invalid": True},
        )
        self.state.current_action = validated.function
        await self._publish()
        try:
            await self.executor.execute_expression(validated, self.state.round_id)
            self.state.last_action = validated
        finally:
            self.state.current_action = None
            await self._publish()
        await self._publish()
        return self.state.public_dict()

    async def _close_session(self, reason: str) -> None:
        await self._transition(Stage.SESSION_CLOSE, reason)
        await self._expression(Stage.SESSION_CLOSE)
        self._ensure_not_stopped()
        await self._transition(Stage.FINISHED, "session_close_complete")
        await self.executor.close()
        self.state.robot_connected = False
        self.state.robot_operating_mode = "disconnected"
        self.state.robot_safety_mode = "disconnected"
        await self._publish()
        self._write_summary()

    async def _fail(self, exc: Exception, operation: str) -> None:
        if operation == "session_start":
            self.state.robot_connected = False
        self.state.error = f"{type(exc).__name__}: {exc}"
        self._invalid_reason = f"{operation}: {type(exc).__name__}"
        self.logger.event(
            "error", "state_machine_failure", level="ERROR", round_id=self.state.round_id,
            data={"operation": operation, "error_type": type(exc).__name__, "error": str(exc),
                  "round_invalid": True},
        )
        if self.state.stage is not Stage.ERROR:
            await self._transition(Stage.ERROR, f"failure_{operation}")
        self._write_summary()

    def _require(self, expected: Stage) -> None:
        if self.state.stage is not expected:
            raise StateTransitionError(
                f"Command requires {expected.value}, current stage is {self.state.stage.value}"
            )

    def _ensure_not_stopped(self) -> None:
        if self.state.stage is Stage.ERROR or self.executor._stopped:
            raise StateTransitionError("Action was interrupted by an emergency stop")

    async def _transition(self, target: Stage, reason: str) -> None:
        previous = self.state.stage
        self.state.stage = target
        self.logger.event(
            "state", "transition", round_id=self.state.round_id,
            data={"from": previous.value, "to": target.value, "reason": reason},
        )
        await self._publish()

    async def _publish(self) -> None:
        await self.broadcast({"type": "system.status", "payload": self.state.public_dict()})

    def _write_summary(self) -> None:
        self.logger.write_summary({
            **self.state.public_dict(),
            "config_version": self.config.version,
            "config_hash": self.config.config_hash,
            "robot_mode": self.config.robot_mode,
        })

    def _round_started(self) -> None:
        self.logger.event(
            "round", "started", round_id=self.state.round_id,
            data={"stage": self.state.stage.value, "condition": self.state.condition.value},
        )
