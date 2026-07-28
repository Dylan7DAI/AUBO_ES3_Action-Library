# AUBO ES3 HCI study system: deployment and calibration

## System boundary

The implementation follows requirement document v0.9. It is intentionally a
repeatable WoZ research platform, not an autonomous vision system. Researchers
provide shuffle completion, cup choice, outcome, and optional response labels.
The LLM can select only a registered expressive function and bounded parameters;
it cannot output joint positions, TCP poses, code, URLs, files, or task choices.

The task path is one shared `RobotExecutor` implementation for all three
conditions. Task-only, rule, and LLM code can only select expressive actions.
The selected condition is immutable for the lifetime of a session.
In formal mode it must also match `experiment.formal_condition_assignments`, keyed
by session ID (preferred) or participant ID; the web request cannot override it.

```text
Researcher browser
  └─ authenticated WebSocket commands with request_id
       └─ strict GameStateMachine
            ├─ ConditionStrategy (task | rule | llm)
            │    └─ SafetyValidator → Expressive motion
            └─ fixed cup TaskMotionLibrary
                 └─ single RobotExecutor queue
                      ├─ MockRobotAdapter
                      └─ AuboRobotAdapter → pyaubo_sdk RPC
```

Emergency stop is the single exception to the motion queue: it deliberately
bypasses the normal command lock and calls `stopJoint` immediately. This is a
best-effort network/software stop. The physical emergency stop remains the
primary safety control.

## State and legal WoZ commands

| State | Accepted command | Result |
|---|---|---|
| `WAIT_USER_SETUP` | `user_setup_done` | pre-task expression, then cup wait |
| `WAIT_CUP_SELECTION` | `select_cup(1..3)` | fixed pick trajectory |
| `WAIT_RESULT` | `result(win/loss)` | fixed place, result expression, round log |
| Most active states | `end_after_round` | queue normal close after current round |
| Any active state | `emergency_stop` | immediate SDK stop and `ERROR` |
| Pilot `ERROR` in mock mode | `reset_error` | researcher-supervised mock recovery |

`user_response` is an optional label-only event. Every accepted command receives
`woz.ack` with the same `request_id`; malformed, stale, or illegal commands receive
`woz.error`. A command with a stale `round_id` cannot affect a later round.

## Safety checks and logged parameters

Before and during an action the runtime records each check separately, including
requested value, configured limit, validated value, and pass/fail:

- action whitelist and legal stage;
- exact parameter schema and every individual parameter range;
- estimated stage duration budget;
- workcell calibration and named-pose registration;
- current and target six-joint finiteness;
- current/target joint soft limits and per-command joint delta;
- physical velocity and acceleration after normalized scaling;
- robot power, mode, safety mode, steady/collision/within-limits flags;
- all SDK telemetry exposed by the installed controller/SDK combination;
- command target, SDK result/error code, and actual elapsed time.

The event stream is append-only and hash chained. Editing, inserting, reordering,
or deleting an internal record causes `scripts/verify_event_log.py` to fail. Detecting
suffix truncation requires comparing the final hash/count with a separately archived
run record. Copy completed session directories to read-only research storage; the local
hash chain is an integrity detector, not a replacement for access-controlled archival
storage.

## Ubuntu 20.04 installation

Supported deployment target: Ubuntu 20.04 x86-64 with Conda CPython 3.10. Do not use
Ubuntu 20.04's system Python 3.8: the current application dependencies require newer
Python. The vendored wheel is `manylinux2014_x86_64` and version-pinned to the stable
0.24.1 release; Ubuntu 20.04's glibc 2.31 satisfies the wheel's glibc 2.17 baseline.
Because Ubuntu 20.04 is outside standard maintenance, enable Ubuntu Pro/ESM and apply
current security updates before study deployment; see the official
[Ubuntu release lifecycle](https://wiki.ubuntu.com/Releases).
Install Miniconda or Anaconda first; the setup script creates the named Conda
environment `aubo` from `environment.yml`. Follow the official
[Conda Linux installation guide](https://docs.conda.io/projects/conda/en/stable/user-guide/install/linux.html)
when `conda` is not already installed.

```bash
chmod +x scripts/*.sh
./scripts/setup_ubuntu20.sh
conda activate aubo
python -m unittest discover -s tests -v
python main.py study --study-config config/study.local.json \
  --robot-config config/robot.local.json --host 127.0.0.1 --port 8000
```

The environment is consistently named `aubo`. Interactive commands use
`conda activate aubo`; the systemd template uses `conda run --name aubo`, so it does
not depend on interactive shell activation. Adjust the Conda executable path in the
service template when Miniconda is not installed at `/opt/miniconda3`.

The vendor's current Python package page documents Linux x64 wheels and installation
through pip: <https://pypi.org/project/pyaubo-sdk/>. AUBO's SDK resource index is
<https://developer.aubo-robotics.cn/application_notes/55-aubo-sdk-resources-guide/>.

For a dedicated machine, adapt `deploy/aubo-study.service`, create a non-login
`aubo-study` user, store secrets in `/etc/aubo-es3-study.env` with mode `0600`, and
allow only the log directory to be writable. Keep the service bound to localhost
behind an authenticated TLS proxy if it must be used from another computer.

## Real-workcell calibration checklist

The requirement document deliberately leaves the gripper, cup coordinates, Home,
user direction, workspace boundary, controller/ARCS version, and LLM provider open.
The repository therefore ships example values with both calibration flags false.

Record the following in the local configuration and lab runbook:

- ES3 serial/model, controller hardware, ARCS/firmware, and `pyaubo-sdk` versions;
- authoritative joint and controller safety limits and their source;
- tool mass, center of mass, TCP, gripper model, DO/DI mapping, and feedback polarity;
- Home, three cup `pre/pick/lift` poses, place/retreat poses, and user direction;
- table, participant, cable, self-collision, singularity, and protected-stop margins;
- safe speed/acceleration and stop-deceleration validated during guarded low-speed hardware tests;
- 20 consecutive runs of each fixed cup path without collision, drift, or dropped cups;
- minimum/default/maximum variants of every expressive action, reviewed for Laban intent;
- formal condition assignment and session-level locking method;
- LLM provider/model, network assumptions, prompt version, timeout, and failure policy.

Use `mock` first, then guarded single-waypoint real tests at the lowest safe speed,
then whole paths. Do not set `calibrated=true` merely to bypass the startup gate.

## OpenAI online emotion reasoning

The default configuration uses the official OpenAI Responses API:

```json
{
  "provider": "openai",
  "api_key_env": "OPENAI_API_KEY",
  "prompt_file": "config/openai_prompt.txt",
  "model": "gpt-5.6-terra",
  "reasoning_effort": "low",
  "max_output_tokens": 1200,
  "timeout_s": 5.0
}
```

Set the key in the process environment or the protected systemd environment file:

```bash
export OPENAI_API_KEY="..."
```

The adapter calls `client.responses.create` with `store=false` and strict
`text.format.type=json_schema`. OpenAI returns the four-dimensional affective state,
registered action function/variant, bounded parameters, and history factors. The API
key is never included in prompts or logs. Missing credentials reject an LLM session
before any experiment actions start.

The entire editable prompt is stored in `config/openai_prompt.txt`; no fixed prompt
instructions or few-shot examples are embedded in Python. Keep each `{{...}}`
placeholder exactly once. At runtime the strategy fills the registered variants,
game context, output schema, and optional retry validation error. The configured file
path and its SHA-256 are logged automatically, so every prompt revision is traceable.

For a self-hosted provider-compatible service, `llm.provider=http_json` remains
available. That adapter sends:

```json
{"model":"...","prompt":"...","json_schema":{}}
```

with an optional bearer token from `llm.api_key_env`. The endpoint must return a
JSON action object directly or under `output`. Both adapters log the input context,
prompt version/hash, raw response, parsed response, latency, retry, and validation
result. First invalid output is retried once with the validation error. A second
failure executes a registered `neutral_wait`, marks the round `LLM_FAILURE`, and
does not substitute a rule action, preventing silent condition contamination.

## Remaining hardware acceptance work

The software can be fully exercised in mock mode. The following cannot be proven
without the actual lab hardware and decisions from requirement section 11.2:

- collision-free calibrated coordinates and expressive trajectory semantics;
- gripper electrical mapping, timing, and actual grasp feedback;
- controller/SDK compatibility and real stop latency;
- LLM provider/model/network latency and formal-study policy;
- study counterbalancing and researcher cup-choice protocol.

These are explicit deployment gates, not silent TODOs in a motion path.
