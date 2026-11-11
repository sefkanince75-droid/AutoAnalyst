from __future__ import annotations

import time
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pandas as pd
import psutil
import pytest

from autoanalyst.application.snapshots import WorkspaceSnapshotService
from autoanalyst.data import CSVIngestor
from autoanalyst.data.ingest import _canonical_scalar, _content_fingerprint, _read_source
from autoanalyst.data.limits import enforce_shape, enforce_xlsx_archive
from autoanalyst.domain.codec import fingerprint
from autoanalyst.domain.errors import ResourceError
from autoanalyst.execution.data_tasks import (
    DataBudget,
    DataTaskService,
    atomic_json,
    read_json,
    task_directory,
)
from autoanalyst.execution.data_worker import violation
from autoanalyst.storage.activity import write_activity


def wait_task(service, identifier, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = service.poll(identifier)
        if state["status"] != "running":
            return state
        time.sleep(0.05)
    service.cancel(identifier)
    raise AssertionError("data task did not stop")


def start_csv(ws, service, **kwargs):
    return service.start(
        operation="csv",
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        source=b"num,text\n1,a\n2,b\n",
        **kwargs,
    )


def test_real_data_worker_import_reopens_and_catalog_commit_wins(phase3_workspace):
    ws = phase3_workspace
    service = DataTaskService(ws.catalog)
    identifier = start_csv(ws, service)
    state = wait_task(service, identifier)
    assert state["status"] == "completed", state
    assert state["peak_rss_bytes"] > 0 and state["elapsed_seconds"] > 0
    version = ws.datasets.current_version(ws.dataset.dataset_id)
    assert version.version_id == state["result_id"] and version.created_by_run_id == identifier
    assert version.row_count == 2
    # Interruption after SQLite commit but before outcome publication must not lie
    # about cancellation or create another version on reconnect.
    directory = task_directory(service.root, identifier)
    atomic_json(directory / "outcome.json", {"status": "failed", "reason": "data_task_memory"})
    service.cancel(identifier)
    assert DataTaskService(ws.catalog).poll(identifier)["status"] == "completed"
    assert service.latest() == identifier


def test_real_data_worker_preparation_is_not_applied_implicitly(phase3_workspace):
    ws = phase3_workspace
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.imported.version.version_id,
        steps=[
            {
                "operation": "drop_missing_rows",
                "parameters": {"column_ids": [ws.column("num")["column_id"]]},
            }
        ],
    )
    service = DataTaskService(ws.catalog)
    identifier = service.start(
        operation="preparation",
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        recipe_id=recipe.recipe_id,
    )
    state = wait_task(service, identifier)
    assert state["status"] == "completed", state
    assert state["result_id"] == identifier
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    assert ws.preparation.get_preview(identifier).after_row_count == 4
    assert ws.preparation.apply_preview(identifier).row_count == 4


@pytest.mark.parametrize("kind", ["cancel", "memory", "time", "crash"])
def test_interrupted_data_task_preserves_original_head(phase3_workspace, kind):
    ws = phase3_workspace
    service = DataTaskService(ws.catalog)
    budget = (
        DataBudget(memory_bytes=1)
        if kind == "memory"
        else DataBudget(seconds=0.001)
        if kind == "time"
        else DataBudget()
    )
    identifier = start_csv(ws, service, budget=budget)
    if kind == "cancel":
        service.cancel(identifier)
    if kind == "crash":
        directory = task_directory(service.root, identifier)
        for _ in range(200):
            if (directory / "worker.json").exists():
                break
            time.sleep(0.01)
        identity = read_json(directory / "worker.json") or read_json(directory / "identity.json")
        process = psutil.Process(identity["pid"])
        assert abs(process.create_time() - identity["created"]) < 0.001
        process.terminate()
        process.wait(timeout=10)
    state = wait_task(service, identifier)
    assert state["status"] == ("cancelled" if kind == "cancel" else "failed"), state
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    if kind in {"memory", "time"}:
        assert state["reason"] == "data_task_" + ("memory" if kind == "memory" else "timeout")


def test_active_task_blocks_conflicting_writers_backups_and_archive(phase3_workspace, tmp_path):
    ws = phase3_workspace
    service = DataTaskService(ws.catalog)
    identifier = start_csv(ws, service)
    try:
        with pytest.raises(ResourceError):
            start_csv(ws, service)
        with pytest.raises(ResourceError):
            with write_activity(service.root):
                pytest.fail("competing writer entered")
        with pytest.raises(ResourceError):
            WorkspaceSnapshotService(ws.catalog, ws.store).export_snapshot(
                tmp_path.parent / f"{uuid4()}.aasnapshot"
            )
        with pytest.raises(Exception) as error:
            ws.projects.set_archived(
                ws.project.project_id, True, expected_revision=ws.project.revision
            )
        assert error.value.context["reason"] == "project_has_active_runs"
    finally:
        service.cancel(identifier)
        wait_task(service, identifier)


def test_disk_estimate_rejected_before_source_is_staged(phase3_workspace):
    ws = phase3_workspace
    with pytest.raises(ResourceError):
        start_csv(ws, DataTaskService(ws.catalog), budget=DataBudget(disk_bytes=1))
    assert not (ws.catalog.paths.staging / "data-tasks").exists()


@pytest.mark.parametrize("rows,columns", [(1_000_001, 1), (1, 501), (100_001, 50)])
def test_shape_limits_are_explicit(rows, columns):
    with pytest.raises(ResourceError):
        enforce_shape(rows, columns)


def test_csv_shape_is_checked_before_pandas(phase3_workspace, monkeypatch):
    import autoanalyst.data.ingest as module
    import autoanalyst.data.limits as limits

    ws = phase3_workspace
    monkeypatch.setattr(limits, "MAX_ROWS", 1)
    monkeypatch.setattr(
        module, "_read_dataframe", lambda *a, **k: pytest.fail("pandas read oversized input")
    )
    with pytest.raises(ResourceError):
        CSVIngestor(ws.catalog, ws.store).import_csv(
            project_id=ws.project.project_id, dataset_id=ws.dataset.dataset_id, source=b"x\n1\n2\n"
        )
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version


def test_path_source_is_size_checked_before_allocation(tmp_path, monkeypatch):
    import autoanalyst.data.limits as limits

    monkeypatch.setattr(limits, "CSV_MAX_BYTES", 1)
    path = tmp_path / "large.csv"
    path.write_bytes(b"value\n123\n")
    with pytest.raises(ResourceError):
        _read_source(path, source_format="csv")


def test_xlsx_expansion_limit_precedes_excel_parser(monkeypatch):
    import autoanalyst.data.limits as limits

    monkeypatch.setattr(limits, "XLSX_UNPACKED_MAX_BYTES", 10)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("large.xml", "x" * 100)
    with pytest.raises(ResourceError):
        enforce_xlsx_archive(data.getvalue())


def test_streamed_fingerprint_is_backward_compatible():
    frame = pd.DataFrame({"Türkçe": ["a,b", None, 'quote"\n'], "value": [1, float("inf"), -2]})
    headers = tuple(frame.columns)
    contract = {"format": "csv", "delimiter": ",", "encoding": "utf-8", "header": True}
    old_descriptor = {
        "parse_contract": contract,
        "columns": [
            {"display_name": name, "physical_type": str(frame.dtypes.iloc[i]), "ordinal": i}
            for i, name in enumerate(headers)
        ],
        "rows": [
            [_canonical_scalar(v) for v in row] for row in frame.itertuples(index=False, name=None)
        ],
    }
    assert _content_fingerprint(frame, headers, contract) == fingerprint(old_descriptor)


@pytest.mark.parametrize(
    "changed,expected",
    [
        ({"elapsed": 121}, "data_task_timeout"),
        ({"rss": 1537 * 1024**2}, "data_task_memory"),
        ({"growth": 1025 * 1024**2}, "data_task_disk_budget"),
        ({"free": 0}, "data_disk_space"),
        ({}, None),
    ],
)
def test_watchdog_resource_decisions(changed, expected):
    values = {"elapsed": 1, "rss": 1, "growth": 0, "free": 1024**3}
    values.update(changed)
    assert violation(DataBudget(), **values) == expected


def test_unsafe_task_id_never_resolves_outside_workspace(phase3_workspace):
    with pytest.raises(ValueError):
        task_directory(phase3_workspace.catalog.paths.root, "../outside")


def test_completed_task_cleans_only_its_private_staging(phase3_workspace):
    ws = phase3_workspace
    service = DataTaskService(ws.catalog)
    identifier = start_csv(ws, service)
    assert wait_task(service, identifier)["status"] == "completed"
    directory = task_directory(service.root, identifier)
    assert not (directory / "source.bin").exists()
    spill = ws.catalog.paths.staging / f"data-spill-{identifier}-simulated"
    spill.mkdir()
    (spill / "leftover.tmp").write_bytes(b"partial")
    keep = ws.catalog.paths.staging / "keep-user.txt"
    keep.write_bytes(b"keep")
    atomic_json(directory / "spill.json", {"name": spill.name})
    service.poll(identifier)
    assert not spill.exists() and keep.read_bytes() == b"keep"
    ws.store.verify(
        ws.catalog.get_artifact(
            ws.datasets.current_version(ws.dataset.dataset_id).table_artifact_id
        )
    )


def test_task_cleanup_rejects_forged_spill_paths(phase3_workspace):
    service = DataTaskService(phase3_workspace.catalog)
    identifier = start_csv(phase3_workspace, service)
    wait_task(service, identifier)
    directory = task_directory(service.root, identifier)
    atomic_json(directory / "spill.json", {"name": "../outside"})
    with pytest.raises(ResourceError):
        service.poll(identifier)


def test_atomic_telemetry_retries_transient_windows_reader_conflict(tmp_path, monkeypatch):
    import autoanalyst.execution.data_tasks as module

    replace_file = module.os.replace
    attempts = 0

    def temporary_conflict(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("sharing violation")
        replace_file(source, target)

    monkeypatch.setattr(module.os, "replace", temporary_conflict)
    target = tmp_path / "metrics.json"
    atomic_json(target, {"peak": 123})
    assert read_json(target) == {"peak": 123} and attempts == 3
    assert not list(tmp_path.glob("*.tmp"))


def test_worker_adoption_preserves_identity_and_refuses_foreign_request(phase3_workspace):
    import os

    from autoanalyst.execution.lease import (
        activate_worker,
        adopt_worker,
        read_lease,
        release_worker,
        reserve_worker,
    )

    root = phase3_workspace.catalog.paths.root
    identifier = str(uuid4())
    reserve_worker(root, identifier)
    activate_worker(root, identifier, os.getpid())
    adopted = adopt_worker(root, identifier, os.getpid())
    assert read_lease(root) == adopted and adopted.pid == os.getpid()
    with pytest.raises(ResourceError):
        adopt_worker(root, str(uuid4()), os.getpid())
    release_worker(root, identifier)


def test_distinct_values_are_bounded_and_search_is_literal(phase3_workspace):
    from autoanalyst.data import CSVIngestor
    from autoanalyst.data.table_access import TableAccess

    ws = phase3_workspace
    imported = CSVIngestor(ws.catalog, ws.store).import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        source=("name\n" + "\n".join(f"item-{i:03}" for i in range(300)) + "\n").encode(),
    )
    column = next(c for c in imported.columns if not c.is_system)
    service = TableAccess(ws.catalog, ws.store)
    choices = service.distinct_values(imported.version.version_id, column.column_id)
    assert len(choices.values) == 200 and choices.has_more
    selected = service.distinct_values(imported.version.version_id, column.column_id, search="-299")
    assert selected.values == ("item-299",) and not selected.has_more
    assert not service.distinct_values(
        imported.version.version_id, column.column_id, search="' OR 1=1 --"
    ).values
