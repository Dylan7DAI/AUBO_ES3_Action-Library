"""Task-only, history-aware rule, and schema-constrained LLM condition strategies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from math import isfinite
from typing import Any, Protocol

from .config import StudyConfig
from .event_logger import EventLogger
from .models import ActionPlan, Condition, Outcome, SessionState, Stage
from .safety_validator import DEFAULT_PARAMETERS, PARAMETER_RULES


class StrategyError(RuntimeError):
    pass


class LLMFailure(StrategyError):
    pass


class ConditionStrategy(Protocol):
    async def plan(self, stage: Stage, state: SessionState) -> ActionPlan: ...


_STAGE_FUNCTIONS = {
    Stage.SESSION_OPEN: ("greet",),
    Stage.PRE_TASK_EXPRESSION: ("observe_hesitate",),
    Stage.POST_RESULT_EXPRESSION: ("positive_reaction", "negative_reaction"),
    Stage.SESSION_CLOSE: ("farewell",),
}

LLM_VARIANTS = ("default", "subtle", "moderate", "strong", "recovery", "pilot_default")


def _neutral_for_stage(stage: Stage, source: str) -> ActionPlan:
    durations = {
        Stage.SESSION_OPEN: 6.3,
        Stage.PRE_TASK_EXPRESSION: 5.3,
        Stage.POST_RESULT_EXPRESSION: 5.4,
        Stage.SESSION_CLOSE: 7.3,
    }
    return ActionPlan("neutral_wait", parameters={"duration_s": durations.get(stage, 1.0)}, source=source)


class TaskOnlyStrategy:
    async def plan(self, stage: Stage, state: SessionState) -> ActionPlan:
        return _neutral_for_stage(stage, "task_only.time_matched")


class RuleBasedStrategy:
    async def plan(self, stage: Stage, state: SessionState) -> ActionPlan:
        if stage is Stage.SESSION_OPEN:
            return ActionPlan("greet", "fixed", dict(DEFAULT_PARAMETERS["greet"]), source="rule.fixed")
        if stage is Stage.PRE_TASK_EXPRESSION:
            params = dict(DEFAULT_PARAMETERS["observe_hesitate"])
            # History-aware but discretized: only intensity-related fields change.
            loss_streak = state.streak(Outcome.LOSS)
            if loss_streak >= 2:
                params.update({"dwell_s": 0.9, "retreat_m": 0.06, "recheck_count": 1})
            return ActionPlan("observe_hesitate", "fixed_history", params, source="rule.history")
        if stage is Stage.POST_RESULT_EXPRESSION:
            if state.pending_outcome is Outcome.WIN:
                params = dict(DEFAULT_PARAMETERS["positive_reaction"])
                params["intensity"] = min(0.75, 0.45 + 0.1 * state.streak(Outcome.WIN))
                return ActionPlan("positive_reaction", "fixed_positive", params, source="rule.history")
            params = dict(DEFAULT_PARAMETERS["negative_reaction"])
            params["intensity"] = min(0.75, 0.45 + 0.1 * state.streak(Outcome.LOSS))
            return ActionPlan("negative_reaction", "fixed_negative", params, source="rule.history")
        if stage is Stage.SESSION_CLOSE:
            return ActionPlan("farewell", "fixed", dict(DEFAULT_PARAMETERS["farewell"]), source="rule.fixed")
        return _neutral_for_stage(stage, "rule.neutral")


class LLMClient(Protocol):
    async def complete(self, prompt: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]: ...


class HttpJsonLLMClient:
    """Provider-agnostic HTTP adapter expecting a JSON object or JSON text response."""

    def __init__(self, endpoint: str, api_key_env: str, model: str, timeout_s: float):
        self.endpoint = endpoint
        self.api_key_env = api_key_env
        self.model = model
        self.timeout_s = timeout_s

    async def complete(self, prompt: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return await asyncio.to_thread(self._complete_sync, prompt, schema)

    def _complete_sync(self, prompt: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        body = json.dumps({"model": self.model, "prompt": prompt, "json_schema": schema}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        key = os.getenv(self.api_key_env, "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            raw = response.read().decode("utf-8")
        parsed = json.loads(raw)
        candidate = parsed.get("output", parsed) if isinstance(parsed, dict) else parsed
        if isinstance(candidate, str):
            candidate = json.loads(candidate)
        if not isinstance(candidate, dict):
            raise LLMFailure("LLM endpoint did not return a JSON object")
        return raw, candidate


class OpenAIResponsesLLMClient:
    """Official OpenAI Responses API adapter with strict Structured Outputs."""

    def __init__(
        self,
        api_key_env: str,
        model: str,
        timeout_s: float,
        reasoning_effort: str = "low",
        max_output_tokens: int = 1200,
        client: Any | None = None,
    ):
        self.api_key_env = api_key_env
        self.model = model
        self.timeout_s = timeout_s
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self._client = client

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = os.getenv(self.api_key_env, "")
        if not api_key:
            raise LLMFailure(f"Missing OpenAI API key in {self.api_key_env}")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMFailure(
                "The OpenAI Python SDK is not installed; run scripts/setup_ubuntu20.sh"
            ) from exc
        self._client = AsyncOpenAI(
            api_key=api_key,
            timeout=self.timeout_s,
            max_retries=0,  # LLMStrategy owns the single controlled retry.
        )
        return self._client

    async def complete(self, prompt: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        response = await self._client_instance().responses.create(
            model=self.model,
            input=[{"role": "user", "content": prompt}],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "aubo_affective_action",
                    "strict": True,
                    "schema": schema,
                },
                "verbosity": "low",
            },
            reasoning={"effort": self.reasoning_effort},
            max_output_tokens=self.max_output_tokens,
            store=False,
        )
        output_text = str(getattr(response, "output_text", "") or "").strip()
        if not output_text:
            status = getattr(response, "status", "unknown")
            raise LLMFailure(f"OpenAI response contained no structured output (status={status})")
        try:
            candidate = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise LLMFailure("OpenAI structured output was not valid JSON") from exc
        if not isinstance(candidate, dict):
            raise LLMFailure("OpenAI structured output was not a JSON object")
        dump = getattr(response, "model_dump_json", None)
        raw = dump() if callable(dump) else output_text
        return raw, candidate


class MockLLMClient:
    """Deterministic pilot-only client used for offline end-to-end verification."""

    async def complete(self, prompt: str, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        marker = json.loads(prompt.split("GAME_CONTEXT_JSON\n", 1)[1].split("\nEND_CONTEXT", 1)[0])
        stage = marker["stage"]
        outcome = marker.get("pending_outcome")
        function = {
            Stage.SESSION_OPEN.value: "greet",
            Stage.PRE_TASK_EXPRESSION.value: "observe_hesitate",
            Stage.SESSION_CLOSE.value: "farewell",
        }.get(stage, "positive_reaction" if outcome == "win" else "negative_reaction")
        result = {
            "stage": stage,
            "affective_state": {"valence": 0.5 if outcome == "win" else -0.2,
                                "arousal": 0.3, "confidence": 0.5, "engagement": 0.6},
            "action": {"function": function, "variant": "pilot_default",
                       "parameters": dict(DEFAULT_PARAMETERS[function])},
            "history_factors": ["previous_outcome"] if outcome else [],
        }
        return json.dumps(result), result


class LLMStrategy:
    def __init__(self, client: LLMClient, config: StudyConfig, logger: EventLogger):
        self.client = client
        self.config = config
        self.logger = logger
        self.prompt_path = config.llm_prompt_path
        try:
            self.prompt_template = self.prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StrategyError(f"Cannot read LLM prompt template: {self.prompt_path}") from exc
        self.prompt_template_hash = hashlib.sha256(
            self.prompt_template.encode("utf-8")
        ).hexdigest()

    async def plan(self, stage: Stage, state: SessionState) -> ActionPlan:
        schema = self._schema(stage)
        validation_error: str | None = None
        time_budget_s = float(self.config.llm.get("timeout_s", 5.0))
        deadline = asyncio.get_running_loop().time() + time_budget_s
        errors: list[str] = []
        for attempt in (1, 2):
            prompt = self._prompt(stage, state, schema, validation_error)
            remaining_s = deadline - asyncio.get_running_loop().time()
            if remaining_s <= 0:
                errors.append("LLM stage time budget exhausted")
                break
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            self.logger.event(
                "llm", "request", round_id=state.round_id,
                data={"prompt_version": self.prompt_template_hash[:12],
                      "prompt_template_hash": self.prompt_template_hash,
                      "prompt_file": str(self.prompt_path), "prompt_hash": prompt_hash,
                      "raw_prompt": prompt, "input_context": self._context(stage, state),
                      "schema": schema, "attempt": attempt,
                      "provider": type(self.client).__name__,
                      "model": getattr(self.client, "model", None),
                      "stage_time_budget_s": time_budget_s,
                      "remaining_time_budget_s": remaining_s},
            )
            started = asyncio.get_running_loop().time()
            raw: str | None = None
            try:
                raw, candidate = await asyncio.wait_for(
                    self.client.complete(prompt, schema), timeout=remaining_s
                )
                plan = self._parse(stage, state, candidate)
                self.logger.event(
                    "llm", "response_valid", round_id=state.round_id,
                    data={"prompt_hash": prompt_hash, "raw_output": raw,
                          "parsed_plan": plan.as_dict(), "attempt": attempt,
                          "latency_s": asyncio.get_running_loop().time() - started},
                )
                return plan
            except Exception as exc:
                errors.append(str(exc))
                validation_error = str(exc)
                self.logger.event(
                    "llm", "response_invalid", level="WARNING", round_id=state.round_id,
                    data={"prompt_hash": prompt_hash, "attempt": attempt, "error": str(exc),
                          "raw_output": raw,
                          "latency_s": asyncio.get_running_loop().time() - started},
                )
        raise LLMFailure("; ".join(errors))

    def _parse(self, stage: Stage, state: SessionState, value: Any) -> ActionPlan:
        required_fields = {"stage", "affective_state", "action", "history_factors"}
        if not isinstance(value, dict) or set(value) != required_fields:
            raise LLMFailure("Output must contain exactly the registered top-level fields")
        if value.get("stage") != stage.value:
            raise LLMFailure("Output stage does not match current stage")
        action = value.get("action")
        if not isinstance(action, dict) or set(action) != {"function", "variant", "parameters"}:
            raise LLMFailure("action must contain exactly function, variant, parameters")
        function = action["function"]
        allowed = _STAGE_FUNCTIONS.get(stage, ())
        if function not in allowed:
            raise LLMFailure(f"{function!r} is not available in {stage.value}")
        parameters = action["parameters"]
        if not isinstance(parameters, dict):
            raise LLMFailure("parameters must be an object")
        banned = {"pose", "q", "joint", "joints", "code", "script", "python"}
        if any(str(key).lower() in banned for key in parameters):
            raise LLMFailure("Output contains forbidden pose/code fields")
        if set(parameters) != set(PARAMETER_RULES[function]):
            raise LLMFailure("Output must contain exactly the registered parameters")
        for name, rule in PARAMETER_RULES[function].items():
            item = parameters[name]
            if isinstance(rule, set):
                if item not in rule:
                    raise LLMFailure(f"action.parameters.{name} is not an allowed value")
            elif (
                not isinstance(item, (int, float))
                or isinstance(item, bool)
                or not isfinite(float(item))
                or not rule[0] <= float(item) <= rule[1]
            ):
                raise LLMFailure(
                    f"action.parameters.{name} is outside [{rule[0]}, {rule[1]}]"
                )
        variant = action["variant"]
        if variant not in LLM_VARIANTS:
            raise LLMFailure("Output variant is not registered")
        if stage is Stage.POST_RESULT_EXPRESSION:
            if state.pending_outcome is Outcome.WIN and function != "positive_reaction":
                raise LLMFailure("Win must map to positive_reaction")
            if state.pending_outcome is Outcome.LOSS and function != "negative_reaction":
                raise LLMFailure("Loss must map to negative_reaction")
        intensity = parameters.get("intensity")
        previous = state.last_action.parameters.get("intensity") if state.last_action else None
        if isinstance(intensity, (int, float)) and isinstance(previous, (int, float)):
            if abs(float(intensity) - float(previous)) > 0.25:
                raise LLMFailure("Adjacent intensity change exceeds 0.25")
        affect = value.get("affective_state", {})
        affect_keys = {"valence", "arousal", "confidence", "engagement"}
        if not isinstance(affect, dict) or set(affect) != affect_keys:
            raise LLMFailure("Invalid affective_state")
        for name, item in affect.items():
            if not isinstance(item, (int, float)) or isinstance(item, bool) or not isfinite(float(item)):
                raise LLMFailure(f"affective_state.{name} must be finite")
            minimum = -1.0 if name == "valence" else 0.0
            if not minimum <= float(item) <= 1.0:
                raise LLMFailure(f"affective_state.{name} is outside [{minimum}, 1.0]")
        history = value.get("history_factors", [])
        if not isinstance(history, list) or not all(isinstance(item, str) for item in history):
            raise LLMFailure("history_factors must be a string array")
        return ActionPlan(function, str(variant), parameters, affect, tuple(history), "llm")

    def _context(self, stage: Stage, state: SessionState) -> dict[str, Any]:
        return {
            "stage": stage.value, "round_id": state.round_id,
            "pending_outcome": state.pending_outcome.value if state.pending_outcome else None,
            "wins": state.wins, "losses": state.losses,
            "win_streak": state.streak(Outcome.WIN), "loss_streak": state.streak(Outcome.LOSS),
            "recent_history": [item.as_dict() for item in state.history[-5:]],
            "user_response_labels": list(state.pending_user_response),
            "last_action": state.last_action.as_dict() if state.last_action else None,
            "stage_time_budget_s": float(self.config.safety["max_action_duration_s"]),
        }

    def _prompt(
        self,
        stage: Stage,
        state: SessionState,
        schema: dict[str, Any],
        validation_error: str | None = None,
    ) -> str:
        replacements = {
            "{{REGISTERED_VARIANTS_JSON}}": json.dumps(LLM_VARIANTS),
            "{{GAME_CONTEXT_JSON}}": json.dumps(self._context(stage, state), sort_keys=True),
            "{{ALLOWED_OUTPUT_SCHEMA_JSON}}": json.dumps(schema, sort_keys=True),
            "{{VALIDATION_ERROR_JSON}}": json.dumps(validation_error),
        }
        prompt = self.prompt_template
        for marker, value in replacements.items():
            prompt = prompt.replace(marker, value)
        return prompt

    @staticmethod
    def _schema(stage: Stage) -> dict[str, Any]:
        functions = list(_STAGE_FUNCTIONS.get(stage, ()))
        action_variants = []
        for function in functions:
            properties: dict[str, Any] = {}
            for name, rule in PARAMETER_RULES[function].items():
                if isinstance(rule, set):
                    values = sorted(rule)
                    properties[name] = {
                        "type": "integer" if all(isinstance(item, int) for item in values) else "string",
                        "enum": values,
                    }
                else:
                    properties[name] = {
                        "type": "number", "minimum": rule[0], "maximum": rule[1]
                    }
            action_variants.append({
                "type": "object",
                "additionalProperties": False,
                "required": ["function", "variant", "parameters"],
                "properties": {
                    "function": {"type": "string", "enum": [function]},
                    "variant": {"enum": list(LLM_VARIANTS)},
                    "parameters": {
                        "type": "object", "additionalProperties": False,
                        "required": list(PARAMETER_RULES[function]),
                        "properties": properties,
                    },
                },
            })
        return {
            "type": "object", "additionalProperties": False,
            "required": ["stage", "affective_state", "action", "history_factors"],
            "properties": {
                "stage": {"type": "string", "enum": [stage.value]},
                "affective_state": {
                    "type": "object", "additionalProperties": False,
                    "required": ["valence", "arousal", "confidence", "engagement"],
                    "properties": {
                        "valence": {"type": "number", "minimum": -1, "maximum": 1},
                        "arousal": {"type": "number", "minimum": 0, "maximum": 1},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "engagement": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                "action": {"anyOf": action_variants},
                "history_factors": {"type": "array", "items": {"type": "string"}},
            },
        }


def build_strategy(condition: Condition, config: StudyConfig, logger: EventLogger) -> ConditionStrategy:
    if condition is Condition.TASK_ONLY:
        return TaskOnlyStrategy()
    if condition is Condition.RULE_BASED:
        return RuleBasedStrategy()
    llm = config.llm
    provider = llm.get("provider", "disabled")
    if provider == "mock":
        if config.formal_study:
            raise StrategyError("Mock LLM is forbidden in formal-study mode")
        client: LLMClient = MockLLMClient()
    elif provider == "openai":
        api_key_env = str(llm.get("api_key_env", "OPENAI_API_KEY"))
        if not os.getenv(api_key_env, ""):
            raise StrategyError(
                f"OpenAI LLM condition requires an API key in {api_key_env}"
            )
        client = OpenAIResponsesLLMClient(
            api_key_env=api_key_env,
            model=str(llm["model"]),
            timeout_s=float(llm.get("timeout_s", 5)),
            reasoning_effort=str(llm.get("reasoning_effort", "low")),
            max_output_tokens=int(llm.get("max_output_tokens", 1200)),
        )
    elif provider == "http_json":
        endpoint = str(llm.get("endpoint", "")).strip()
        if not endpoint:
            raise StrategyError("llm.endpoint is required for http_json")
        client = HttpJsonLLMClient(
            endpoint, str(llm.get("api_key_env", "LLM_API_KEY")),
            str(llm.get("model", "")), float(llm.get("timeout_s", 5)),
        )
    else:
        raise StrategyError("LLM condition is disabled; configure llm.provider before starting it")
    return LLMStrategy(client, config, logger)
