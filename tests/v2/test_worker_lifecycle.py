from __future__ import annotations

import os
from uuid import uuid4

import pytest

from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.errors import ResourceError
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.execution.lease import (
    activate_worker,
    owned_process,
    read_lease,
    release_worker,
    reserve_worker,
)
from autoanalyst.execution.recovery import reconcile_interrupted_runs
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
        resource_budget=ResourceBudget(1_000_000, 1_000_000, 30, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def test_worker_lease_is_single_owner_and_pid_reuse_safe(tmp_path) -> None:
    run_id = str(uuid4())
    reserve_worker(tmp_path, run_id)
    with pytest.raises(ResourceError) as busy:
        reserve_worker(tmp_path, str(uuid4()))
    assert busy.value.context["reason"] == "heavy_worker_starting"

    lease = activate_worker(tmp_path, run_id, os.getpid())
    assert lease.pid == os.getpid()
    assert owned_process(tmp_path, run_id) is not None
    assert read_lease(tmp_path) == lease
    assert release_worker(tmp_path, run_id, pid=os.getpid())
    assert read_lease(tmp_path) is None


def test_cancel_pending_run_reaches_terminal_state_without_worker(phase3_workspace) -> None:
    workspace = phase3_workspace
    store = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(store)
    run = coordinator.create_run(_profiling_spec(workspace), request_key="pending-cancel")

    coordinator.request_cancel(run.run_id, workspace.catalog.paths.root, cooperative_grace_seconds=0)

    cancelled = store.get_run(run.run_id)
    assert cancelled.status is RunStatus.CANCELLED
    event_types = [event["event_type"] for event in store.list_events(run.run_id)]
    assert event_types == ["cancel_requested", "cancelled"]


def test_startup_reconciliation_fails_orphaned_running_job(phase3_workspace) -> None:
    workspace = phase3_workspace
    store = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(store)
    run = coordinator.create_run(_profiling_spec(workspace), request_key="orphaned")
    store.transition(run.run_id, status=RunStatus.RUNNING)

    recovered = reconcile_interrupted_runs(store, workspace.catalog.paths.root)

    assert recovered == (run.run_id,)
    failed = store.get_run(run.run_id)
    assert failed.status is RunStatus.FAILED
    assert failed.error_code == "unexpected_execution"
    events = store.list_events(run.run_id)
    assert events[-1]["stage"] == "recovery"
    assert events[-1]["payload"]["reason"] == "worker_interrupted"
