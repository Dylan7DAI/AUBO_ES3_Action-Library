from __future__ import annotations

import unittest

from scripts.llm_expressive_scheme import (
    ParameterizedPlan,
    build_round_input,
    compile_motion,
    validate_plan,
)


def sample_plan() -> ParameterizedPlan:
    return ParameterizedPlan.model_validate(
        {
            "emotion_state": {
                "emotion_name": "joy",
                "valence": 0.6,
                "arousal": 0.5,
                "intensity": 0.5,
                "persistence": 0.3,
                "unexpectedness": 0.2,
            },
            "transition_type": "continuation",
            "brief_reason": "首次猜对形成适度高兴",
            "motion_profile": {
                "spatial_extent": 0.75,
                "speed_scale": 0.9,
                "acceleration_scale": 0.9,
                "hold_scale": 1.0,
                "rhythmic_accent": 0.3,
                "repeat_count": 1,
                "repeat_amplitude": 0.7,
                "return_speed_scale": 0.85,
            },
        }
    )


class SchemeOneTests(unittest.TestCase):
    def test_valid_plan(self) -> None:
        round_input = build_round_input(
            {
                "current_result": "correct",
                "result_history": ["correct"],
                "transition_type": "continuation",
                "previous_emotion_state": {},
                "previous_decision": None,
            }
        )
        self.assertIn("previous_decision", round_input)
        self.assertNotIn("previous_plan", round_input)
        validate_plan(sample_plan(), round_input)

    def test_compiler_keeps_fixed_start_and_end(self) -> None:
        joints = {
            name: value
            for name, value in zip(
                [
                    "shoulder_joint",
                    "upperArm_joint",
                    "foreArm_joint",
                    "wrist1_joint",
                    "wrist2_joint",
                    "wrist3_joint",
                ],
                [0.0] * 6,
            )
        }
        pose_data = {
            "poses": {
                "curiosity_down": {"joint_positions_rad": joints},
                "joy_lift_max": {"joint_positions_rad": {**joints, "upperArm_joint": 0.4}},
                "joy_shout_peak_max": {"joint_positions_rad": {**joints, "upperArm_joint": 0.6}},
                "anticipation_look_down": {"joint_positions_rad": {**joints, "shoulder_joint": 0.1}},
            }
        }
        motion = compile_motion(
            sample_plan(),
            round_input={"current_result": "correct"},
            pose_data=pose_data,
            client=None,
            expressive_limits=None,
        )
        self.assertEqual(motion.keyframes[0], [0.0] * 6)
        self.assertEqual(motion.keyframes[-1][0], 0.1)
        self.assertFalse(motion.requires_cartesian_validation)


if __name__ == "__main__":
    unittest.main()
