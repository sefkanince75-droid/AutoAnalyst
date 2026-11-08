from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest

import autoanalyst.storage.sqlite as sqlite_module
from autoanalyst.data import CSVIngestor, XLSXIngestor


def _xlsx_bytes():
    buffer = BytesIO()
    pd.DataFrame({"value": [10, 20]}).to_excel(buffer, index=False, sheet_name="Data")
    return buffer.getvalue()


@pytest.mark.parametrize("source_format", ["csv", "xlsx"])
@pytest.mark.parametrize("failure", ["parquet", "publication", "finalize", "after_commit"])
def test_import_failure_preserves_registered_objects_and_removes_only_unpublished_files(
    phase3_workspace, monkeypatch, source_format, failure
):
    ws = phase3_workspace
    ingestor = (
        CSVIngestor(ws.catalog, ws.store)
        if source_format == "csv"
        else XLSXIngestor(ws.catalog, ws.store)
    )
    arguments = {
        "project_id": ws.project.project_id,
        "dataset_id": ws.dataset.dataset_id,
        "source": b"value\n10\n20\n" if source_format == "csv" else _xlsx_bytes(),
    }
    if source_format == "xlsx":
        arguments["sheet_name"] = "Data"
    previous = ws.datasets.current_version(ws.dataset.dataset_id)
    before = {p for p in ws.catalog.paths.projects.rglob("*") if p.is_file()}

    def fail(*args, **kwargs):
        raise OSError("simulated failure")

    if failure == "parquet":
        monkeypatch.setattr(ingestor, "_write_parquet", fail)
    elif failure == "publication":
        # Fail inside the SQL transaction, after artifact/source/version inserts.
        monkeypatch.setattr(sqlite_module, "_move_head", fail)
    elif failure == "finalize":
        monkeypatch.setattr(ws.store, "finalize", fail)
    else:
        publish = ws.catalog.publish_dataset_import

        def fail_after_commit(**kwargs):
            publish(**kwargs)
            raise OSError("simulated failure after commit")

        monkeypatch.setattr(ws.catalog, "publish_dataset_import", fail_after_commit)

    operation = ingestor.import_csv if source_format == "csv" else ingestor.import_xlsx
    with pytest.raises(OSError, match="simulated failure"):
        operation(**arguments)
    assert not list(ws.catalog.paths.staging.iterdir())
    after = {p for p in ws.catalog.paths.projects.rglob("*") if p.is_file()}
    if failure == "after_commit":
        assert len(after - before) == 2
        assert ws.datasets.current_version(ws.dataset.dataset_id) != previous
    else:
        assert after == before
        assert ws.datasets.current_version(ws.dataset.dataset_id) == previous
    with ws.catalog.connection() as connection:
        registered = connection.execute("SELECT artifact_id FROM artifacts").fetchall()
    for row in registered:
        assert ws.store.verify(ws.catalog.get_artifact(row[0]))


@pytest.mark.parametrize("content", [b"", b"not an xlsx archive"])
def test_rejected_xlsx_leaves_previous_version_intact(phase3_workspace, content):
    ws = phase3_workspace
    from autoanalyst.domain.errors import DataError

    with pytest.raises(DataError):
        XLSXIngestor(ws.catalog, ws.store).import_xlsx(
            project_id=ws.project.project_id,
            dataset_id=ws.dataset.dataset_id,
            source=content,
            sheet_name="Data",
        )
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
