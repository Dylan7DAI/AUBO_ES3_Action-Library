from __future__ import annotations

import signal
import threading
import time
from pathlib import Path
from typing import Callable, Optional


class EmergencyStopRequested(RuntimeError):
    pass


class EmergencyStopMonitor:
    """Watch a stop file and invoke a controlled SDK stop callback.

    This is a secondary software stop. The physical emergency-stop circuit
    remains the only safety-rated emergency stop.
    """

    def __init__(
        self,
        stop_file: Path,
        *,
        poll_seconds: float = 0.02,
    ) -> None:
        self.stop_file = Path(stop_file)
        self.poll_seconds = max(0.01, float(poll_seconds))
        self._requested = threading.Event()
        self._closed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._callback: Optional[Callable[[], None]] = None
        self._callback_error: Optional[BaseException] = None
        self._previous_handlers: dict[int, object] = {}

    @property
    def requested(self) -> bool:
        return self._requested.is_set() or self.stop_file.exists()

    @property
    def callback_error(self) -> Optional[BaseException]:
        return self._callback_error

    def assert_clear(self) -> None:
        if self.stop_file.exists():
            raise EmergencyStopRequested(
                f"急停锁存文件仍存在：{self.stop_file}。"
                "确认现场安全后使用专用命令清除，程序不会自动清除。"
            )

    def request(self, reason: str = "software emergency stop") -> None:
        self.stop_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.stop_file.write_text(
                f"{reason}\n",
                encoding="utf-8",
            )
        finally:
            self._requested.set()

    def check(self) -> None:
        if self.requested:
            detail = (
                f"；SDK停止回调异常：{self._callback_error}"
                if self._callback_error is not None
                else ""
            )
            raise EmergencyStopRequested(
                "检测到软件急停请求，禁止继续运动" + detail
            )

    def _watch(self) -> None:
        callback_called = False
        while not self._closed.is_set():
            if self.requested and not callback_called:
                callback_called = True
                self._requested.set()
                if self._callback is not None:
                    try:
                        self._callback()
                    except BaseException as exc:  # preserve stop intent
                        self._callback_error = exc
            time.sleep(self.poll_seconds)

    def _signal_handler(self, signum: int, _frame: object) -> None:
        self.request(f"signal {signum}")

    def start(
        self,
        stop_callback: Callable[[], None],
        *,
        install_signal_handlers: bool = True,
    ) -> "EmergencyStopMonitor":
        self.assert_clear()
        if self._thread is not None:
            raise RuntimeError("急停监视器已经启动")
        self._callback = stop_callback
        self._thread = threading.Thread(
            target=self._watch,
            name="aubo-emergency-stop-monitor",
            daemon=True,
        )
        self._thread.start()

        if install_signal_handlers and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                self._previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._signal_handler)
        return self

    def close(self) -> None:
        self._closed.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.2, self.poll_seconds * 4))
            self._thread = None
        if threading.current_thread() is threading.main_thread():
            for signum, handler in self._previous_handlers.items():
                signal.signal(signum, handler)
        self._previous_handlers.clear()

    def __enter__(self) -> "EmergencyStopMonitor":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

