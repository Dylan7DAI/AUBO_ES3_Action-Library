# Requirements traceability — supplied v0.9 document

This matrix maps the supplied 21-page AUBO-ES3 multi-round cup-game
specification to implementation and verification evidence.

| Requirement area | Implementation | Verification/status |
|---|---|---|
| Robot connect, power, startup, state, motion, stop | `src/aubo_sdk_client/client.py`, `robot_game/robot_adapter.py` | Low-level tests; real controller validation is a deployment gate |
| Fixed identical cup task trajectories | `config/study.example.json`, `RobotExecutor.execute_pick/execute_place` | Task library SHA-256 logged; mock acceptance repeats each cup 20 times |
| Laban-inspired six-action API | `PARAMETER_RULES`, `DEFAULT_PARAMETERS`, calibrated expressive templates/renderer | Boundary tests for every parameter; real Laban review remains required |
| Task-only condition | `TaskOnlyStrategy` | Time-matched registered neutral waits; strategy test |
| History-aware rule condition | `RuleBasedStrategy` | Fixed functions/bounds and outcome direction test |
| LLM condition | `OpenAIResponsesLLMClient`, `LLMStrategy` | Official Responses API, strict Structured Outputs, forbidden pose/code fields, one retry, neutral invalid-round fallback |
| Formal condition lock | `StudyConfig.formal_condition_for`, `SessionManager.start_session` | Formal config requires pre-registration and matching request |
| Multi-round state machine | `GameStateMachine` | Full round and illegal/stale command tests |
| Five-second inter-round wait | `inter_round_wait_s=5`; `time_scale=1` forced for formal studies | Config validation; mock may accelerate time only in pilot |
| Normal end after round | `_end_after_round`, `_close_session` | Full-session test |
| Immediate emergency stop | background WebSocket command tasks, queue bypass, `stopJoint` | Concurrency test proves ERROR cannot be overwritten; real latency requires hardware |
| WoZ ready/cup/result/labels/end/abort | backend commands and `static/index.html` controls | WebSocket integration and state tests |
| Pilot manual action/retry | `_pilot_action`, `_reset_error`, pilot console | Disabled in formal and real recovery; logged as invalid |
| WebSocket request ID and ack/error | `WozCommand`, `/ws` | Integration test confirms same request ID |
| LLM prompt/history/output contract | `strategies.py` | Prompt hash/version/raw/parsed/latency/retry events; schema tests |
| Action whitelist and phase/range validation | `safety_validator.py` | Every boundary and forbidden output tests |
| Single robot action queue | `RobotExecutor._motion_lock` | All normal motion goes through one executor; emergency is explicit bypass |
| Append-only organized logs | `event_logger.py` | Per-session files, fsync, sequence and SHA-256 chain test/tool |
| Required log fields | state/session/strategy/executor logging | Session, round, WoZ, LLM, action, SDK, error and safety categories emitted |
| Safety telemetry | expanded `RobotSnapshot`, per-move safety checks | Unsupported SDK fields recorded explicitly as unavailable/read error |
| Ubuntu 20.04 / Conda Python 3.10 | vendored manylinux wheel, setup/verify scripts, systemd template | Wheel SHA-256 verified; installer enforces Ubuntu 20.04 x86-64 |
| Researcher-only access | token auth, localhost default, non-local token gate | Formal mode rejects default/missing token |
| Error handling/invalid rounds | state `ERROR`, `invalid_reason`, LLM failure policy | Unit tests and structured error events |

## Items that require the physical study setup

The specification itself leaves Q1–Q10 and all exact workcell values open. The
software therefore blocks real mode until these are supplied rather than inventing
unsafe values. Completion on the target hardware requires:

- gripper model and verified DO/DI mapping;
- three cup, place, retreat, Home, TCP, and user-direction calibration;
- official ES3/controller limits and collision workspace;
- guarded low-speed real validation of all points and action variants;
- 20 real repetitions per cup path without collision, drift, or drop;
- controller/ARCS compatibility and measured software-stop latency;
- final formal condition assignments and LLM provider/model/network policy.

These are deployment/experimental decisions, not missing unrestricted motion code.
