from __future__ import annotations

import unittest

from scripts.llm_expressive_scheme import LabanPlan, validate_plan


class SchemeTwoTests(unittest.TestCase):
    def test_positive_shape_is_accepted(self) -> None:
        plan = LabanPlan.model_validate(
            {
                "emotion_state": {
                    "emotion_name": "joy",
                    "valence": 0.5,
                    "arousal": 0.5,
                    "intensity": 0.4,
                    "persistence": 0.3,
                    "unexpectedness": 0.2,
                },
                "transition_type": "continuation",
                "brief_reason": "首次猜对形成适度高兴",
                "motion_quality": {
                    "vertical_shape": 0.4,
                    "radial_shape": 0.3,
                    "depth_shape": 0.0,
                    "suddenness": 0.5,
                    "freedom": 0.6,
                    "directness": 0.4,
                    "lightness": 0.7,
                    "curvature": 0.5,
                    "duration_scale": 1.0,
                    "hold_ratio": 0.1,
                    "oscillation_count": 1,
                    "oscillation_scale": 0.2,
                },
            }
        )
        validate_plan(
            plan,
            {"current_result": "correct", "result_history": ["correct"]},
        )

    def test_wrong_shape_direction_is_rejected(self) -> None:
        data = {
            "emotion_state": {
                "emotion_name": "joy",
                "valence": 0.5,
                "arousal": 0.5,
                "intensity": 0.4,
                "persistence": 0.3,
                "unexpectedness": 0.2,
            },
            "transition_type": "continuation",
            "brief_reason": "首次猜对形成适度高兴",
            "motion_quality": {
                "vertical_shape": -0.4,
                "radial_shape": -0.3,
                "depth_shape": 0.0,
                "suddenness": 0.5,
                "freedom": 0.6,
                "directness": 0.4,
                "lightness": 0.7,
                "curvature": 0.5,
                "duration_scale": 1.0,
                "hold_ratio": 0.1,
                "oscillation_count": 1,
                "oscillation_scale": 0.2,
            },
        }
        with self.assertRaises(ValueError):
            validate_plan(
                LabanPlan.model_validate(data),
                {"current_result": "correct", "result_history": ["correct"]},
            )


if __name__ == "__main__":
    unittest.main()
