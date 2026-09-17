"""Hard runtime budget monitor used only inside the isolated analysis worker."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import threading
import time

import psutil

from ..domain.runs import RunStatus
from ..storage.runs import RunStore
from .lease import release_worker
from .protocol import EventKind


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
    """Terminate a worker that exceeds its immutable run budget.

    The worker is a disposable process. Before hard exit we persist FAILED when
    possible; if SQLite itself is unavailable, startup reconciliation converts a
    stranded RUNNING row to FAILED on the next application open.
    """

    def __init__(
        self,
        store: RunStore,
        *,
        workspace: str | Path,
        run_id: str,
        max_duration_seconds: float,
        max_memory_bytes: int,
        poll_seconds: float = 0.2,
        hard_exit: Callable[[int], None] = os._exit,
    ) -> None:
        self.store = store
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
            self._persist_failure(reason, rss)
            release_worker(self.workspace, self.run_id, pid=os.getpid())
            self.hard_exit(3)
            return

    def _persist_failure(self, reason: str, rss_bytes: int) -> None:
        try:
            current = self.store.get_run(self.run_id)
            if current.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                return
            self.store.transition(
                self.run_id,
                status=RunStatus.FAILED,
                error_code=RESOURCE_ERROR_CODE,
            )
            self.store.append_event(
                self.run_id,
                EventKind.FAILED.value,
                stage="watchdog",
                payload={
                    "error_code": RESOURCE_ERROR_CODE,
                    "reason": reason,
                    "rss_bytes": rss_bytes,
                    "max_memory_bytes": self.max_memory_bytes,
                    "max_duration_seconds": self.max_duration_seconds,
                },
            )
        except Exception:
            # The process must still stop. Startup reconciliation will repair a
            # stranded RUNNING row if persistence failed during the incident.
            return
