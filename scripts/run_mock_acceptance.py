#!/usr/bin/env python3
"""Run each fixed cup path 20 times using the full mock study state machine."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from robot_game.config import load_study_config
from robot_game.models import WozCommand
from robot_game.session_manager import SessionManager


def woz(name: str, round_id: int, payload: dict | None = None) -> WozCommand:
    return WozCommand(name, str(uuid.uuid4()), payload or {}, "mock-acceptance", round_id)


async def run() -> int:
    source = json.loads((ROOT / "config" / "study.example.json").read_text(encoding="utf-8"))
    source["runtime"]["time_scale"] = 0
    source["runtime"]["inter_round_wait_s"] = 0
    with tempfile.TemporaryDirectory(prefix="aubo-acceptance-") as directory:
        root = Path(directory)
        config_path = root / "study.json"
        config_path.write_text(json.dumps(source), encoding="utf-8")
        os.environ["AUBO_LOG_ROOT"] = str(root / "logs")
        config = load_study_config(config_path)

        async def discard(message: dict) -> None:
            return None

        manager = SessionManager(config, ROOT / "config" / "robot.local.json", discard)
        await manager.start_session("mock-acceptance", "mock-acceptance", "task", False)
        for round_id in range(1, 61):
            cup_id = (round_id - 1) % 3 + 1
            await manager.handle(woz("user_setup_done", round_id))
            await manager.handle(woz("select_cup", round_id, {"cup_id": cup_id}))
            if round_id == 60:
                await manager.handle(woz("end_after_round", round_id))
            outcome = "win" if round_id % 2 else "loss"
            await manager.handle(woz("result", round_id, {"outcome": outcome}))

        status = manager.status()
        if status["stage"] != "FINISHED" or len(status["history"]) != 60:
            raise RuntimeError(f"Unexpected final state: {status}")
        adapter = manager.machine.executor.adapter
        expected_home = tuple(float(item) for item in config.workcell["joint_poses_rad"]["home"])
        if tuple(adapter.joints) != expected_home:
            raise RuntimeError("Mock robot did not return to Home")

        events = [
            json.loads(line)
            for line in (root / "logs" / "mock-acceptance" / "events.jsonl")
            .read_text(encoding="utf-8").splitlines()
        ]
        picks = [record for record in events if record["event"] == "task_pick_completed"]
        counts = {cup: sum(record["data"]["cup_id"] == cup for record in picks) for cup in (1, 2, 3)}
        if counts != {1: 20, 2: 20, 3: 20}:
            raise RuntimeError(f"Unexpected path counts: {counts}")
        errors = [record for record in events if record["level"] in {"ERROR", "CRITICAL"}]
        if errors:
            raise RuntimeError(f"Acceptance run contains {len(errors)} error events")
        print(f"PASS: fixed paths completed 20 times each; {len(events)} events; final Home verified")
        manager.machine.logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))

