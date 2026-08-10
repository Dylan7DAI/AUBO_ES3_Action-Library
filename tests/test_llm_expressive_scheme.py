from __future__ import annotations

import json
import unittest
from pathlib import Path

from aubo_es3_actions.expressive_common import load_joint_pose
from aubo_es3_actions.expressive_safety import load_expressive_limits
from scripts.llm_expressive_scheme import PlanModel, compile_motion, validate_plan


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

    def test_keyframes_compile_through_seeded_ik(self) -> None:
        poses = json.loads(
            Path("config/emotion_poses_new_es3.json").read_text(encoding="utf-8")
        )

        class FakeClient:
            def forward_kinematics(self, joints: list[float]) -> list[float]:
                retreat = load_joint_pose(poses, "disappointment_retreat_max")
                if max(abs(a - b) for a, b in zip(joints, retreat)) < 1e-8:
                    return list(
                        poses["poses"]["disappointment_retreat_max"]["tcp_pose"]
                    )
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
            PlanModel.model_validate(self._plan()),
            round_input={"current_result": "correct"},
            pose_data=poses,
            client=FakeClient(),
            expressive_limits=load_expressive_limits(
                Path("config/expressive_motion_limits.json")
            ),
        )
        self.assertEqual(motion.metadata["tcp_sample_count"], 24)
        self.assertTrue(motion.requires_cartesian_validation)


if __name__ == "__main__":
    unittest.main()
