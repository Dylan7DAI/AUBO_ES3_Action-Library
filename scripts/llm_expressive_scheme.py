from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from aubo_es3_actions.expressive_common import (
    CompiledMotion,
    EmotionState,
    TransitionType,
    load_joint_pose,
    validate_common_plan,
)
from aubo_es3_actions.expressive_safety import (
    interpolate_vector,
    maximum_difference,
    sample_joint_keyframes,
    validate_joint_keyframes,
    validate_sampled_trajectory,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEME_ID = "llm_scheme_5_candidates"
SCHEME_NAME = "方案五：LLM多候选与本地风险感知选择"
DEFAULT_PROMPT = ROOT / "config" / "llm_scheme_5_prompt.txt"
TEMPERATURE = 0.65

Strategy = Literal["shape_dominant", "rhythm_dominant", "path_dominant"]


class CandidateQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertical_shape: float = Field(ge=-1.0, le=1.0)
    radial_shape: float = Field(ge=-1.0, le=1.0)
    depth_shape: float = Field(ge=-1.0, le=0.20)
    suddenness: float = Field(ge=0.0, le=1.0)
    freedom: float = Field(ge=0.0, le=1.0)
    directness: float = Field(ge=0.0, le=1.0)
    lightness: float = Field(ge=0.0, le=1.0)
    curvature: float = Field(ge=0.0, le=1.0)
    duration_scale: float = Field(ge=0.80, le=1.25)
    hold_ratio: float = Field(ge=0.05, le=0.25)
    oscillation_count: int = Field(ge=0, le=2)
    oscillation_scale: float = Field(ge=0.0, le=0.50)


class MotionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,24}$")
    strategy: Strategy
    motion_quality: CandidateQuality


class CandidatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emotion_state: EmotionState
    transition_type: TransitionType
    brief_reason: str = Field(min_length=1, max_length=100)
    candidates: list[MotionCandidate] = Field(min_length=3, max_length=3)


PlanModel = CandidatePlan


def _rule_baseline(result: str, transition: str) -> dict[str, float | int]:
    if result == "correct":
        baseline: dict[str, float | int] = {
            "vertical_shape": 0.48, "radial_shape": 0.42, "depth_shape": -0.05,
            "suddenness": 0.55, "freedom": 0.62, "directness": 0.40,
            "lightness": 0.68, "curvature": 0.48, "duration_scale": 1.00,
            "hold_ratio": 0.10, "oscillation_count": 1, "oscillation_scale": 0.18,
        }
    else:
        baseline = {
            "vertical_shape": -0.42, "radial_shape": -0.38, "depth_shape": -0.35,
            "suddenness": 0.25, "freedom": 0.28, "directness": 0.62,
            "lightness": 0.25, "curvature": 0.28, "duration_scale": 1.12,
            "hold_ratio": 0.16, "oscillation_count": 0, "oscillation_scale": 0.00,
        }
    if transition == "fluctuation":
        baseline["vertical_shape"] = float(baseline["vertical_shape"]) * 0.75
        baseline["radial_shape"] = float(baseline["radial_shape"]) * 0.75
        baseline["oscillation_count"] = 0
    return baseline


def _signature(quality: CandidateQuality | dict[str, Any]) -> list[float]:
    data = quality.model_dump() if isinstance(quality, BaseModel) else quality
    return [
        float(data["vertical_shape"]),
        float(data["radial_shape"]),
        float(data["depth_shape"]),
        float(data["suddenness"]),
        float(data["freedom"]),
        float(data["directness"]),
        float(data["lightness"]),
        float(data["curvature"]),
        (float(data["duration_scale"]) - 0.8) / 0.45,
        float(data["hold_ratio"]) / 0.25,
        float(data["oscillation_count"]) / 2.0,
        float(data["oscillation_scale"]) / 0.5,
    ]


def _distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def build_round_input(base_input: dict[str, Any]) -> dict[str, Any]:
    baseline = _rule_baseline(
        str(base_input["current_result"]),
        str(base_input["transition_type"]),
    )
    recent: list[list[float]] = []
    previous = base_input.get("previous_plan")
    if isinstance(previous, dict):
        for candidate in previous.get("candidates", []):
            quality = candidate.get("motion_quality") if isinstance(candidate, dict) else None
            if isinstance(quality, dict):
                try:
                    recent.append(_signature(quality))
                except (KeyError, TypeError, ValueError):
                    pass
    return {
        **base_input,
        "rule_baseline_signature": baseline,
        "recent_motion_signatures": recent[-3:],
        "required_strategies": ["shape_dominant", "rhythm_dominant", "path_dominant"],
        "selection_policy": (
            "本地先硬性安全过滤，再综合情感一致性、偏离规则基线、近期新颖性、"
            "平滑度和安全裕度选择；LLM不得指定胜者"
        ),
    }


def _validate_quality(
    quality: CandidateQuality,
    *,
    result: str,
    transition: str,
) -> None:
    if result == "correct":
        if quality.vertical_shape < 0.15 or quality.radial_shape < 0.10:
            raise ValueError("正向候选必须至少轻微上扬和舒展")
    else:
        if quality.vertical_shape > -0.10 or quality.radial_shape > -0.10:
            raise ValueError("负向候选必须至少轻微下沉和收缩")
        if quality.depth_shape > 0.0 or quality.suddenness > 0.65:
            raise ValueError("负向候选不得接近用户或使用高突然性")
    if transition == "fluctuation":
        if max(abs(quality.vertical_shape), abs(quality.radial_shape)) > 0.55:
            raise ValueError("fluctuation候选的Shape绝对值不得超过0.55")
        if quality.oscillation_count > 1:
            raise ValueError("fluctuation候选最多一次振荡")
    if quality.oscillation_count == 0 and quality.oscillation_scale != 0.0:
        raise ValueError("无振荡时oscillation_scale必须为0.00")


def validate_plan(plan: CandidatePlan, round_input: dict[str, Any]) -> None:
    result = str(round_input["current_result"])
    validate_common_plan(
        plan,
        current_result=result,
        result_history=list(round_input["result_history"]),
    )
    identifiers = [candidate.candidate_id for candidate in plan.candidates]
    if len(set(identifiers)) != 3:
        raise ValueError("三个candidate_id必须唯一")
    strategies = [candidate.strategy for candidate in plan.candidates]
    if set(strategies) != {"shape_dominant", "rhythm_dominant", "path_dominant"}:
        raise ValueError("三个候选必须各使用一种指定策略")
    signatures: list[list[float]] = []
    for candidate in plan.candidates:
        _validate_quality(
            candidate.motion_quality,
            result=result,
            transition=plan.transition_type,
        )
        signatures.append(_signature(candidate.motion_quality))
    for index, first in enumerate(signatures):
        for second in signatures[index + 1:]:
            if _distance(first, second) < 0.35:
                raise ValueError("三个候选差异过小，无法利用LLM采样不确定性")


def build_preview(plan: CandidatePlan, round_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme": SCHEME_ID,
        "result": round_input["current_result"],
        **plan.model_dump(mode="json"),
        "local_selection": "deferred_until_compilation_and_safety_check",
        "safety": "每个候选独立编译并硬性过滤；LLM无权绕过检查或指定执行候选",
    }


def _blend_three(
    anchor: list[float],
    first: list[float],
    second: list[float],
    first_weight: float,
    second_weight: float,
) -> list[float]:
    result = interpolate_vector(anchor, first, first_weight)
    return [
        value + second_weight * (second_value - first_value)
        for value, first_value, second_value in zip(result, first, second)
    ]


def _compile_candidate(
    candidate: MotionCandidate,
    *,
    result: str,
    pose_data: dict[str, Any],
) -> CompiledMotion:
    quality = candidate.motion_quality
    start = load_joint_pose(pose_data, "curiosity_down")
    end = load_joint_pose(pose_data, "anticipation_look_down")
    amount = min(1.0, max(0.25, abs(quality.vertical_shape)))
    radial = min(0.42, max(0.05, abs(quality.radial_shape) * 0.35))
    if result == "correct":
        vertical_basis = load_joint_pose(pose_data, "joy_lift_max")
        radial_basis = load_joint_pose(pose_data, "joy_shout_peak_max")
    else:
        vertical_basis = load_joint_pose(pose_data, "disappointment_retreat_max")
        radial_basis = load_joint_pose(pose_data, "disappointment_turn_away")
    preparation = interpolate_vector(start, vertical_basis, amount * 0.55)
    apex = _blend_three(start, vertical_basis, radial_basis, amount, radial)
    side_name = "curiosity_left" if quality.depth_shape <= -0.25 else "curiosity_right"
    side = load_joint_pose(pose_data, side_name)
    curved = interpolate_vector(
        preparation,
        side,
        quality.curvature * (1.0 - quality.directness) * 0.12,
    )
    keyframes = [start, curved, apex]
    holds = [0.0, 0.0, 0.30 * quality.hold_ratio]
    pulse = interpolate_vector(apex, preparation, 0.10 * quality.oscillation_scale)
    for _ in range(quality.oscillation_count):
        keyframes.extend([pulse, apex])
        holds.extend([0.03, 0.05])
    keyframes.append(end)
    holds.append(0.0)
    return CompiledMotion(
        scheme=SCHEME_ID,
        keyframes=keyframes,
        velocity_rad_s=(0.15 + 0.12 * quality.suddenness) / quality.duration_scale,
        acceleration_rad_s2=0.26 + 0.20 * quality.suddenness,
        holds_s=holds,
        metadata={"candidate_id": candidate.candidate_id},
        requires_cartesian_validation=False,
    )


def _semantic_prototype(plan: CandidatePlan, result: str) -> list[float]:
    base = _rule_baseline(result, plan.transition_type)
    intensity = plan.emotion_state.intensity
    if result == "correct":
        base["vertical_shape"] = 0.25 + 0.60 * intensity
        base["radial_shape"] = 0.20 + 0.55 * intensity
        base["suddenness"] = 0.30 + 0.55 * plan.emotion_state.arousal
    else:
        base["vertical_shape"] = -(0.20 + 0.55 * intensity)
        base["radial_shape"] = -(0.18 + 0.50 * intensity)
        base["duration_scale"] = 0.95 + 0.25 * intensity
    return _signature(base)


def compile_motion(
    plan: CandidatePlan,
    *,
    round_input: dict[str, Any],
    pose_data: dict[str, Any],
    client: Any,
    expressive_limits: Any,
) -> CompiledMotion:
    result = str(round_input["current_result"])
    current = client.current_joints()
    baseline_signature = _signature(round_input["rule_baseline_signature"])
    recent = [list(map(float, item)) for item in round_input["recent_motion_signatures"]]
    prototype = _semantic_prototype(plan, result)
    scored: list[tuple[float, CompiledMotion, dict[str, Any]]] = []
    rejected: list[dict[str, str]] = []

    for candidate in plan.candidates:
        try:
            compiled = _compile_candidate(candidate, result=result, pose_data=pose_data)
            validated = validate_joint_keyframes(
                [current] + compiled.keyframes,
                client.config.safety,
                expressive_limits,
            )
            samples = sample_joint_keyframes(
                validated,
                max_velocity_rad_s=min(
                    compiled.velocity_rad_s,
                    client.config.safety.max_velocity_rad_s,
                    expressive_limits.max_velocity_rad_s,
                ),
                sample_period_s=expressive_limits.sample_period_s,
                max_acceleration_rad_s2=min(
                    compiled.acceleration_rad_s2,
                    client.config.safety.max_acceleration_rad_s2,
                    expressive_limits.max_acceleration_rad_s2,
                ),
                max_jerk_rad_s3=expressive_limits.max_jerk_rad_s3,
            )
            validate_sampled_trajectory(samples, client.config.safety, expressive_limits)
        except Exception as exc:
            rejected.append({"candidate_id": candidate.candidate_id, "reason": str(exc)})
            continue

        signature = _signature(candidate.motion_quality)
        semantic = max(0.0, 1.0 - _distance(signature, prototype) / 3.5)
        baseline_novelty = min(1.0, _distance(signature, baseline_signature) / 2.0)
        recent_novelty = 1.0 if not recent else min(
            1.0,
            min(_distance(signature, old) for old in recent) / 2.0,
        )
        quality = candidate.motion_quality
        smoothness = max(
            0.0,
            1.0 - 0.45 * quality.suddenness - 0.20 * quality.oscillation_count,
        )
        largest_segment = max(
            maximum_difference(a, b)
            for a, b in zip(validated, validated[1:])
        )
        risk_margin = max(
            0.0,
            1.0 - largest_segment / expressive_limits.max_segment_joint_delta_rad,
        )
        score = (
            0.40 * semantic
            + 0.25 * baseline_novelty
            + 0.15 * recent_novelty
            + 0.10 * smoothness
            + 0.10 * risk_margin
        )
        details = {
            "candidate_id": candidate.candidate_id,
            "strategy": candidate.strategy,
            "score": round(score, 4),
            "semantic": round(semantic, 4),
            "baseline_novelty": round(baseline_novelty, 4),
            "recent_novelty": round(recent_novelty, 4),
            "smoothness": round(smoothness, 4),
            "risk_margin": round(risk_margin, 4),
        }
        scored.append((score, compiled, details))

    if not scored:
        raise ValueError(f"三个候选均未通过本地安全检查：{rejected}")
    scored.sort(key=lambda item: (item[0], item[2]["candidate_id"]), reverse=True)
    _score, selected, selected_details = scored[0]
    selected.metadata = {
        "selected_candidate": selected_details,
        "safe_candidate_scores": [details for _value, _motion, details in scored],
        "rejected_candidates": rejected,
        "selection_weights": {
            "semantic": 0.40,
            "baseline_novelty": 0.25,
            "recent_novelty": 0.15,
            "smoothness": 0.10,
            "risk_margin": 0.10,
        },
    }
    return selected
