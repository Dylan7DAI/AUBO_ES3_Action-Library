"""Offline end-to-end tests for the study state machine, safety log, and condition lock."""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from robot_game.config import StudyConfigError, load_study_config
from robot_game.models import WozCommand
from robot_game.session_manager import SessionManager
from robot_game.state_machine import StateTransitionError


ROOT = Path(__file__).resolve().parents[1]
STUDY_CONFIG = ROOT / "config" / "study.example.json"


def command(name: str, round_id: int, payload: dict | None = None) -> WozCommand:
    return WozCommand(
        command=name,
        request_id=str(uuid.uuid4()),
        payload=payload or {},
        session_id="S-test",
        round_id=round_id,
    )


class StudyConfigTests(unittest.TestCase):
    def test_example_is_mock_and_uncalibrated(self):
        config = load_study_config(STUDY_CONFIG)
        self.assertEqual(config.robot_mode, "mock")
        self.assertFalse(config.calibrated)
        self.assertEqual(len(config.safety["joint_min_rad"]), 6)
        self.assertEqual(config.llm["provider"], "openai")
        self.assertEqual(config.llm["model"], "gpt-5.6-terra")
        self.assertEqual(config.llm_prompt_path, ROOT / "config" / "openai_prompt.txt")
        self.assertTrue(config.llm_prompt_path.is_file())

    def test_real_mode_is_blocked_without_calibration(self):
        original = os.environ.get("AUBO_ROBOT_MODE")
        os.environ["AUBO_ROBOT_MODE"] = "real"
        try:
            with self.assertRaises(StudyConfigError):
                load_study_config(STUDY_CONFIG)
        finally:
            if original is None:
                os.environ.pop("AUBO_ROBOT_MODE", None)
            else:
                os.environ["AUBO_ROBOT_MODE"] = original


class StudyFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.previous_log_root = os.environ.get("AUBO_LOG_ROOT")
        os.environ["AUBO_LOG_ROOT"] = self.temp.name
        self.config = load_study_config(STUDY_CONFIG)
        self.messages: list[dict] = []

        async def broadcast(message: dict) -> None:
            self.messages.append(message)

        self.manager = SessionManager(
            self.config, ROOT / "config" / "robot.local.json", broadcast
        )
        await self.manager.start_session("P-test", "S-test", "task", False)

    async def _cleanup(self):
        if self.manager.machine:
            await self.manager.machine.executor.close()
            self.manager.machine.logger.close()
        if self.previous_log_root is None:
            os.environ.pop("AUBO_LOG_ROOT", None)
        else:
            os.environ["AUBO_LOG_ROOT"] = self.previous_log_root
        self.temp.cleanup()

    async def test_complete_one_round_and_end(self):
        self.assertEqual(self.manager.status()["stage"], "WAIT_USER_SETUP")
        self.assertTrue(self.manager.status()["robot_connected"])
        self.assertEqual(self.manager.status()["robot_safety_mode"], "Normal")
        await self.manager.handle(command("user_setup_done", 1))
        self.assertEqual(self.manager.status()["stage"], "WAIT_CUP_SELECTION")
        await self.manager.handle(command("select_cup", 1, {"cup_id": 2}))
        self.assertEqual(self.manager.status()["stage"], "WAIT_RESULT")
        await self.manager.handle(command("end_after_round", 1))
        await self.manager.handle(command("result", 1, {"outcome": "win"}))
        status = self.manager.status()
        self.assertEqual(status["stage"], "FINISHED")
        self.assertEqual(status["wins"], 1)
        self.assertEqual(status["condition"], "task")
        self.assertFalse(status["robot_connected"])
        self.assertEqual(status["history"][0]["selected_cup"], 2)

        events_path = Path(self.temp.name) / "S-test" / "events.jsonl"
        records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertGreater(len(records), 25)
        previous = "0" * 64
        safety_events = 0
        for record in records:
            self.assertEqual(record["previous_hash"], previous)
            digest = record.pop("record_hash")
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(hashlib.sha256(canonical.encode("utf-8")).hexdigest(), digest)
            previous = digest
            safety_events += record["category"] == "safety"
        self.assertGreater(safety_events, 20)

    async def test_out_of_order_command_is_rejected_without_state_change(self):
        with self.assertRaises(StateTransitionError):
            await self.manager.handle(command("select_cup", 1, {"cup_id": 1}))
        self.assertEqual(self.manager.status()["stage"], "WAIT_USER_SETUP")

    async def test_stale_round_command_is_rejected(self):
        with self.assertRaises(StateTransitionError):
            await self.manager.handle(command("user_setup_done", 2))

    async def test_emergency_stop_enters_error_immediately(self):
        stop_command = command(
            "emergency_stop", 1, {"reason": "operator_detected_risk"}
        )
        await self.manager.handle(stop_command)
        self.assertEqual(self.manager.status()["stage"], "ERROR")
        self.assertIn("Emergency stop", self.manager.status()["error"])
        records = [
            json.loads(line)
            for line in (Path(self.temp.name) / "S-test" / "events.jsonl")
            .read_text(encoding="utf-8").splitlines()
        ]
        audit = [
            item for item in records
            if item["request_id"] == stop_command.request_id and item["category"] == "woz"
        ]
        self.assertEqual([item["event"] for item in audit], ["command_received", "command_ack"])

    async def test_emergency_stop_cannot_be_overwritten_by_active_action(self):
        active = asyncio.create_task(self.manager.handle(command("user_setup_done", 1)))
        await asyncio.sleep(0.005)
        await self.manager.handle(
            command("emergency_stop", 1, {"reason": "risk_during_expression"})
        )
        with self.assertRaises(StateTransitionError):
            await active
        self.assertEqual(self.manager.status()["stage"], "ERROR")


if __name__ == "__main__":
    unittest.main()
