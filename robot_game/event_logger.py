"""追加写入的哈希链实验日志，以及便于现场排查的文本运行日志。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable


_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_id(value: str) -> str:
    sanitized = _SAFE_ID.sub("_", value.strip())[:96]
    if not sanitized or sanitized in {".", ".."}:
        raise ValueError("identifier contains no safe characters")
    return sanitized


class EventLogger:
    """写入可检测篡改的事件链，同时生成便于阅读的诊断日志。"""

    def __init__(
        self,
        root: Path,
        session_id: str,
        context: dict[str, Any],
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.session_id = safe_id(session_id)
        self.directory = root / self.session_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events_path = self.directory / "events.jsonl"
        self.runtime_path = self.directory / "runtime.log"
        self.summary_path = self.directory / "session_summary.json"
        self._lock = threading.Lock()
        self._sequence = 0
        self._previous_hash = "0" * 64
        self.context = dict(context)
        self.event_sink = event_sink

        self.runtime = logging.getLogger(f"robot_game.{self.session_id}.{id(self)}")
        self.runtime.setLevel(logging.DEBUG)
        self.runtime.propagate = False
        handler = RotatingFileHandler(
            self.runtime_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)sZ %(levelname)-8s %(message)s", "%Y-%m-%dT%H:%M:%S")
        )
        handler.formatter.converter = time.gmtime
        self.runtime.addHandler(handler)
        self.event("session", "logger_initialized", data={"paths": {
            "events": str(self.events_path), "runtime": str(self.runtime_path)
        }})

    def event(
        self,
        category: str,
        name: str,
        *,
        data: dict[str, Any] | None = None,
        level: str = "INFO",
        round_id: int | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        # 多个 asyncio task 和工作线程可能同时产生日志，必须串行更新 sequence/hash。
        with self._lock:
            self._sequence += 1
            record: dict[str, Any] = {
                "schema_version": "1.0",
                "sequence": self._sequence,
                "timestamp_utc": utc_now(),
                "monotonic_ns": time.monotonic_ns(),
                "level": level,
                "category": category,
                "event": name,
                **self.context,
                "round_id": round_id,
                "request_id": request_id,
                "data": data or {},
                "previous_hash": self._previous_hash,
            }
            # 当前记录包含前一条 hash；修改、插入或重排中间记录都会破坏后续链条。
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            record["record_hash"] = digest
            line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                # 每条事件立即落盘，降低断电或进程崩溃时丢失实验记录的范围。
                os.fsync(stream.fileno())
            self._previous_hash = digest
            log_method = getattr(self.runtime, level.lower(), self.runtime.info)
            log_method("[%s] %s %s", category, name, json.dumps(data or {}, ensure_ascii=False))
            if self.event_sink is not None:
                try:
                    self.event_sink(record)
                except Exception as exc:
                    self.runtime.error("Event sink failed: %s", exc)
            return record

    def safety_check(
        self,
        check: str,
        passed: bool,
        *,
        requested: Any,
        limits: Any,
        validated: Any = None,
        round_id: int | None = None,
    ) -> None:
        self.event(
            "safety",
            check,
            level="INFO" if passed else "ERROR",
            round_id=round_id,
            data={
                "passed": passed,
                "requested": requested,
                "configured_limits": limits,
                "validated": validated,
            },
        )

    def write_summary(self, summary: dict[str, Any]) -> None:
        temporary = self.summary_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self.summary_path)
        self.event("session", "summary_written", data={"path": str(self.summary_path)})

    def close(self) -> None:
        for handler in tuple(self.runtime.handlers):
            handler.flush()
            handler.close()
            self.runtime.removeHandler(handler)
