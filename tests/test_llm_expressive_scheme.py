from __future__ import annotations

import unittest

from scripts.llm_expressive_scheme import PlanModel, validate_plan


class KeyframeIkSchemeTests(unittest.TestCase):
    def _plan(self) -> dict:
        return {
            "emotion_state": {
                "emotion_name": "joy", "valence": 0.58,
                "arousal": 0.52, "intensity": 0.48,
                "persistence": 0.35, "unexpectedness": 0.20,
            },
            "transition_type": "continuation",
            "brief_reason": "一般情境下猜对，以适度上扬回应",
            "keyframes": [
                {
                    "phase": "preparation",
                    "position_norm": {"vertical": 0.18, "lateral": -0.08, "away_from_user": 0.08},
                    "orientation_token": "look_up_soft", "path_type": "vertical_arc",
                    "duration_weight": 0.92, "hold_ratio": 0.04, "speed_emphasis": 0.45,
                },
                {
                    "phase": "apex",
                    "position_norm": {"vertical": 0.52, "lateral": 0.10, "away_from_user": 0.05},
                    "orientation_token": "open_wrist_soft", "path_type": "release_arc",
                    "duration_weight": 1.02, "hold_ratio": 0.10, "speed_emphasis": 0.55,
                },
            ],
        }

    def test_valid_plan(self) -> None:
        plan = PlanModel.model_validate(self._plan())
        validate_plan(plan, {"current_result": "correct", "result_history": ["correct"]})

    def test_wrong_phase_order_is_rejected(self) -> None:
        data = self._plan()
        data["keyframes"][0]["phase"] = "apex"
        plan = PlanModel.model_validate(data)
        with self.assertRaises(ValueError):
            validate_plan(plan, {"current_result": "correct", "result_history": ["correct"]})


if __name__ == "__main__":
    unittest.main()
