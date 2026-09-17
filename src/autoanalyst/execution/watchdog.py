"""Hard runtime budget monitor used only inside the isolated analysis worker."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import psutil

from .lease import release_worker
from .worker_protocol import write_failure_manifest

RESOURCE_ERROR_CODE = "resource_error"


def budget_violation(
    *,
    elapsed_seconds: float,
    rss_bytes: int,
    max_duration_seconds: float,
    max_memory_bytes: int,
) -> str | None:
    if elapsed_seconds > max_duration_seconds:
        return "runtime_timeout"
    if rss_bytes > max_memory_bytes:
        return "memory_budget_exceeded"
    return None


class WorkerBudgetWatchdog:
    """Terminate a disposable worker without mutating SQLite metadata.

    On a hard budget breach the worker writes a durable staging manifest first.
    The app-owned coordinator later reconciles that manifest into catalog state.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        run_id: str,
        max_duration_seconds: float,
        max_memory_bytes: int,
        poll_seconds: float = 0.2,
        hard_exit: Callable[[int], None] = os._exit,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.run_id = run_id
        self.max_duration_seconds = float(max_duration_seconds)
        self.max_memory_bytes = int(max_memory_bytes)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.hard_exit = hard_exit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("watchdog already started")
        self._thread = threading.Thread(
            target=self._monitor,
            name=f"autoanalyst-watchdog-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(0.25, self.poll_seconds * 2))

    def _monitor(self) -> None:
        started = time.monotonic()
        process = psutil.Process(os.getpid())
        while not self._stop.wait(self.poll_seconds):
            try:
                rss = int(process.memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return
            reason = budget_violation(
                elapsed_seconds=time.monotonic() - started,
                rss_bytes=rss,
                max_duration_seconds=self.max_duration_seconds,
                max_memory_bytes=self.max_memory_bytes,
            )
            if reason is None:
                continue
            try:
                write_failure_manifest(
                    self.workspace,
                    self.run_id,
                    error_code=RESOURCE_ERROR_CODE,
                    context={
                        "reason": reason,
                        "rss_bytes": rss,
                        "max_memory_bytes": self.max_memory_bytes,
                        "max_duration_seconds": self.max_duration_seconds,
                    },
                )
            finally:
                release_worker(self.workspace, self.run_id, pid=os.getpid())
                self.hard_exit(3)
            return
