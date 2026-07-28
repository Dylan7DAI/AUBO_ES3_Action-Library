"""Strict whitelist, phase, range, duration, calibration, and robot-state checks."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

from .config import StudyConfig
from .event_logger import EventLogger
from .models import ActionPlan, Stage


class StudySafetyError(ValueError):
    pass


ALLOWED_BY_STAGE: dict[Stage, set[str]] = {
    Stage.SESSION_OPEN: {"greet", "neutral_wait"},
    Stage.PRE_TASK_EXPRESSION: {"observe_hesitate", "neutral_wait"},
    Stage.POST_RESULT_EXPRESSION: {"positive_reaction", "negative_reaction", "neutral_wait"},
    Stage.SESSION_CLOSE: {"farewell", "neutral_wait"},
}


PARAMETER_RULES: dict[str, dict[str, tuple[float, float] | set[Any]]] = {
    "greet": {
        "intensity": (0.0, 1.0), "lift_m": (0.04, 0.15), "user_yaw_rad": (-0.44, 0.44),
        "sway_rad": (0.052, 0.21), "sway_count": {1, 2}, "speed_scale": (0.2, 0.6),
        "accel_scale": (0.2, 0.6), "hold_s": (0.2, 1.5),
    },
    "observe_hesitate": {
        "dwell_s": (0.2, 1.5), "recheck_count": {0, 1}, "retreat_m": (0.03, 0.12),
        "path_style": {"direct", "arc"}, "speed_scale": (0.2, 0.6),
        "wrist_rad": (-0.21, 0.21),
    },
    "positive_reaction": {
        "intensity": (0.0, 1.0), "lift_m": (0.04, 0.15), "sway_rad": (0.052, 0.21),
        "sway_count": {1, 2}, "orient_user_rad": (-0.44, 0.44), "speed_scale": (0.2, 0.6),
        "accel_scale": (0.2, 0.6), "hold_s": (0.2, 1.5),
    },
    "negative_reaction": {
        "intensity": (0.0, 1.0), "sink_m": (0.04, 0.15), "retreat_m": (0.03, 0.12),
        "turn_away_rad": (-0.44, 0.44), "oscillation_rad": (0.052, 0.21),
        "speed_scale": (0.2, 0.6), "accel_scale": (0.2, 0.6), "hold_s": (0.2, 1.5),
    },
    "neutral_wait": {"duration_s": (0.0, 20.0)},
    "farewell": {
        "lift_m": (0.04, 0.15), "user_yaw_rad": (-0.44, 0.44), "sway_rad": (0.052, 0.21),
        "sway_count": {1, 2}, "speed_scale": (0.2, 0.6), "hold_s": (0.2, 1.5),
    },
}


DEFAULT_PARAMETERS: dict[str, dict[str, Any]] = {
    "greet": {"intensity": 0.5, "lift_m": 0.07, "user_yaw_rad": 0.08, "sway_rad": 0.07,
              "sway_count": 1, "speed_scale": 0.35, "accel_scale": 0.35, "hold_s": 0.3},
    "observe_hesitate": {"dwell_s": 0.6, "recheck_count": 0, "retreat_m": 0.04,
                          "path_style": "direct", "speed_scale": 0.3, "wrist_rad": 0.06},
    "positive_reaction": {"intensity": 0.5, "lift_m": 0.08, "sway_rad": 0.07,
                          "sway_count": 1, "orient_user_rad": 0.08, "speed_scale": 0.4,
                          "accel_scale": 0.4, "hold_s": 0.3},
    "negative_reaction": {"intensity": 0.5, "sink_m": 0.07, "retreat_m": 0.05,
                          "turn_away_rad": 0.08, "oscillation_rad": 0.06,
                          "speed_scale": 0.3, "accel_scale": 0.3, "hold_s": 0.5},
    "neutral_wait": {"duration_s": 1.0},
    "farewell": {"lift_m": 0.07, "user_yaw_rad": 0.08, "sway_rad": 0.07,
                 "sway_count": 1, "speed_scale": 0.3, "hold_s": 0.3},
}


class SafetyValidator:
    def __init__(self, config: StudyConfig, logger: EventLogger):
        self.config = config
        self.logger = logger

    def validate_plan(self, plan: ActionPlan, stage: Stage, round_id: int) -> ActionPlan:
        allowed = ALLOWED_BY_STAGE.get(stage, set())
        name_ok = plan.function in allowed and plan.function in PARAMETER_RULES
        self.logger.safety_check(
            "action_whitelist_and_stage", name_ok, requested={"function": plan.function, "stage": stage.value},
            limits={"allowed_functions": sorted(allowed)}, round_id=round_id,
        )
        if not name_ok:
            raise StudySafetyError(f"{plan.function!r} is not allowed during {stage.value}")

        rules = PARAMETER_RULES[plan.function]
        unknown = sorted(set(plan.parameters) - set(rules))
        self.logger.safety_check(
            "parameter_schema", not unknown, requested=plan.parameters,
            limits={"allowed_parameters": sorted(rules)}, round_id=round_id,
        )
        if unknown:
            raise StudySafetyError(f"Unknown parameters for {plan.function}: {', '.join(unknown)}")

        values = deepcopy(DEFAULT_PARAMETERS[plan.function])
        values.update(plan.parameters)
        for name, rule in rules.items():
            value = values[name]
            valid = False
            if isinstance(rule, set):
                valid = value in rule
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                number = float(value)
                valid = isfinite(number) and rule[0] <= number <= rule[1]
            self.logger.safety_check(
                f"parameter_range.{plan.function}.{name}", valid, requested=value,
                limits=sorted(rule) if isinstance(rule, set) else list(rule),
                validated=value if valid else None, round_id=round_id,
            )
            if not valid:
                raise StudySafetyError(f"Invalid {plan.function}.{name}: {value!r}")

        duration = self._estimate_duration(plan.function, values)
        maximum = float(self.config.safety["max_action_duration_s"])
        duration_ok = duration <= maximum
        self.logger.safety_check(
            "action_duration_budget", duration_ok, requested=duration,
            limits={"max_action_duration_s": maximum}, validated=duration if duration_ok else None,
            round_id=round_id,
        )
        if not duration_ok:
            raise StudySafetyError(f"Estimated action duration {duration:.2f}s exceeds {maximum:.2f}s")
        return ActionPlan(
            function=plan.function, variant=plan.variant, parameters=values,
            affective_state=plan.affective_state, history_factors=plan.history_factors,
            source=plan.source,
        )

    @staticmethod
    def _estimate_duration(function: str, values: dict[str, Any]) -> float:
        if function == "neutral_wait":
            return float(values["duration_s"])
        movement_segments = 4 + 2 * int(values.get("sway_count", values.get("recheck_count", 0)))
        speed = max(float(values.get("speed_scale", 0.3)), 0.01)
        holds = float(values.get("hold_s", values.get("dwell_s", 0.0)))
        return movement_segments * 0.35 / speed + holds

    def validate_robot_snapshot(self, snapshot: Any, round_id: int) -> None:
        checks = {
            "power_on": bool(snapshot.power_on),
            "steady": snapshot.steady is True,
            "within_safety_limits": snapshot.within_safety_limits is not False,
            "collision_clear": snapshot.collision_occurred is not True,
            "safety_mode_readable": snapshot.safety_mode not in {"unknown", "None"},
        }
        for name, passed in checks.items():
            self.logger.safety_check(
                f"robot_state.{name}", passed, requested={
                    "robot_mode": snapshot.robot_mode, "safety_mode": snapshot.safety_mode,
                    "power_on": snapshot.power_on, "steady": snapshot.steady,
                    "collision_occurred": snapshot.collision_occurred,
                    "within_safety_limits": snapshot.within_safety_limits,
                    "joint_positions_rad": snapshot.joint_positions_rad,
                    "tcp_pose": snapshot.tcp_pose, "telemetry": snapshot.telemetry,
                }, limits={"must_pass": True}, validated=passed, round_id=round_id,
            )
            if not passed:
                raise StudySafetyError(f"Robot state precondition failed: {name}")
