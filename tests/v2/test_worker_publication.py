from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from autoanalyst.analyses.contract import ResultDraft
from autoanalyst.domain.codec import fingerprint, from_json, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.results import ResultOutcome
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.execution.recovery import reconcile_worker_run
from autoanalyst.execution.worker import run_worker
from autoanalyst.execution.worker_protocol import (
    draft_from_manifest,
    progress_manifest_path,
    read_result_manifest,
    result_manifest_path,
    write_cancelled_manifest,
    write_failure_manifest,
    write_progress_manifest,
    write_success_manifest,
)
from autoanalyst.storage.read_only import ReadOnlySQLiteCatalog
from autoanalyst.storage.runs import RunStore


def _profiling_spec(workspace) -> AnalysisSpec:
    version_id = workspace.imported.version.version_id
    payload = {"module": "profiling", "operation": "profile", "version": version_id}
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=workspace.project.project_id,
        module_id=AnalysisModuleId.PROFILING,
        module_version="2.0",
        operation="profile",
        input_version_id=version_id,
        column_roles=(),
        parameters={},
        seed=42,
        resource_budget=ResourceBudget(2_000_000_000, 2_000_000_000, 30, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _running_profiling_run(workspace, request_key: str):
    store = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(store)
    run = coordinator.create_run(_profiling_spec(workspace), request_key=request_key)
    running = store.transition(run.run_id, status=RunStatus.RUNNING)
    return store, coordinator, running


def test_read_only_catalog_allows_reads_and_rejects_writes(phase3_workspace) -> None:
    workspace = phase3_workspace
    catalog = ReadOnlySQLiteCatalog(workspace.catalog.paths.root)

    with catalog.connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("UPDATE projects SET name = 'mutated'")

    with pytest.raises(RuntimeError, match="read-only worker catalog"):
        with catalog.transaction():
            pass


def test_worker_executes_read_only_then_app_reconciles_success(phase3_workspace) -> None:
    workspace = phase3_workspace
    store, coordinator, running = _running_profiling_run(workspace, "worker-success")

    with workspace.catalog.connection() as connection:
        before_results = connection.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0]

    exit_code = run_worker(workspace.catalog.paths.root, running.run_id)

    assert exit_code == 0
    still_running = store.get_run(running.run_id)
    assert still_running.status is RunStatus.RUNNING
    assert still_running.result_id is None
    with workspace.catalog.connection() as connection:
        after_worker_results = connection.execute(
            "SELECT COUNT(*) FROM analysis_results"
        ).fetchone()[0]
    assert after_worker_results == before_results

    manifest = read_result_manifest(workspace.catalog.paths.root, running.run_id)
    assert manifest is not None
    assert manifest["status"] == "succeeded"

    reconciled = coordinator.reconcile_worker(running.run_id, workspace.catalog.paths.root)
    assert reconciled.status is RunStatus.COMPLETED
    assert reconciled.result_id is not None
    assert store.result_for_run(running.run_id) is not None


def test_worker_protocol_round_trips_success_and_progress(tmp_path) -> None:
    run_id = str(uuid4())
    draft = ResultDraft(
        outcome=ResultOutcome.SUCCEEDED,
        methodology={"module": "profiling", "module_version": "2.0"},
        sample_summary={"rows": 3},
        provenance={"input_version_id": str(uuid4())},
    )

    write_progress_manifest(
        tmp_path,
        run_id,
        stage="profile",
        fraction=0.5,
        payload={"completed_units": 1, "total_units": 2},
    )
    progress = from_json(progress_manifest_path(tmp_path, run_id).read_text(encoding="utf-8"))
    assert progress["run_id"] == run_id
    assert progress["stage"] == "profile"
    assert progress["fraction"] == 0.5

    write_success_manifest(tmp_path, run_id, draft)
    manifest = read_result_manifest(tmp_path, run_id)
    assert manifest is not None
    rebuilt = draft_from_manifest(manifest)
    assert rebuilt.outcome is ResultOutcome.SUCCEEDED
    assert rebuilt.methodology["module"] == "profiling"
    assert rebuilt.sample_summary["rows"] == 3


@pytest.mark.parametrize(
    ("status", "writer", "expected_status", "expected_error"),
    (
        ("cancelled", write_cancelled_manifest, RunStatus.CANCELLED, None),
        ("failed", write_failure_manifest, RunStatus.FAILED, "resource_error"),
    ),
)
def test_recovery_publishes_worker_terminal_manifests(
    phase3_workspace,
    status,
    writer,
    expected_status,
    expected_error,
) -> None:
    workspace = phase3_workspace
    store, _, running = _running_profiling_run(workspace, f"worker-{status}")

    if status == "cancelled":
        writer(
            workspace.catalog.paths.root,
            running.run_id,
            context={"reason": "execution_cancelled"},
        )
    else:
        writer(
            workspace.catalog.paths.root,
            running.run_id,
            error_code="resource_error",
            context={"reason": "memory_budget_exceeded"},
        )

    assert reconcile_worker_run(store, workspace.catalog.paths.root, running.run_id)
    terminal = store.get_run(running.run_id)
    assert terminal.status is expected_status
    assert terminal.error_code == expected_error


def test_recovery_rejects_corrupt_worker_manifest(phase3_workspace) -> None:
    workspace = phase3_workspace
    store, _, running = _running_profiling_run(workspace, "worker-corrupt")
    result_manifest_path(workspace.catalog.paths.root, running.run_id).write_text(
        "{not-json",
        encoding="utf-8",
    )

    assert reconcile_worker_run(store, workspace.catalog.paths.root, running.run_id)
    failed = store.get_run(running.run_id)
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "unexpected_execution"
    event = store.list_events(running.run_id)[-1]
    assert event["payload"]["reason"] == "worker_manifest_corrupt"


def test_worker_protocol_rejects_identity_mismatch(tmp_path) -> None:
    run_id = str(uuid4())
    path = result_manifest_path(tmp_path, run_id)
    path.write_text(
        '{"manifest_version":"1","run_id":"wrong","status":"failed"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity mismatch"):
        read_result_manifest(tmp_path, run_id)
