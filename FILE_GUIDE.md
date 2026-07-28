# Project file guide

This guide introduces every maintained file in the repository. Generated files
such as `__pycache__`, Conda environments, local secrets, and session logs are intentionally
excluded from version control.

## Root files

| File | Purpose |
|---|---|
| `.env.example` | Template for robot credentials, runtime mode, researcher token, log location, and LLM API key. Copy values into a protected environment file; never commit secrets. |
| `.gitignore` | Excludes Python caches, IDE files, local credentials/configuration, ROS output, models, datasets, and study logs. |
| `README.md` | Main operator/developer introduction, mock quick start, Ubuntu setup, real-robot gates, and logging overview. |
| `FILE_GUIDE.md` | This file-by-file map. |
| `main.py` | Unified CLI. Dispatches legacy diagnostics/actions and the `study` researcher web service. |
| `environment.yml` | Definition for the consistently named `aubo` Conda environment, pinning Python 3.10 and pip. |
| `requirements.txt` | Python web/runtime dependencies and the Linux-only pinned AUBO SDK requirement. |

## Configuration

| File | Purpose |
|---|---|
| `config/robot.example.json` | AUBO RPC credentials template plus six-joint soft limits, velocity, acceleration, and startup timing. Copy to ignored `robot.local.json`. |
| `config/study.example.json` | Mock-safe study configuration: experiment lock, normalized/physical safety ranges, fixed cup/Home paths, expressive templates, gripper adapter, LLM adapter, and logging/runtime settings. Copy to ignored `study.local.json`. All included poses are examples, not real-workcell calibration. |
| `config/openai_prompt.txt` | Single editable OpenAI prompt template containing all fixed instructions, the few-shot example, retry guidance, and the four runtime placeholders. |

## HCI study runtime (`robot_game`)

| File | Purpose |
|---|---|
| `robot_game/__init__.py` | Package metadata and study runtime version. |
| `robot_game/app.py` | FastAPI HTTP service, authenticated researcher WebSocket, per-connection send serialization, static console serving, command acknowledgements, and background command processing so emergency stop remains responsive. |
| `robot_game/config.py` | Loads and validates study JSON, environment overrides, formal-study assignment/token/timing locks, calibration gates, and SHA-256 config fingerprint. |
| `robot_game/event_logger.py` | Creates one session directory, rotating readable logs, atomic summaries, and fsynced SHA-256 hash-chained JSONL events. |
| `robot_game/models.py` | Enums and dataclasses for conditions, stages, outcomes, action plans, rounds, sessions, and validated WoZ messages. |
| `robot_game/robot_adapter.py` | Mock and real AUBO adapters, digital-output gripper, fixed task library fingerprint, calibrated expressive renderer, single motion queue, emergency stop, SDK telemetry, and per-command safety logging. |
| `robot_game/safety_validator.py` | Shared action whitelist, phase rules, exact parameter schemas/ranges, duration limits, and robot-state preconditions for both rule and LLM conditions. |
| `robot_game/session_manager.py` | Owns one active session, locks formal condition assignments, constructs adapters/strategy/logger, and broadcasts live server events. |
| `robot_game/state_machine.py` | Implements the complete session/round state machine, legal WoZ inputs, fixed task ordering, five-second inter-round wait, deferred normal end, immediate stop, pilot recovery, and invalid-round marking. |
| `robot_game/strategies.py` | Task-only time matching, history-aware rule policy, official OpenAI Responses API client, generic HTTP and pilot mock adapters, strict emotion/action output parsing, one retry, and neutral failure behavior. |

## Researcher web console (`robot_game/static`)

| File | Purpose |
|---|---|
| `robot_game/static/index.html` | Researcher-only interface for session setup, ready/cup/result labels, condition display, normal end, abort, emergency stop, pilot actions/recovery, metrics, and live events. |
| `robot_game/static/app.js` | WebSocket lifecycle, UUID request messages, ack/error handling, legal-stage button locking, live server event display, pilot visibility, and REST session creation. |
| `robot_game/static/styles.css` | Responsive visual design and safety/action status styling; contains no external web assets. |

## AUBO SDK access (`src/aubo_sdk_client`)

| File | Purpose |
|---|---|
| `src/aubo_sdk_client/__init__.py` | Stable public imports for the low-level SDK package. |
| `src/aubo_sdk_client/client.py` | Lazy `pyaubo_sdk` RPC connection/login, lifecycle, power/startup, comprehensive state/safety telemetry, joint movement, stopJoint, and gripper digital I/O. |
| `src/aubo_sdk_client/config.py` | Robot credential/environment loading and validation of six-joint limits and motion parameters. |
| `src/aubo_sdk_client/safety.py` | Pure joint-vector, finiteness, relative increment, and soft-limit validation used by low-level tools. |

## Existing standalone tools (`host_tools`)

| File | Purpose |
|---|---|
| `host_tools/__init__.py` | Marks the standalone tools package. |
| `host_tools/diagnose_robot.py` | Read-only robot connection, joint/TCP state display, and JSON diagnostics. |
| `host_tools/safe_joint_test.py` | Default-dry-run relative joint test requiring two explicit execution confirmations. |
| `host_tools/nod_action.py` | Legacy full-body/wrist nod trajectory generator and guarded real execution. |
| `host_tools/cup_action.py` | Legacy single cup round-trip action, TCP error report, guarded motion, and return-code handling. |

## Deployment and verification scripts

| File | Purpose |
|---|---|
| `scripts/download_pyaubo_linux.sh` | Re-downloads the exact official CPython 3.10 manylinux x86-64 AUBO wheel and verifies its pinned SHA-256 hash. |
| `scripts/setup_ubuntu20.sh` | Enforces Ubuntu 20.04 x86-64, verifies the wheel, creates or updates the named `aubo` Conda environment, installs dependencies, and runs import/config verification. |
| `scripts/verify_install.py` | Read-only dependency, platform, SDK import, and study-config verification; never connects to the robot. |
| `scripts/verify_openai_llm.py` | Makes one explicitly requested paid OpenAI call, validates the returned emotion/action plan, writes preflight logs, and never connects to the robot. |
| `scripts/verify_event_log.py` | Recomputes a session JSONL sequence and hash chain to detect content edits, internal deletion, insertion, or reordering. |
| `scripts/run_mock_acceptance.py` | Runs the complete mock state machine through each cup path 20 times, verifies 60 rounds, no error events, and final Home. |
| `deploy/aubo-study.service` | Hardened systemd service template for a dedicated Ubuntu study account and `/opt/aubo-es3-study` deployment. |

## Documentation

| File | Purpose |
|---|---|
| `docs/study_system.md` | Architecture, protocol, safety/logging behavior, Ubuntu deployment, workcell calibration, LLM endpoint contract, and hardware acceptance gates. |
| `docs/requirements_traceability.md` | Requirement-to-code/test matrix for the supplied v0.9 research specification. |
| `docs/safety_checklist.md` | Original pre-run software, workcell, execution, and post-run physical safety checklist. |
| `docs/implementation_plan.md` | Original longer-term ROS 2, simulation, vision, and learning roadmap; those out-of-scope future milestones are distinct from the v0.9 WoZ study runtime. |

## Tests

| File | Purpose |
|---|---|
| `tests/test_config.py` | Robot config and secret override tests. |
| `tests/test_safety.py` | Low-level joint target/increment safety tests. |
| `tests/test_diagnose_robot.py` | Read-only pose output tests. |
| `tests/test_main.py` | Unified CLI/menu dispatch tests. |
| `tests/test_nod_action.py` | Legacy nod interpolation, limits, return-to-start, and speed tests. |
| `tests/test_cup_action.py` | Legacy cup plan, dwell, limits, TCP error, and SDK-code tests. |
| `tests/test_study_system.py` | Full mock session, ordering, stale messages, emergency concurrency, structured logging, and hash-chain tests. |
| `tests/test_strategies_and_validation.py` | Strategy behavior, OpenAI Responses/Structured Outputs contract, strict LLM retry/schema checks, every parameter boundary, rendered return-to-base, and gripper regression tests. |
| `tests/test_web_console.py` | Authenticated HTTP/WebSocket integration test, including live events and request-ID-correlated acknowledgements. |

## Vendored SDK

| File | Purpose |
|---|---|
| `vendor/pyaubo_sdk/pyaubo_sdk-0.24.1-cp310-cp310-manylinux2014_x86_64.whl` | Official stable Linux x86-64 AUBO Python SDK wheel used by Ubuntu 20.04's Conda Python 3.10 environment. |
| `vendor/pyaubo_sdk/SHA256SUMS` | Expected wheel digest. |
| `vendor/pyaubo_sdk/README.md` | Platform tags, origin, verification, and safe-install notes for the wheel. |
