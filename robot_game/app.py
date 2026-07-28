"""FastAPI HTTP/WebSocket service for the researcher Wizard-of-Oz console."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from .config import StudyConfigError, load_study_config
from .models import WozCommand
from .session_manager import SessionManager


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"


class ConnectionHub:
    def __init__(self) -> None:
        self.connections: set[Any] = set()
        self.send_locks: dict[Any, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: Any) -> None:
        await websocket.accept()
        async with self._lock:
            self.connections.add(websocket)
            self.send_locks[websocket] = asyncio.Lock()

    async def disconnect(self, websocket: Any) -> None:
        async with self._lock:
            self.connections.discard(websocket)
            self.send_locks.pop(websocket, None)

    async def send(self, websocket: Any, message: dict[str, Any]) -> None:
        async with self._lock:
            lock = self.send_locks.get(websocket)
        if lock is None:
            return
        async with lock:
            await websocket.send_json(message)

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale: list[Any] = []
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            targets = tuple(self.connections)
        for connection in targets:
            try:
                lock = self.send_locks.get(connection)
                if lock is None:
                    continue
                async with lock:
                    await connection.send_text(payload)
            except Exception:
                stale.append(connection)
        if stale:
            async with self._lock:
                for connection in stale:
                    self.connections.discard(connection)
                    self.send_locks.pop(connection, None)


def create_app(
    study_config_path: str | Path = ROOT / "config" / "study.example.json",
    robot_config_path: str | Path = ROOT / "config" / "robot.local.json",
) -> Any:
    try:
        from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise RuntimeError(
            "Web dependencies are missing. Run scripts/setup_ubuntu20.sh or install requirements.txt."
        ) from exc

    # FastAPI resolves postponed annotations against module globals. Dependencies
    # remain optional for pure core tests, but the WebSocket type must be visible.
    globals()["WebSocket"] = WebSocket

    config = load_study_config(study_config_path)
    hub = ConnectionHub()
    manager = SessionManager(config, robot_config_path, hub.broadcast)

    @asynccontextmanager
    async def lifespan(application: Any):
        yield
        machine = manager.machine
        if machine is not None:
            try:
                if machine.state.robot_connected:
                    await machine.executor.close()
                    machine.state.robot_connected = False
            finally:
                machine.logger.close()

    app = FastAPI(
        title="AUBO ES3 HCI Study Console", version="1.0.0", lifespan=lifespan
    )
    app.state.study_config = config
    app.state.session_manager = manager
    app.state.hub = hub
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def authorized(token: str | None) -> bool:
        expected = config.researcher_token
        return not expected or token == expected

    @app.get("/")
    async def index() -> Any:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status(x_researcher_token: str | None = Header(default=None)) -> dict[str, Any]:
        if not authorized(x_researcher_token):
            raise HTTPException(status_code=401, detail="Invalid researcher token")
        return manager.status()

    @app.post("/api/session")
    async def start_session(
        body: dict[str, Any], x_researcher_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if not authorized(x_researcher_token):
            raise HTTPException(status_code=401, detail="Invalid researcher token")
        try:
            state = await manager.start_session(
                str(body.get("participant_id", "")), str(body.get("session_id", "")),
                str(body.get("condition", "")), bool(body.get("formal_study", False)),
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return state

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
        if not authorized(token):
            await websocket.close(code=4401, reason="Invalid researcher token")
            return
        await hub.connect(websocket)
        await hub.send(websocket, {"type": "system.status", "payload": manager.status()})
        tasks: set[asyncio.Task[Any]] = set()

        async def process(raw: Any) -> None:
            request_id = raw.get("request_id") if isinstance(raw, dict) else None
            try:
                command = WozCommand.from_dict(raw)
                state = await manager.handle(command)
                await hub.send(websocket, {
                    "type": "woz.ack", "request_id": command.request_id,
                    "timestamp": _timestamp(), "payload": {"state": state},
                })
            except Exception as exc:
                await hub.send(websocket, {
                    "type": "woz.error", "request_id": request_id,
                    "timestamp": _timestamp(),
                    "payload": {"error_type": type(exc).__name__, "message": str(exc)},
                })

        try:
            while True:
                raw = await websocket.receive_json()
                task = asyncio.create_task(process(raw))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    return app


def _timestamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AUBO ES3 researcher WoZ web console",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Default behavior: omitting both config flags uses study.example.json and "
            "robot.local.json. To use your editable study configuration, pass "
            "--study-config config/study.local.json.\n\n"
            "Recommended mock command:\n"
            "  AUBO_ROBOT_MODE=mock python main.py study "
            "--study-config config/study.local.json"
        ),
    )
    parser.add_argument(
        "--study-config",
        default=str(ROOT / "config" / "study.example.json"),
        metavar="PATH",
        help="study/runtime JSON; the default is the read-only example, not study.local.json",
    )
    parser.add_argument(
        "--robot-config",
        default=str(ROOT / "config" / "robot.local.json"),
        metavar="PATH",
        help="robot credentials and low-level safety JSON",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="web bind address; a researcher token is required outside localhost",
    )
    parser.add_argument("--port", type=int, default=8000, help="web server TCP port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import uvicorn
        config = load_study_config(args.study_config)
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not config.researcher_token:
            raise StudyConfigError("A researcher token is required when binding beyond localhost")
        application = create_app(args.study_config, args.robot_config)
        uvicorn.run(
            application,
            host=args.host,
            port=args.port,
            reload=False,
            log_level="info",
        )
        return 0
    except (RuntimeError, StudyConfigError) as exc:
        print(f"Web console failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
