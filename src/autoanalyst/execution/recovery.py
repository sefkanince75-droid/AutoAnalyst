"""Startup reconciliation for analysis runs whose worker disappeared unexpectedly."""

from __future__ import annotations

from pathlib import Path

from ..domain.runs import RunStatus
from ..storage.runs import RunStore
from .lease import clear_stale_lease, owned_process
from .protocol import EventKind


def reconcile_interrupted_runs(store: RunStore, workspace: str | Path) -> tuple[str, ...]:
    """Fail orphaned RUNNING rows while leaving a provably live owned worker alone."""
    workspace_path = Path(workspace).resolve()
    lease = clear_stale_lease(workspace_path)
    live_run_id = None
    if lease is not None and owned_process(workspace_path, lease.run_id) is not None:
        live_run_id = lease.run_id

    with store.catalog.connection() as connection:
        rows = connection.execute(
            "SELECT run_id FROM analysis_runs WHERE status = ? ORDER BY created_at, run_id",
            (RunStatus.RUNNING.value,),
        ).fetchall()
    recovered: list[str] = []
    for row in rows:
        run_id = str(row["run_id"])
        if run_id == live_run_id:
            continue
        current = store.get_run(run_id)
        if current.status is not RunStatus.RUNNING:
            continue
        store.transition(run_id, status=RunStatus.FAILED, error_code="unexpected_execution")
        store.append_event(
            run_id,
            EventKind.FAILED.value,
            stage="recovery",
            payload={"error_code": "unexpected_execution", "reason": "worker_interrupted"},
        )
        recovered.append(run_id)
    return tuple(recovered)
