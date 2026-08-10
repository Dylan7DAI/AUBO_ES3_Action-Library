from __future__ import annotations

import json
import unittest
from pathlib import Path

from aubo_es3_actions import load_config
from aubo_es3_actions.expressive_common import load_joint_pose
from aubo_es3_actions.expressive_safety import load_expressive_limits
from scripts.llm_expressive_scheme import (
    PlanModel,
    build_round_input,
    compile_motion,
    validate_plan,
)


class CandidateSchemeTests(unittest.TestCase):
    def _quality(self, **updates: float | int) -> dict:
        data: dict[str, float | int] = {
            "vertical_shape": 0.55, "radial_shape": 0.45, "depth_shape": -0.05,
            "suddenness": 0.55, "freedom": 0.60, "directness": 0.45,
            "lightness": 0.70, "curvature": 0.45, "duration_scale": 1.00,
            "hold_ratio": 0.10, "oscillation_count": 0, "oscillation_scale": 0.00,
        }
        data.update(updates)
        return data

    def _plan(self) -> dict:
        return {
            "emotion_state": {
                "emotion_name": "joy", "valence": 0.6, "arousal": 0.55,
                "intensity": 0.5, "persistence": 0.4, "unexpectedness": 0.2,
            },
            "transition_type": "continuation",
            "brief_reason": "首次猜对，生成三种安全动作质量候选",
            "candidates": [
                {"candidate_id": "shape", "strategy": "shape_dominant", "motion_quality": self._quality(vertical_shape=0.82, radial_shape=0.72)},
                {"candidate_id": "rhythm", "strategy": "rhythm_dominant", "motion_quality": self._quality(suddenness=0.82, duration_scale=0.84, oscillation_count=1, oscillation_scale=0.24)},
                {"candidate_id": "path", "strategy": "path_dominant", "motion_quality": self._quality(freedom=0.84, directness=0.20, curvature=0.82)},
            ],
        }

    def test_three_distinct_candidates_are_valid(self) -> None:
        plan = PlanModel.model_validate(self._plan())
        round_input = build_round_input({
            "current_result": "correct", "result_history": ["correct"],
            "transition_type": "continuation", "previous_decision": None,
        })
        validate_plan(plan, round_input)

    def test_duplicate_strategy_is_rejected(self) -> None:
        data = self._plan()
        data["candidates"][2]["strategy"] = "rhythm_dominant"
        plan = PlanModel.model_validate(data)
        with self.assertRaises(ValueError):
            validate_plan(plan, {"current_result": "correct", "result_history": ["correct"]})

    def test_local_selector_returns_a_safe_candidate(self) -> None:
        poses = json.loads(
            Path("config/emotion_poses_new_es3.json").read_text(encoding="utf-8")
        )
        robot_config = load_config("config/robot.curiosity_fast.json")

        class FakeClient:
            config = robot_config

            def current_joints(self) -> list[float]:
                return load_joint_pose(poses, "curiosity_down")

        plan = PlanModel.model_validate(self._plan())
        round_input = build_round_input({
            "current_result": "correct", "result_history": ["correct"],
            "transition_type": "continuation", "previous_decision": None,
        })
        motion = compile_motion(
            plan,
            round_input=round_input,
            pose_data=poses,
            client=FakeClient(),
            expressive_limits=load_expressive_limits(
                Path("config/expressive_motion_limits.json")
            ),
        )
        self.assertIn(
            motion.metadata["selected_candidate"]["candidate_id"],
            {"shape", "rhythm", "path"},
        )
        self.assertGreaterEqual(len(motion.keyframes), 4)


if __name__ == "__main__":
    unittest.main()
