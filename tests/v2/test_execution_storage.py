from __future__ import annotations

from uuid import uuid4

from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import AnalysisRun, RunKind, RunStatus
from autoanalyst.storage.runs import RunStore


def _spec(ws):
    version = ws.imported.version
    payload = {
        "project_id": ws.project.project_id,
        "input_version_id": version.version_id,
        "module_id": AnalysisModuleId.PROFILING.value,
        "operation": "profile",
        "seed": 42,
    }
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=ws.project.project_id,
        module_id=AnalysisModuleId.PROFILING,
        module_version="1.0",
        operation="profile",
        input_version_id=version.version_id,
        column_roles=(),
        parameters={},
        seed=42,
        resource_budget=ResourceBudget(100_000_000, 100_000_000, 30, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def test_run_store_roundtrip_and_idempotent_request_key(phase3_workspace):
    store = RunStore(phase3_workspace.catalog)
    spec = store.insert_spec(_spec(phase3_workspace))
    run = AnalysisRun(
        run_id=str(uuid4()),
        project_id=phase3_workspace.project.project_id,
        kind=RunKind.ANALYSIS,
        request_key="profile-1",
        input_fingerprint=fingerprint({"version": spec.input_version_id, "spec": spec.spec_hash}),
        status=RunStatus.PENDING,
        created_at=utc_now(),
        spec_id=spec.spec_id,
    )
    first = store.insert_run(run)
    duplicate = AnalysisRun(
        run_id=str(uuid4()),
        project_id=run.project_id,
        kind=RunKind.ANALYSIS,
        request_key=run.request_key,
        input_fingerprint=run.input_fingerprint,
        status=RunStatus.PENDING,
        created_at=utc_now(),
        spec_id=spec.spec_id,
    )
    second = store.insert_run(duplicate)
    assert second.run_id == first.run_id
    assert store.get_spec(spec.spec_id) == spec
    assert store.get_run(first.run_id) == first


def test_run_state_machine_and_events(phase3_workspace):
    store = RunStore(phase3_workspace.catalog)
    spec = store.insert_spec(_spec(phase3_workspace))
    run = store.insert_run(
        AnalysisRun(
            run_id=str(uuid4()),
            project_id=spec.project_id,
            kind=RunKind.ANALYSIS,
            request_key="profile-2",
            input_fingerprint=fingerprint({"spec": spec.spec_hash}),
            status=RunStatus.PENDING,
            created_at=utc_now(),
            spec_id=spec.spec_id,
        )
    )
    running = store.transition(run.run_id, status=RunStatus.RUNNING)
    assert running.status is RunStatus.RUNNING
    assert running.started_at is not None
    assert store.append_event(run.run_id, "progress", stage="load", payload={"fraction": 0.5}) == 0
    assert (
        store.append_event(run.run_id, "progress", stage="compute", payload={"fraction": 1.0}) == 1
    )
    assert [item["sequence"] for item in store.list_events(run.run_id)] == [0, 1]
