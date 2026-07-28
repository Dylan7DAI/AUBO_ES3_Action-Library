"""Validate shared action schemas and the three experimental strategies offline."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from robot_game.config import load_study_config
from robot_game.event_logger import EventLogger
from robot_game.models import ActionPlan, Condition, Outcome, SessionState, Stage
from robot_game.robot_adapter import (
    DigitalOutputGripperAdapter,
    MockGripperAdapter,
    MockRobotAdapter,
    RobotExecutor,
)
from robot_game.safety_validator import (
    ALLOWED_BY_STAGE,
    DEFAULT_PARAMETERS,
    PARAMETER_RULES,
    SafetyValidator,
)
from robot_game.strategies import (
    LLMFailure,
    LLMStrategy,
    MockLLMClient,
    OpenAIResponsesLLMClient,
    RuleBasedStrategy,
    StrategyError,
    TaskOnlyStrategy,
    build_strategy,
)


ROOT = Path(__file__).resolve().parents[1]


class StrategyAndValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_log_root = os.environ.get("AUBO_LOG_ROOT")
        os.environ["AUBO_LOG_ROOT"] = self.temp.name
        self.config = load_study_config(ROOT / "config" / "study.example.json")
        self.logger = EventLogger(Path(self.temp.name), "schema-test", {
            "participant_id": "P", "session_id": "schema-test",
            "condition": "test", "config_version": self.config.version,
        })
        self.validator = SafetyValidator(self.config, self.logger)

    async def asyncTearDown(self):
        self.logger.close()
        if self.previous_log_root is None:
            os.environ.pop("AUBO_LOG_ROOT", None)
        else:
            os.environ["AUBO_LOG_ROOT"] = self.previous_log_root
        self.temp.cleanup()

    async def test_task_only_uses_only_time_matched_neutral(self):
        state = SessionState("P", "S", Condition.TASK_ONLY, False)
        for stage in ALLOWED_BY_STAGE:
            plan = await TaskOnlyStrategy().plan(stage, state)
            self.assertEqual(plan.function, "neutral_wait")
            self.validator.validate_plan(plan, stage, 1)

    async def test_rule_result_direction_follows_outcome(self):
        state = SessionState("P", "S", Condition.RULE_BASED, False)
        state.pending_outcome = Outcome.WIN
        positive = await RuleBasedStrategy().plan(Stage.POST_RESULT_EXPRESSION, state)
        self.assertEqual(positive.function, "positive_reaction")
        state.pending_outcome = Outcome.LOSS
        negative = await RuleBasedStrategy().plan(Stage.POST_RESULT_EXPRESSION, state)
        self.assertEqual(negative.function, "negative_reaction")

    async def test_mock_llm_output_is_schema_constrained(self):
        strategy = LLMStrategy(MockLLMClient(), self.config, self.logger)
        state = SessionState("P", "S", Condition.LLM_BASED, False)
        state.pending_outcome = Outcome.WIN
        plan = await strategy.plan(Stage.POST_RESULT_EXPRESSION, state)
        self.assertEqual(plan.function, "positive_reaction")
        self.validator.validate_plan(plan, Stage.POST_RESULT_EXPRESSION, 1)

    async def test_prompt_is_loaded_from_single_external_template(self):
        strategy = LLMStrategy(MockLLMClient(), self.config, self.logger)
        state = SessionState("P", "S", Condition.LLM_BASED, False)
        schema = strategy._schema(Stage.SESSION_OPEN)
        prompt = strategy._prompt(Stage.SESSION_OPEN, state, schema)
        self.assertEqual(strategy.prompt_path, ROOT / "config" / "openai_prompt.txt")
        self.assertIn("affective motion planner", strategy.prompt_template)
        self.assertIn('"stage": "SESSION_OPEN"', prompt)
        self.assertIn("VALIDATION_ERROR_JSON\nnull", prompt)
        self.assertNotIn("{{", prompt)

    async def test_openai_responses_contract_and_structured_emotion_plan(self):
        candidate = {
            "stage": Stage.SESSION_OPEN.value,
            "affective_state": {
                "valence": 0.25, "arousal": 0.35,
                "confidence": 0.55, "engagement": 0.65,
            },
            "action": {
                "function": "greet", "variant": "moderate",
                "parameters": dict(DEFAULT_PARAMETERS["greet"]),
            },
            "history_factors": ["session_open"],
        }

        class FakeResponse:
            output_text = json.dumps(candidate)
            status = "completed"

            def model_dump_json(self):
                return '{"id":"resp_test","status":"completed"}'

        class FakeResponses:
            def __init__(self):
                self.kwargs = None

            async def create(self, **kwargs):
                self.kwargs = kwargs
                return FakeResponse()

        class FakeOpenAI:
            def __init__(self):
                self.responses = FakeResponses()

        sdk = FakeOpenAI()
        client = OpenAIResponsesLLMClient(
            "OPENAI_API_KEY", "gpt-5.6-terra", 5.0,
            reasoning_effort="low", max_output_tokens=1200, client=sdk,
        )
        strategy = LLMStrategy(client, self.config, self.logger)
        plan = await strategy.plan(
            Stage.SESSION_OPEN,
            SessionState("P", "S", Condition.LLM_BASED, False),
        )
        self.assertEqual(plan.function, "greet")
        self.assertEqual(plan.affective_state["engagement"], 0.65)
        request = sdk.responses.kwargs
        self.assertEqual(request["model"], "gpt-5.6-terra")
        self.assertFalse(request["store"])
        self.assertEqual(request["reasoning"], {"effort": "low"})
        output_format = request["text"]["format"]
        self.assertTrue(output_format["strict"])
        self.assertEqual(output_format["type"], "json_schema")
        self.assertIn("anyOf", output_format["schema"]["properties"]["action"])
        self.assertNotIn("oneOf", json.dumps(output_format["schema"]))
        self.assertNotIn('"const"', json.dumps(output_format["schema"]))

    async def test_openai_condition_fails_before_session_without_api_key(self):
        previous = os.environ.get("OPENAI_API_KEY")
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaises(StrategyError):
                build_strategy(Condition.LLM_BASED, self.config, self.logger)
        finally:
            if previous is not None:
                os.environ["OPENAI_API_KEY"] = previous

    async def test_llm_rejects_pose_or_joint_fields(self):
        strategy = LLMStrategy(MockLLMClient(), self.config, self.logger)
        state = SessionState("P", "S", Condition.LLM_BASED, False)
        value = {
            "stage": Stage.SESSION_OPEN.value,
            "affective_state": {},
            "action": {"function": "greet", "variant": "bad", "parameters": {"q": [0] * 6}},
            "history_factors": [],
        }
        with self.assertRaises(LLMFailure):
            strategy._parse(Stage.SESSION_OPEN, state, value)

    async def test_llm_retries_out_of_range_parameters_then_fails(self):
        class InvalidClient:
            def __init__(self):
                self.calls = 0
                self.prompts = []

            async def complete(self, prompt, schema):
                self.calls += 1
                self.prompts.append(prompt)
                value = {
                    "stage": Stage.SESSION_OPEN.value,
                    "affective_state": {
                        "valence": 0.1, "arousal": 0.2,
                        "confidence": 0.3, "engagement": 0.4,
                    },
                    "action": {
                        "function": "greet", "variant": "default",
                        "parameters": dict(DEFAULT_PARAMETERS["greet"], lift_m=9.0),
                    },
                    "history_factors": [],
                }
                return "invalid raw response", value

        client = InvalidClient()
        strategy = LLMStrategy(client, self.config, self.logger)
        state = SessionState("P", "S", Condition.LLM_BASED, False)
        with self.assertRaises(LLMFailure):
            await strategy.plan(Stage.SESSION_OPEN, state)
        self.assertEqual(client.calls, 2)
        self.assertIn("outside [", client.prompts[1])

    async def test_every_registered_parameter_accepts_both_boundaries(self):
        stage_for = {
            "greet": Stage.SESSION_OPEN,
            "observe_hesitate": Stage.PRE_TASK_EXPRESSION,
            "positive_reaction": Stage.POST_RESULT_EXPRESSION,
            "negative_reaction": Stage.POST_RESULT_EXPRESSION,
            "neutral_wait": Stage.PRE_TASK_EXPRESSION,
            "farewell": Stage.SESSION_CLOSE,
        }
        for function, rules in PARAMETER_RULES.items():
            for boundary_index in (0, -1):
                parameters = dict(DEFAULT_PARAMETERS[function])
                for name, rule in rules.items():
                    parameters[name] = sorted(rule)[boundary_index]
                plan = ActionPlan(function, "boundary", parameters, source="test")
                validated = self.validator.validate_plan(plan, stage_for[function], 1)
                self.assertEqual(validated.function, function)

    async def test_boundary_actions_execute_and_return_to_safe_base(self):
        stage_for = {
            "greet": Stage.SESSION_OPEN,
            "observe_hesitate": Stage.PRE_TASK_EXPRESSION,
            "positive_reaction": Stage.POST_RESULT_EXPRESSION,
            "negative_reaction": Stage.POST_RESULT_EXPRESSION,
            "neutral_wait": Stage.PRE_TASK_EXPRESSION,
            "farewell": Stage.SESSION_CLOSE,
        }
        adapter = MockRobotAdapter()
        executor = RobotExecutor(
            adapter, MockGripperAdapter(), self.config, self.logger, self.validator
        )
        await executor.connect()
        start = adapter.joints
        for function, rules in PARAMETER_RULES.items():
            for boundary_index in (0, -1):
                parameters = dict(DEFAULT_PARAMETERS[function])
                for name, rule in rules.items():
                    parameters[name] = sorted(rule)[boundary_index]
                plan = self.validator.validate_plan(
                    ActionPlan(function, "boundary", parameters, source="test"),
                    stage_for[function],
                    1,
                )
                await executor.execute_expression(plan, 1)
                self.assertEqual(adapter.joints, start, function)
        await executor.close()

    async def test_digital_output_gripper_close_issues_output_and_checks_feedback(self):
        class FakeClient:
            def __init__(self):
                self.outputs = []

            def set_standard_digital_output(self, index, value):
                self.outputs.append((index, value))
                return 0

            def get_standard_digital_input(self, index):
                return True

        class FakeAdapter:
            client = FakeClient()

        gripper = DigitalOutputGripperAdapter(FakeAdapter(), {
            "output_index": 3,
            "closed_output_value": True,
            "closed_feedback_input": 4,
            "settle_s": 0,
        })
        self.assertEqual(gripper.close(), 0)
        self.assertEqual(FakeAdapter.client.outputs, [(3, True)])


if __name__ == "__main__":
    unittest.main()
