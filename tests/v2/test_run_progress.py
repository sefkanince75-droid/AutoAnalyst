from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import pytest

from autoanalyst.domain.codec import canonical_json, utc_now
from autoanalyst.domain.errors import ResourceError, SchemaError
from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.domain.runs import RunKind, RunStatus
from autoanalyst.execution.lease import activate_worker, read_lease, release_worker, reserve_worker
from autoanalyst.execution.progress import read_worker_progress
from autoanalyst.execution.worker_protocol import progress_manifest_path, write_progress_manifest
from autoanalyst.ui.app import _spec


def _run(ws, key):
    from autoanalyst.bootstrap import build_runtime

    runtime = build_runtime(ws.catalog.paths.root)
    spec = _spec(
        ws.project.project_id,
        ws.imported.version.version_id,
        AnalysisModuleId.PROFILING,
        "profile",
        {},
        (),
    )
    return runtime, runtime.coordinator.create_run(spec, request_key=key)


def test_progress_snapshot_keeps_only_known_model_metadata(tmp_path):
    run_id = str(uuid4())
    assert read_worker_progress(tmp_path, run_id) is None
    assert not (tmp_path / "staging").exists()
    write_progress_manifest(
        tmp_path,
        run_id,
        stage="binary.fit",
        fraction=0.5,
        payload={"model_id": "random_forest", "secret": "private-content"},
    )
    progress = read_worker_progress(tmp_path, run_id)
    assert progress.stage == "binary.fit" and progress.fraction == 0.5
    assert progress.model_id == "random_forest" and "private" not in repr(progress)


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": str(uuid4())},
        {"manifest_version": "unknown"},
        {"fraction": True},
        {"fraction": -1},
        {"fraction": 1.1},
        {"fraction": "0.5"},
        {"stage": ""},
        {"stage": 5},
        {"stage": "a" * 81},
        {"updated_at": "yesterday"},
        {"updated_at": None},
    ],
)
def test_invalid_progress_is_ignored(tmp_path, change):
    run_id = str(uuid4())
    payload = {
        "run_id": run_id,
        "manifest_version": "1",
        "stage": "analysis",
        "fraction": 0.5,
        "updated_at": utc_now(),
        **change,
    }
    progress_manifest_path(tmp_path, run_id).write_text(canonical_json(payload), encoding="utf-8")
    assert read_worker_progress(tmp_path, run_id) is None


@pytest.mark.parametrize(
    "raw",
    [b"broken-json", b"[]", b"\xff", b"x" * 65537],
    ids=["broken", "list", "encoding", "oversized"],
)
def test_unreadable_or_oversized_progress_is_ignored(tmp_path, raw):
    run_id = str(uuid4())
    progress_manifest_path(tmp_path, run_id).write_bytes(raw)
    assert read_worker_progress(tmp_path, run_id) is None


def test_polling_persists_progress_once_and_bounds_events(phase3_workspace):
    ws = phase3_workspace
    runtime, run = _run(ws, "progress-poll")
    runtime.run_store.transition(run.run_id, status=RunStatus.RUNNING)
    reserve_worker(ws.catalog.paths.root, run.run_id)
    activate_worker(ws.catalog.paths.root, run.run_id, os.getpid())
    try:
        write_progress_manifest(
            ws.catalog.paths.root,
            run.run_id,
            stage="binary.fit",
            fraction=0.5,
            payload={"model_id": "dummy", "secret": "private-content"},
        )
        runtime.coordinator.reconcile_worker(run.run_id, ws.catalog.paths.root)
        runtime.coordinator.reconcile_worker(run.run_id, ws.catalog.paths.root)
        events = runtime.run_store.list_events(run.run_id)
        assert len(events) == 1 and events[0]["payload"] == {"fraction": 0.5, "model_id": "dummy"}
        write_progress_manifest(
            ws.catalog.paths.root, run.run_id, stage="binary.fit", fraction=0.75
        )
        runtime.coordinator.reconcile_worker(run.run_id, ws.catalog.paths.root)
        assert len(runtime.run_store.list_events(run.run_id)) == 2
        assert runtime.run_store.list_events(run.run_id, limit=1)[0]["payload"]["fraction"] == 0.75
        for invalid in (0, 501, True, 1.5):
            with pytest.raises(SchemaError):
                runtime.run_store.list_events(run.run_id, limit=invalid)
    finally:
        release_worker(ws.catalog.paths.root, run.run_id)


def test_active_runs_are_workspace_wide_and_exclude_terminal_and_nonanalysis(phase3_workspace):
    runtime, pending = _run(phase3_workspace, "active-pending")
    _, cancelled = _run(phase3_workspace, "active-cancelled")
    runtime.run_store.transition(cancelled.run_id, status=RunStatus.CANCELLED)
    runtime.run_store.insert_run(
        replace(pending, run_id=str(uuid4()), kind=RunKind.REPORT, request_key="report-pending")
    )
    assert [run.run_id for run in runtime.run_store.active_analysis_runs()] == [pending.run_id]


def test_busy_worker_start_does_not_leave_an_orphaned_pending_run(phase3_workspace):
    ws = phase3_workspace
    runtime, first = _run(ws, "lease-first")
    reserve_worker(ws.catalog.paths.root, first.run_id)
    _, second = _run(ws, "lease-second")
    try:
        with pytest.raises(ResourceError):
            runtime.coordinator.start_worker_process(second.run_id, ws.catalog.paths.root)
        assert runtime.run_store.get_run(second.run_id).status is RunStatus.FAILED
        assert read_lease(ws.catalog.paths.root).run_id == first.run_id
    finally:
        release_worker(ws.catalog.paths.root, first.run_id)


def test_two_sessions_publish_one_result_and_one_completion_event(phase3_workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier

    from autoanalyst.analyses.contract import ResultDraft
    from autoanalyst.domain.results import ResultOutcome
    from autoanalyst.execution.worker_protocol import write_success_manifest

    ws = phase3_workspace
    runtime, run = _run(ws, "concurrent-publication")
    runtime.run_store.transition(run.run_id, status=RunStatus.RUNNING)
    write_success_manifest(
        ws.catalog.paths.root, run.run_id, ResultDraft(outcome=ResultOutcome.SUCCEEDED)
    )
    original = runtime.catalog.transaction
    barrier = Barrier(2)

    @contextmanager
    def synchronized_transaction():
        barrier.wait(timeout=10)
        with original() as connection:
            yield connection

    monkeypatch.setattr(runtime.catalog, "transaction", synchronized_transaction)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(runtime.coordinator.reconcile_worker, run.run_id, ws.catalog.paths.root)
            for _ in range(2)
        ]
        states = [future.result(timeout=20) for future in futures]
    assert all(state.status is RunStatus.COMPLETED for state in states)
    assert states[0].result_id == states[1].result_id
    assert [event["event_type"] for event in runtime.run_store.list_events(run.run_id)] == [
        "completed"
    ]


@pytest.mark.parametrize("status", [RunStatus.FAILED, RunStatus.CANCELLED])
def test_two_sessions_handle_terminal_transition_race(phase3_workspace, monkeypatch, status):
    import autoanalyst.execution.recovery as recovery

    runtime, run = _run(phase3_workspace, "terminal-race")
    original = runtime.run_store.transition

    def competing_transition(run_id, **kwargs):
        original(
            run_id,
            status=status,
            error_code="unexpected_execution" if status is RunStatus.FAILED else None,
        )
        return original(run_id, **kwargs)

    monkeypatch.setattr(runtime.run_store, "transition", competing_transition)
    assert recovery._finish_terminal(runtime.run_store, run.run_id, status) is False
    assert runtime.run_store.get_run(run.run_id).status is status
