"""Authenticated HTTP/WebSocket integration checks for the researcher console."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # Core safety tests remain runnable before Web extras are installed.
    TestClient = None

from robot_game.app import build_parser, create_app


ROOT = Path(__file__).resolve().parents[1]


class StudyCliHelpTests(unittest.TestCase):
    def test_study_help_explains_config_defaults(self):
        help_text = build_parser().format_help()
        self.assertIn("study.example.json", help_text)
        self.assertIn("robot.local.json", help_text)
        self.assertIn("--study-config config/study.local.json", help_text)


@unittest.skipUnless(TestClient is not None, "FastAPI test dependencies are not installed")
class WebConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_log_root = os.environ.get("AUBO_LOG_ROOT")
        self.previous_token = os.environ.get("AUBO_RESEARCHER_TOKEN")
        os.environ["AUBO_LOG_ROOT"] = self.temp.name
        os.environ["AUBO_RESEARCHER_TOKEN"] = "integration-test-token"
        self.app = create_app(
            ROOT / "config" / "study.example.json",
            ROOT / "config" / "robot.local.json",
        )

    def tearDown(self):
        machine = self.app.state.session_manager.machine
        if machine is not None:
            machine.executor.adapter.close()
            machine.logger.close()
        if self.previous_log_root is None:
            os.environ.pop("AUBO_LOG_ROOT", None)
        else:
            os.environ["AUBO_LOG_ROOT"] = self.previous_log_root
        if self.previous_token is None:
            os.environ.pop("AUBO_RESEARCHER_TOKEN", None)
        else:
            os.environ["AUBO_RESEARCHER_TOKEN"] = self.previous_token
        self.temp.cleanup()

    @staticmethod
    def _receive_type(websocket, message_type, request_id=None):
        for _ in range(500):
            message = websocket.receive_json()
            if message.get("type") != message_type:
                continue
            if request_id is None or message.get("request_id") == request_id:
                return message
        raise AssertionError(f"Did not receive {message_type} for {request_id}")

    def test_authenticated_session_command_ack_and_live_events(self):
        headers = {"X-Researcher-Token": "integration-test-token"}
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/status").status_code, 401)
            self.assertFalse(client.get("/api/status", headers=headers).json()["robot_connected"])
            with client.websocket_connect("/ws?token=integration-test-token") as websocket:
                initial = self._receive_type(websocket, "system.status")
                self.assertFalse(initial["payload"]["active"])
                response = client.post("/api/session", headers=headers, json={
                    "participant_id": "P-web",
                    "session_id": "S-web",
                    "condition": "rule",
                    "formal_study": False,
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["robot_connected"])
                event = self._receive_type(websocket, "system.event")
                self.assertIn("category", event["payload"])

                request_id = "web-setup-1"
                websocket.send_json({
                    "type": "woz.command",
                    "session_id": "S-web",
                    "round_id": 1,
                    "timestamp": "2026-07-28T00:00:00Z",
                    "command": "user_setup_done",
                    "payload": {},
                    "request_id": request_id,
                })
                ack = self._receive_type(websocket, "woz.ack", request_id)
                self.assertEqual(ack["payload"]["state"]["stage"], "WAIT_CUP_SELECTION")
        self.assertFalse(
            self.app.state.session_manager.machine.state.robot_connected,
            "Application shutdown must disconnect an active robot session",
        )


if __name__ == "__main__":
    unittest.main()
