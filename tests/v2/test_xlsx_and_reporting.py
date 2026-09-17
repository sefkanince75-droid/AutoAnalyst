from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pandas as pd

from autoanalyst.analyses.profiling import ProfilingModule
from autoanalyst.application import DatasetService, ProjectService
from autoanalyst.data import XLSXIngestor
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.reporting import ReportService
from autoanalyst.storage import ArtifactStore, SQLiteCatalog
from autoanalyst.storage.runs import RunStore


class _Event:
    def is_set(self):
        return False


def test_xlsx_explicit_sheet_and_offline_reports(tmp_path):
    catalog = SQLiteCatalog(tmp_path)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Reports")
    dataset = DatasetService(catalog).create(project.project_id, "Sheet")
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame({"other": [9]}).to_excel(writer, sheet_name="Other", index=False)
    ingestor = XLSXIngestor(catalog, store)
    assert ingestor.sheet_names(buffer.getvalue()) == ("Data", "Other")
    imported = ingestor.import_xlsx(project_id=project.project_id, dataset_id=dataset.dataset_id, source=buffer.getvalue(), sheet_name="Data", original_name="book.xlsx")
    assert imported.version.row_count == 3

    spec = AnalysisSpec(
        spec_id=str(uuid4()), project_id=project.project_id,
        module_id=AnalysisModuleId.PROFILING, module_version="2.0", operation="profile",
        input_version_id=imported.version.version_id, column_roles=(), parameters={}, seed=42,
        resource_budget=ResourceBudget(100_000_000, 100_000_000, 30, 1),
        spec_hash=fingerprint({"version": imported.version.version_id, "op": "profile"}), created_at=utc_now(),
    )
    run_store = RunStore(catalog)
    coordinator = ExecutionCoordinator(run_store)
    run = coordinator.create_run(spec, request_key="report-profile")
    coordinator.execute_inline(run.run_id, ProfilingModule(catalog, store), cancellation_event=_Event())
    result = run_store.result_for_run(run.run_id)
    assert result is not None
    reports = ReportService(catalog, store)
    html = reports.render(result.result_id, format="html", language="en")
    markdown = reports.render(result.result_id, format="markdown", language="tr")
    json_report = reports.render(result.result_id, format="json", language="en")
    csv_report = reports.render(result.result_id, format="csv", language="en")
    assert b"AutoAnalyst Analysis Report" in reports.artifact_bytes(html)
    assert b"AutoAnalyst" in reports.artifact_bytes(markdown)
    assert reports.artifact_bytes(json_report).startswith(b"{")
    assert reports.artifact_bytes(csv_report).startswith(b"PK")
    assert reports.render(result.result_id, format="html", language="en").report_id == html.report_id
