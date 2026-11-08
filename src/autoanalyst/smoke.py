"""Real worker/import/report/reopen journey shared by installed and frozen smoke tests."""

from __future__ import annotations

import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from .application import DatasetService, ProjectService
from .bootstrap import build_runtime
from .data import CSVIngestor
from .domain.codec import fingerprint, utc_now
from .domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from .domain.runs import RunStatus
from .reporting import ReportService
from .reporting.charts import chart_views


def verify_workspace_journey(workspace: Path) -> None:
    runtime = build_runtime(workspace)
    project = ProjectService(runtime.catalog).create("Portable self-test")
    dataset = DatasetService(runtime.catalog).create(project.project_id, "Sample")
    imported = CSVIngestor(runtime.catalog, runtime.artifact_store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"value,group\n1,a\n2,a\n3,b\n4,b\n",
        original_name="sample.csv",
    )
    spec = AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project.project_id,
        module_id=AnalysisModuleId.PROFILING,
        module_version="2.0",
        operation="profile",
        input_version_id=imported.version.version_id,
        column_roles=(),
        parameters={},
        seed=42,
        resource_budget=ResourceBudget(2_000_000_000, 2_000_000_000, 60, 1),
        spec_hash=fingerprint({"version": imported.version.version_id, "operation": "profile"}),
        created_at=utc_now(),
    )
    run = runtime.coordinator.create_run(spec, request_key="self-test-profile")
    process = runtime.coordinator.start_worker_process(run.run_id, workspace)
    try:
        exit_code = process.wait(timeout=90)
        if exit_code != 0:
            raise RuntimeError(f"Self-test analysis worker exited with code {exit_code}")
    finally:
        if process.poll() is None:
            runtime.coordinator.request_cancel(run.run_id, workspace, cooperative_grace_seconds=0)
            process.wait(timeout=10)

    # Startup recovery must publish the completed worker manifest without the
    # original Streamlit session or an in-memory result object.
    reopened = build_runtime(workspace)
    completed = reopened.run_store.get_run(run.run_id)
    if completed.status is not RunStatus.COMPLETED or completed.result_id is None:
        raise RuntimeError("Self-test analysis did not persist a completed result")
    result = reopened.run_store.get_result(completed.result_id)
    counts = {metric.name: metric.value for metric in result.metrics}
    if counts.get("row_count") != 4:
        raise RuntimeError("Self-test profile row count is incorrect")
    reports = ReportService(reopened.catalog, reopened.artifact_store)
    report = reports.render(result.result_id, format="html", language="en")
    content = reports.artifact_bytes(report)
    if b"AutoAnalyst Analysis Report" not in content:
        raise RuntimeError("Self-test report could not be rendered")
    views = chart_views(result, "en")
    if not views or any(not view.svg or view.svg.encode() not in content for view in views):
        raise RuntimeError("Self-test report did not preserve recorded charts")
    import io

    from openpyxl import load_workbook

    for fmt in ("pdf", "xlsx"):
        exported_report = reports.render(result.result_id, format=fmt, language="tr")
        exported_bytes = reports.artifact_bytes(exported_report)
        if fmt == "pdf" and not exported_bytes.startswith(b"%PDF-"):
            raise RuntimeError("Self-test PDF is invalid")
        if fmt == "xlsx" and not load_workbook(io.BytesIO(exported_bytes))["Table_1"].max_row:
            raise RuntimeError("Self-test XLSX is invalid")

    final = build_runtime(workspace)
    final_reports = ReportService(final.catalog, final.artifact_store)
    if final_reports.artifact_bytes(final_reports.get(report.report_id)) != content:
        raise RuntimeError("Self-test report did not survive reopening")
    if DatasetService(final.catalog).current_version(dataset.dataset_id) != imported.version:
        raise RuntimeError("Self-test dataset did not survive reopening")
    from .application.snapshots import WorkspaceSnapshotService

    with TemporaryDirectory(prefix="autoanalyst-snapshot-smoke-") as directory:
        backup = WorkspaceSnapshotService(final.catalog, final.artifact_store).export_snapshot(
            Path(directory) / "backup.aasnapshot"
        )
        inspection = WorkspaceSnapshotService.inspect_snapshot(backup)
        if inspection.projects != 1 or inspection.results != 1:
            raise RuntimeError("Self-test snapshot inspection is incorrect")
        restored = WorkspaceSnapshotService.import_snapshot(
            backup, Path(directory) / "restored", trusted=True
        )
        recovered = build_runtime(restored)
        restored_reports = ReportService(recovered.catalog, recovered.artifact_store)
        if restored_reports.artifact_bytes(restored_reports.get(report.report_id)) != content:
            raise RuntimeError("Self-test snapshot did not preserve report data")
        if (
            DatasetService(recovered.catalog).current_version(dataset.dataset_id)
            != imported.version
        ):
            raise RuntimeError("Self-test snapshot did not preserve dataset version")

        from .application.storage_maintenance import MIN_AGE_SECONDS, StorageMaintenanceService

        staged = recovered.artifact_store.stage_bytes(
            b"maintenance self-test",
            project_id=project.project_id,
            owner_run_id=str(uuid4()),
            kind="self-test",
            media_type="application/octet-stream",
        )
        old = time.time() - MIN_AGE_SECONDS - 60
        os.utime(staged.staging_path, (old, old))
        maintenance = StorageMaintenanceService(recovered.catalog)
        review = maintenance.scan()
        relative = staged.staging_path.relative_to(restored).as_posix()
        batch = maintenance.quarantine(review, (relative,))
        if staged.staging_path.exists() or maintenance.restore(batch) != 1:
            raise RuntimeError("Self-test reversible maintenance failed")
        if staged.staging_path.read_bytes() != b"maintenance self-test":
            raise RuntimeError("Self-test maintenance did not preserve file bytes")

        import pyarrow.parquet as pq

        from .application.data_export import DataExportService
        from .reporting.summary import result_summary

        exporter = DataExportService(recovered.catalog, recovered.artifact_store)
        for fmt in ("csv", "parquet"):
            exported = exporter.export(
                project_id=project.project_id,
                dataset_id=dataset.dataset_id,
                version_id=imported.version.version_id,
                format=fmt,
                destination=Path(directory) / f"data.{fmt}",
            )
            if exported.rows != 4 or exported.columns != ("value", "group"):
                raise RuntimeError("Self-test data export scope is incorrect")
            if fmt == "parquet" and pq.read_table(exported.path).column_names != ["value", "group"]:
                raise RuntimeError("Self-test exported Parquet could not be reopened")
            exporter.download_bytes(exported, limit=1024**2)
        if result_summary(result, "en").scope.encode() not in content:
            raise RuntimeError("Self-test report summary is missing")

        import time as clock

        from .application.preparation import PreparationService
        from .execution.data_tasks import DataTaskService

        tasks = DataTaskService(recovered.catalog)

        def wait_task(identifier):
            deadline = clock.monotonic() + 60
            while clock.monotonic() < deadline:
                state = tasks.poll(identifier)
                if state["status"] != "running":
                    return state
                clock.sleep(0.05)
            tasks.cancel(identifier)
            raise RuntimeError("Self-test data worker timed out")

        identifier = tasks.start(
            operation="csv",
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            source=b"value,group\n1,a\n2,b\n",
        )
        state = wait_task(identifier)
        if state["status"] != "completed" or state.get("peak_rss_bytes", 0) <= 0:
            raise RuntimeError(f"Self-test isolated import failed: {state.get('reason')}")
        current = DatasetService(recovered.catalog).current_version(dataset.dataset_id)
        service = PreparationService(recovered.catalog, recovered.artifact_store)
        column = next(
            c
            for c in recovered.catalog.list_columns(current.version_id)
            if c["display_name"] == "value"
        )
        recipe = service.create_recipe(
            project_id=project.project_id,
            base_version_id=current.version_id,
            steps=[
                {
                    "operation": "rename_column",
                    "parameters": {"column_id": column["column_id"], "new_name": "renamed"},
                }
            ],
        )
        identifier = tasks.start(
            operation="preparation",
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            recipe_id=recipe.recipe_id,
        )
        if (
            wait_task(identifier)["status"] != "completed"
            or service.get_preview(identifier).after_row_count != 2
        ):
            raise RuntimeError("Self-test isolated preparation failed")
        identifier = tasks.start(
            operation="csv",
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            source=b"value\n99\n",
        )
        tasks.cancel(identifier)
        if (
            wait_task(identifier)["status"] != "cancelled"
            or DatasetService(recovered.catalog).current_version(dataset.dataset_id) != current
        ):
            raise RuntimeError("Self-test data cancellation did not preserve the head")
        from dataclasses import replace

        from .application.examples import create_example
        from .domain.datasets import ColumnRole, ColumnUsage, SemanticType

        for kind, module_id in (
            ("regression", AnalysisModuleId.REGRESSION),
            ("multiclass", AnalysisModuleId.MULTICLASS_CLASSIFICATION),
        ):
            example = create_example(recovered, kind, language="en")
            example_data = DatasetService(recovered.catalog).list(example.project_id)[0]
            version = DatasetService(recovered.catalog).current_version(example_data.dataset_id)
            columns = {
                c["display_name"]: c for c in recovered.catalog.list_columns(version.version_id)
            }
            roles = (
                ColumnRole(
                    columns["value"]["column_id"],
                    SemanticType.NUMERIC,
                    (ColumnUsage.FEATURE,),
                    True,
                    True,
                ),
                ColumnRole(
                    columns["target"]["column_id"],
                    SemanticType.NUMERIC if kind == "regression" else SemanticType.CATEGORICAL,
                    (ColumnUsage.TARGET,),
                    True,
                ),
            )
            example_spec = replace(
                spec,
                spec_id=str(uuid4()),
                project_id=example.project_id,
                module_id=module_id,
                operation="train_validate",
                input_version_id=version.version_id,
                column_roles=roles,
                parameters={},
                spec_hash=fingerprint({"example": kind, "version": version.version_id}),
            )
            example_run = recovered.coordinator.create_run(
                example_spec, request_key="self-test-" + kind
            )
            worker = recovered.coordinator.start_worker_process(example_run.run_id, restored)
            try:
                if worker.wait(timeout=90) != 0:
                    raise RuntimeError(f"Self-test {kind} worker failed")
            finally:
                if worker.poll() is None:
                    recovered.coordinator.request_cancel(
                        example_run.run_id, restored, cooperative_grace_seconds=0
                    )
                    worker.wait(timeout=10)
            recovered = build_runtime(restored)
            if recovered.run_store.get_run(example_run.run_id).status is not RunStatus.COMPLETED:
                raise RuntimeError(f"Self-test {kind} result did not persist")
