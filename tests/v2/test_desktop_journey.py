from __future__ import annotations

from uuid import uuid4

from autoanalyst.bootstrap import build_runtime
from autoanalyst.desktop import _streamlit_entrypoint
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.lease import read_lease
from autoanalyst.smoke import verify_workspace_journey


def _profiling_spec(workspace):
    version_id = workspace.imported.version.version_id
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
        resource_budget=ResourceBudget(2_000_000_000, 2_000_000_000, 60, 1),
        spec_hash=fingerprint({"version": version_id, "operation": "profile"}),
        created_at=utc_now(),
    )


def test_real_worker_import_report_and_reopen_journey(tmp_path):
    verify_workspace_journey(tmp_path / "workspace")


def test_packaged_ui_entrypoint_does_not_depend_on_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    entrypoint = _streamlit_entrypoint()
    assert entrypoint.is_file()
    assert entrypoint.name == "entrypoint.py"
    assert entrypoint.parent.name == "ui"


def test_real_worker_can_be_cancelled_without_changing_dataset(phase3_workspace):
    ws = phase3_workspace
    runtime = build_runtime(ws.catalog.paths.root)
    spec = _profiling_spec(ws)
    run = runtime.coordinator.create_run(spec, request_key="real-cancel")
    process = runtime.coordinator.start_worker_process(run.run_id, ws.catalog.paths.root)
    try:
        runtime.coordinator.request_cancel(
            run.run_id, ws.catalog.paths.root, cooperative_grace_seconds=0
        )
        process.wait(timeout=10)
        assert runtime.run_store.get_run(run.run_id).status is RunStatus.CANCELLED
        assert read_lease(ws.catalog.paths.root) is None
        assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_restart_recovers_an_abruptly_stopped_real_worker(phase3_workspace):
    ws = phase3_workspace
    runtime = build_runtime(ws.catalog.paths.root)
    spec = _profiling_spec(ws)
    run = runtime.coordinator.create_run(spec, request_key="real-interruption")
    process = runtime.coordinator.start_worker_process(run.run_id, ws.catalog.paths.root)
    try:
        process.kill()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    reopened = build_runtime(ws.catalog.paths.root)
    recovered = reopened.run_store.get_run(run.run_id)
    assert recovered.status is RunStatus.FAILED
    assert recovered.error_code == "unexpected_execution"
    assert read_lease(ws.catalog.paths.root) is None
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
