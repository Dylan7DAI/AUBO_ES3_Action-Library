"""加载并校验研究配置，同时生成用于实验追溯的不可变指纹。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StudyConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StudyConfig:
    path: Path
    raw: dict[str, Any]
    version: str
    robot_mode: str
    formal_study: bool
    log_root: Path
    inter_round_wait_s: float
    time_scale: float
    calibrated: bool
    expressive_calibrated: bool
    gripper_type: str
    config_hash: str

    @property
    def safety(self) -> dict[str, Any]:
        return self.raw["safety"]

    @property
    def workcell(self) -> dict[str, Any]:
        return self.raw["workcell"]

    @property
    def llm(self) -> dict[str, Any]:
        return self.raw.get("llm", {})

    @property
    def llm_prompt_path(self) -> Path:
        return _resolve_prompt_path(self.path, str(self.llm.get("prompt_file", "")))

    @property
    def researcher_token(self) -> str:
        return os.getenv("AUBO_RESEARCHER_TOKEN", self.raw.get("web", {}).get("token", ""))

    def formal_condition_for(self, participant_id: str, session_id: str) -> str | None:
        assignments = self.raw.get("experiment", {}).get("formal_condition_assignments", {})
        if not isinstance(assignments, dict):
            return None
        return assignments.get(session_id, assignments.get(participant_id))


def _finite_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise StudyConfigError(f"{name} must be numeric") from exc
    if not minimum <= number <= maximum:
        raise StudyConfigError(f"{name} must be within [{minimum}, {maximum}]")
    return number


def _resolve_prompt_path(config_path: Path, configured_value: str) -> Path:
    configured = Path(configured_value).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    candidates = (
        config_path.parent / configured,
        config_path.parents[1] / configured,
        Path.cwd() / configured,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return candidates[1].resolve()


def load_study_config(path: str | Path = "config/study.example.json") -> StudyConfig:
    """在连接机器人前拒绝危险或不完整配置，并返回只读配置对象。"""

    config_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise StudyConfigError(f"Study config not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise StudyConfigError(f"Invalid study JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise StudyConfigError("Study config root must be an object")
    version = str(raw.get("version", "")).strip()
    if not version:
        raise StudyConfigError("version is required")
    runtime = raw.get("runtime", {})
    robot_mode = os.getenv("AUBO_ROBOT_MODE", runtime.get("robot_mode", "mock"))
    if robot_mode not in {"mock", "real"}:
        raise StudyConfigError("runtime.robot_mode must be mock or real")
    formal = bool(runtime.get("formal_study", False))
    wait = _finite_number(runtime.get("inter_round_wait_s", 5), "inter_round_wait_s", 0, 60)
    time_scale = _finite_number(runtime.get("time_scale", 1), "time_scale", 0, 1)
    if formal and time_scale != 1:
        raise StudyConfigError("Formal-study mode requires runtime.time_scale=1")
    log_root = Path(os.getenv("AUBO_LOG_ROOT", runtime.get("log_root", "logs"))).expanduser()
    if not log_root.is_absolute():
        log_root = (config_path.parents[1] / log_root).resolve()

    safety = raw.get("safety")
    workcell = raw.get("workcell")
    if not isinstance(safety, dict) or not isinstance(workcell, dict):
        raise StudyConfigError("safety and workcell objects are required")
    for name in ("speed_scale", "accel_scale"):
        bounds = safety.get(name)
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise StudyConfigError(f"safety.{name} must be [min, max]")
        low = _finite_number(bounds[0], f"safety.{name}[0]", 0.01, 1)
        high = _finite_number(bounds[1], f"safety.{name}[1]", 0.01, 1)
        if low > high:
            raise StudyConfigError(f"safety.{name} minimum exceeds maximum")
    _finite_number(safety.get("max_action_duration_s", 20), "max_action_duration_s", 0.1, 120)
    _finite_number(safety.get("stop_deceleration_rad_s2", 1), "stop_deceleration", 0.01, 20)
    vectors: dict[str, tuple[float, ...]] = {}
    for name in ("joint_min_rad", "joint_max_rad", "max_command_delta_rad"):
        value = safety.get(name)
        if not isinstance(value, list) or len(value) != 6:
            raise StudyConfigError(f"safety.{name} must contain six numbers")
        vectors[name] = tuple(
            _finite_number(item, f"safety.{name}[{index}]", -20 if name != "max_command_delta_rad" else 0.001, 20)
            for index, item in enumerate(value)
        )
    for index, (minimum, maximum, delta) in enumerate(zip(
        vectors["joint_min_rad"], vectors["joint_max_rad"], vectors["max_command_delta_rad"]
    )):
        if minimum >= maximum or delta <= 0:
            raise StudyConfigError(f"Invalid joint safety range for J{index + 1}")

    calibrated = bool(workcell.get("calibrated", False))
    expressive_calibrated = bool(workcell.get("expressive_calibrated", False))
    gripper_type = str(workcell.get("gripper", {}).get("type", "unsupported"))
    # 真机门必须在创建 AuboRobotAdapter 之前通过，不能靠运行中警告代替。
    if robot_mode == "real" and not calibrated:
        raise StudyConfigError("Real mode is blocked until workcell.calibrated=true")
    if robot_mode == "real" and not expressive_calibrated:
        raise StudyConfigError("Real mode is blocked until workcell.expressive_calibrated=true")
    if robot_mode == "real" and gripper_type != "standard_digital_output":
        raise StudyConfigError(
            "Real mode currently requires workcell.gripper.type=standard_digital_output"
        )
    researcher_token = os.getenv("AUBO_RESEARCHER_TOKEN", raw.get("web", {}).get("token", ""))
    if formal and researcher_token in {"", "CHANGE_ME"}:
        raise StudyConfigError("Formal-study mode requires a non-default AUBO_RESEARCHER_TOKEN")
    assignments = raw.get("experiment", {}).get("formal_condition_assignments", {})
    if not isinstance(assignments, dict) or any(
        value not in {"task", "rule", "llm"} for value in assignments.values()
    ):
        raise StudyConfigError(
            "experiment.formal_condition_assignments must map IDs to task/rule/llm"
        )
    if formal and not assignments:
        raise StudyConfigError("Formal-study mode requires pre-registered condition assignments")
    llm = raw.get("llm", {})
    if not isinstance(llm, dict):
        raise StudyConfigError("llm must be an object")
    _finite_number(llm.get("timeout_s", 5), "llm.timeout_s", 0.1, 60)
    provider = str(llm.get("provider", "disabled"))
    if provider not in {"mock", "openai", "http_json", "disabled"}:
        raise StudyConfigError("llm.provider must be mock, openai, http_json, or disabled")
    if provider in {"mock", "openai", "http_json"}:
        prompt_file = str(llm.get("prompt_file", "")).strip()
        if not prompt_file:
            raise StudyConfigError("llm.prompt_file is required for an LLM provider")
        prompt_path = _resolve_prompt_path(config_path, prompt_file)
        try:
            prompt_template = prompt_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StudyConfigError(f"Cannot read llm.prompt_file: {prompt_path}") from exc
        required_prompt_tokens = {
            "{{REGISTERED_VARIANTS_JSON}}",
            "{{GAME_CONTEXT_JSON}}",
            "{{ALLOWED_OUTPUT_SCHEMA_JSON}}",
            "{{VALIDATION_ERROR_JSON}}",
        }
        missing_tokens = sorted(
            token for token in required_prompt_tokens if prompt_template.count(token) != 1
        )
        if missing_tokens:
            raise StudyConfigError(
                "llm.prompt_file must contain each required placeholder exactly once: "
                + ", ".join(missing_tokens)
            )
    if provider == "openai":
        if not str(llm.get("model", "")).strip():
            raise StudyConfigError("OpenAI LLM requires llm.model")
        api_key_env = str(llm.get("api_key_env", "OPENAI_API_KEY"))
        valid_env_name = (
            bool(api_key_env) and api_key_env.isascii()
            and (api_key_env[0].isalpha() or api_key_env[0] == "_")
            and all(character.isalnum() or character == "_" for character in api_key_env)
        )
        if not valid_env_name:
            raise StudyConfigError("llm.api_key_env must be an environment-variable name")
        if llm.get("reasoning_effort", "low") not in {
            "none", "low", "medium", "high", "xhigh", "max"
        }:
            raise StudyConfigError("Invalid llm.reasoning_effort")
        tokens = llm.get("max_output_tokens", 1200)
        if not isinstance(tokens, int) or isinstance(tokens, bool) or not 128 <= tokens <= 8192:
            raise StudyConfigError("llm.max_output_tokens must be an integer in [128, 8192]")
    if llm.get("provider") == "http_json":
        if not str(llm.get("endpoint", "")).strip() or not str(llm.get("model", "")).strip():
            raise StudyConfigError("http_json LLM requires both llm.endpoint and llm.model")

    # 对排序后的完整配置取 hash，使每场日志可以追溯到精确配置内容。
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return StudyConfig(
        path=config_path,
        raw=raw,
        version=version,
        robot_mode=robot_mode,
        formal_study=formal,
        log_root=log_root,
        inter_round_wait_s=wait,
        time_scale=time_scale,
        calibrated=calibrated,
        expressive_calibrated=expressive_calibrated,
        gripper_type=gripper_type,
        config_hash=fingerprint,
    )
