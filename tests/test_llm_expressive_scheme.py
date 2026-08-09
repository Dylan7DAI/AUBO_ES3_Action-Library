from __future__ import annotations

import json
import unittest
from pathlib import Path

from aubo_es3_actions.expressive_common import load_joint_pose
from aubo_es3_actions.expressive_safety import load_expressive_limits
from scripts.llm_expressive_scheme import PlanModel, compile_motion, validate_plan


class ResidualSchemeTests(unittest.TestCase):
    def _positive_reversal_data(self) -> dict:
        return {
            "emotion_state": {
                "emotion_name": "relief", "valence": 0.55,
                "arousal": 0.48, "intensity": 0.42,
                "persistence": 0.35, "unexpectedness": 0.72,
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
            "path_curvature": 0.38, "duration_scale": 1.02,
            "apex_hold_ratio": 0.12, "onset_accent": 0.62,
            "local_pulse_count": 1, "pulse_scale": 0.18,
        }

    def test_reversal_positive_plan_is_valid(self) -> None:
        plan = PlanModel.model_validate(self._positive_reversal_data())
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

    def test_residual_plan_compiles_through_seeded_ik(self) -> None:
        poses = json.loads(
            Path("config/emotion_poses_new_es3.json").read_text(encoding="utf-8")
        )

        class FakeClient:
            def forward_kinematics(self, joints: list[float]) -> list[float]:
                for name in (
                    "joy_lift_max", "joy_shout_peak_max",
                    "disappointment_retreat_max",
                ):
                    reference = load_joint_pose(poses, name)
                    if max(abs(a - b) for a, b in zip(joints, reference)) < 1e-8:
                        return list(poses["poses"][name]["tcp_pose"])
                return [0.30, 0.0, 0.35, -1.0, 0.0, -1.0]

            def inverse_kinematics(
                self,
                pose: list[float],
                *,
                seed_joints: list[float],
            ) -> list[float]:
                del pose
                return [value + 0.002 for value in seed_joints]

        motion = compile_motion(
            PlanModel.model_validate(self._positive_reversal_data()),
            round_input={"current_result": "correct"},
            pose_data=poses,
            client=FakeClient(),
            expressive_limits=load_expressive_limits(
                Path("config/expressive_motion_limits.json")
            ),
        )
        self.assertEqual(motion.metadata["tcp_sample_count"], 20)
        self.assertTrue(motion.requires_cartesian_validation)


if __name__ == "__main__":
    unittest.main()
