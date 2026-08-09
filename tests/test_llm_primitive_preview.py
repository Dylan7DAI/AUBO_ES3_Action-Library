from __future__ import annotations

import unittest

from pydantic import ValidationError

from scripts.llm_primitive_preview import (
    EmotionDecision,
    build_motion_preview,
    expected_transition_type,
    intensity_level_for_value,
    normalize_result,
    validate_decision_for_result,
)


def sample_decision(
    *,
    emotion_name: str = "joy",
    valence: float = 0.40,
    intensity: float = 0.40,
    intensity_level: int = 1,
) -> dict:
    return {
        "emotion_state": {
            "emotion_name": emotion_name,
            "valence": valence,
            "arousal": 0.50,
            "intensity": intensity,
            "persistence": 0.30,
            "unexpectedness": 0.20,
        },
        "intensity_level": intensity_level,
        "brief_reason": "本轮结果与此前历史一致",
    }


class PrimitivePreviewTests(unittest.TestCase):
    def test_terminal_input_uses_numeric_results(self) -> None:
        self.assertEqual(normalize_result("1"), "correct")
        self.assertEqual(normalize_result(" 0 "), "incorrect")
        self.assertIsNone(normalize_result("wrong"))

    def test_transition_type_rule(self) -> None:
        self.assertEqual(
            expected_transition_type(["correct"]),
            "continuation",
        )
        self.assertEqual(
            expected_transition_type(["correct", "correct"]),
            "accumulation",
        )
        self.assertEqual(
            expected_transition_type(["correct", "incorrect"]),
            "reversal",
        )
        self.assertEqual(
            expected_transition_type(
                ["correct", "incorrect", "correct"]
            ),
            "fluctuation",
        )

    def test_intensity_level_boundaries(self) -> None:
        self.assertEqual(intensity_level_for_value(0.24), 0)
        self.assertEqual(intensity_level_for_value(0.25), 1)
        self.assertEqual(intensity_level_for_value(0.50), 2)
        self.assertEqual(intensity_level_for_value(0.75), 3)

    def test_correct_decision_is_accepted(self) -> None:
        decision = EmotionDecision.model_validate(sample_decision())
        validate_decision_for_result(
            decision,
            "correct",
            ["correct"],
            None,
        )

    def test_incorrect_decision_is_accepted(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(
                emotion_name="disappointment",
                valence=-0.40,
            )
        )
        validate_decision_for_result(
            decision,
            "incorrect",
            ["incorrect"],
            None,
        )

    def test_wrong_emotion_for_result_is_rejected(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(emotion_name="disappointment")
        )
        with self.assertRaises(ValueError):
            validate_decision_for_result(
                decision,
                "correct",
                ["correct"],
                None,
            )

    def test_intensity_level_must_match_value(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(intensity=0.60, intensity_level=1)
        )
        with self.assertRaises(ValueError):
            validate_decision_for_result(
                decision,
                "correct",
                ["correct"],
                None,
            )

    def test_fluctuation_caps_level_at_one(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(intensity=0.60, intensity_level=2)
        )
        with self.assertRaises(ValueError):
            validate_decision_for_result(
                decision,
                "correct",
                ["correct", "incorrect", "correct"],
                None,
            )

    def test_accumulation_level_cannot_drop(self) -> None:
        decision = EmotionDecision.model_validate(sample_decision())
        with self.assertRaises(ValueError):
            validate_decision_for_result(
                decision,
                "correct",
                ["correct", "correct"],
                {"intensity_level": 2},
            )

    def test_more_than_two_decimal_places_are_rejected(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(
                valence=0.333,
                intensity=0.333,
            )
        )
        with self.assertRaises(ValueError):
            validate_decision_for_result(
                decision,
                "correct",
                ["correct"],
                None,
            )

    def test_extra_decision_fields_are_rejected(self) -> None:
        data = sample_decision()
        data["motion"] = "rise"
        with self.assertRaises(ValidationError):
            EmotionDecision.model_validate(data)

    def test_motion_preview_uses_fixed_local_mapping(self) -> None:
        decision = EmotionDecision.model_validate(
            sample_decision(
                valence=0.60,
                intensity=0.60,
                intensity_level=2,
            )
        )
        preview = build_motion_preview(
            decision,
            "correct",
            ["correct", "correct"],
        )
        self.assertEqual(preview["motion"], "joyshout")
        self.assertEqual(preview["repeated_primitive"], "arm_pulse")
        self.assertEqual(preview["repeat_count"], 2)
        self.assertEqual(preview["speed_scale"], 0.90)
        self.assertEqual(preview["start_pose"], "curiosity_down")
        self.assertEqual(
            preview["end_pose"],
            "anticipation_look_down",
        )


if __name__ == "__main__":
    unittest.main()
