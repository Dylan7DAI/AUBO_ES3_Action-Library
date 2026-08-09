from __future__ import annotations

import unittest

from scripts.llm_expressive_scheme import PlanModel, validate_plan


class ResidualSchemeTests(unittest.TestCase):
    def test_reversal_positive_plan_is_valid(self) -> None:
        data = {
            "emotion_state": {
                "emotion_name": "relief",
                "valence": 0.55,
                "arousal": 0.48,
                "intensity": 0.42,
                "persistence": 0.35,
                "unexpectedness": 0.72,
            },
            "transition_type": "reversal",
            "brief_reason": "连续猜错后猜对，采用克制释放",
            "reference_family": "positive_reference",
            "onset_modifier": {
                "up_down": 0.20, "open_close": 0.12, "lateral": 0.05,
                "yaw": 0.04, "pitch": -0.05, "roll": 0.03,
                "away_from_user": 0.10,
            },
            "apex_modifier": {
                "up_down": 0.35, "open_close": 0.28, "lateral": 0.08,
                "yaw": 0.05, "pitch": -0.02, "roll": 0.04,
                "away_from_user": 0.08,
            },
            "path_curvature": 0.38,
            "duration_scale": 1.02,
            "apex_hold_ratio": 0.12,
            "onset_accent": 0.62,
            "local_pulse_count": 1,
            "pulse_scale": 0.18,
        }
        plan = PlanModel.model_validate(data)
        validate_plan(plan, {
            "current_result": "correct",
            "result_history": ["incorrect", "correct"],
        })

    def test_wrong_reference_family_is_rejected(self) -> None:
        data = {
            "emotion_state": {
                "emotion_name": "disappointment", "valence": -0.4,
                "arousal": 0.3, "intensity": 0.3,
                "persistence": 0.3, "unexpectedness": 0.2,
            },
            "transition_type": "continuation",
            "brief_reason": "首次猜错，形成适度失望",
            "reference_family": "positive_reference",
            "onset_modifier": {
                "up_down": -0.1, "open_close": -0.1, "lateral": 0.0,
                "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                "away_from_user": 0.2,
            },
            "apex_modifier": {
                "up_down": -0.2, "open_close": -0.2, "lateral": 0.0,
                "yaw": 0.0, "pitch": 0.0, "roll": 0.0,
                "away_from_user": 0.3,
            },
            "path_curvature": 0.2, "duration_scale": 1.0,
            "apex_hold_ratio": 0.1, "onset_accent": 0.2,
            "local_pulse_count": 0, "pulse_scale": 0.0,
        }
        plan = PlanModel.model_validate(data)
        with self.assertRaises(ValueError):
            validate_plan(plan, {
                "current_result": "incorrect",
                "result_history": ["incorrect"],
            })


if __name__ == "__main__":
    unittest.main()
