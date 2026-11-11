from __future__ import annotations

import csv
import io
from dataclasses import replace
from uuid import uuid4

import pyarrow.parquet as pq
import pytest

from autoanalyst.application.data_export import DataExportService
from autoanalyst.data import CSVIngestor
from autoanalyst.data.table_access import TableAccess
from autoanalyst.domain.errors import DataError, SchemaError


def export(ws, target, *, version=None, **kwargs):
    return DataExportService(ws.catalog, ws.store).export(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        version_id=(version or ws.imported.version).version_id,
        format=target.suffix[1:],
        destination=target,
        **kwargs,
    )


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_clean_full_version_round_trip(phase3_workspace, tmp_path, fmt):
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
    preview = ws.preparation.preview_recipe(recipe.recipe_id)
    version = ws.preparation.apply_preview(preview.preview_id)
    target = tmp_path.parent / f"{uuid4()}.{fmt}"
    try:
        receipt = export(ws, target, version=version)
        assert receipt.rows == 4
        assert receipt.columns == ("num", "text", "flag", "group")
        assert receipt.version_id == version.version_id
        content = DataExportService.download_bytes(receipt, limit=1024**2)
        if fmt == "parquet":
            table = pq.read_table(io.BytesIO(content))
            assert table.column_names == list(receipt.columns)
            expected = TableAccess(ws.catalog, ws.store).ordered_export(version.version_id)
            user = [c for c in ws.catalog.list_columns(version.version_id) if not c["is_system"]]
            assert table.to_pydict() == {
                c["display_name"]: expected[c["physical_name"]].to_pylist() for c in user
            }
            original = pq.read_schema(
                ws.store.resolve_relative_path(
                    ws.catalog.get_artifact(version.table_artifact_id).relative_path
                )
            )
            assert table.schema.types == [original.field(c["physical_name"]).type for c in user]
        else:
            rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
            assert rows[0] == list(receipt.columns) and len(rows) == 5
            assert [r[0] for r in rows[1:]] == ["1", "2", "4", "inf"]
        assert ws.datasets.current_version(ws.dataset.dataset_id) == version
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "case",
    [
        "format",
        "workspace",
        "extension",
        "missing_parent",
        "project",
        "dataset",
        "version",
        "existing",
        "corrupt",
    ],
)
def test_invalid_export_preserves_source_and_target(phase3_workspace, tmp_path, case):
    ws = phase3_workspace
    target = tmp_path.parent / f"{uuid4()}.csv"
    args = dict(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        version_id=ws.imported.version.version_id,
        format="csv",
        destination=target,
    )
    if case == "format":
        args["format"] = "xlsx"
    if case == "workspace":
        args["destination"] = tmp_path / "data.csv"
    if case == "extension":
        args["destination"] = target.with_suffix(".txt")
    if case == "missing_parent":
        args["destination"] = tmp_path.parent / str(uuid4()) / "data.csv"
    if case == "project":
        args["project_id"] = ws.projects.create("other").project_id
    if case == "dataset":
        args["dataset_id"] = ws.datasets.create(ws.project.project_id, "other").dataset_id
    if case == "version":
        args["version_id"] = str(uuid4())
    if case == "existing":
        target.write_bytes(b"keep")
    if case == "corrupt":
        artifact = ws.catalog.get_artifact(ws.imported.version.table_artifact_id)
        ws.store.resolve_relative_path(artifact.relative_path).write_bytes(b"corrupt")
    try:
        with pytest.raises((DataError, SchemaError)):
            DataExportService(ws.catalog, ws.store).export(**args)
        if case == "existing":
            assert target.read_bytes() == b"keep"
        else:
            assert not target.exists()
        assert not list(tmp_path.parent.glob(".autoanalyst-export-*.tmp"))
    finally:
        target.unlink(missing_ok=True)


def test_concurrent_destination_is_never_overwritten(phase3_workspace, tmp_path, monkeypatch):
    import os

    target = tmp_path.parent / f"{uuid4()}.csv"
    publish = os.rename if os.name == "nt" else os.link

    def raced(source, destination):
        target.write_bytes(b"concurrent")
        return publish(source, destination)

    monkeypatch.setattr(os, "rename" if os.name == "nt" else "link", raced)
    try:
        with pytest.raises(DataError):
            export(phase3_workspace, target)
        assert target.read_bytes() == b"concurrent"
        assert not list(target.parent.glob(".autoanalyst-export-*.tmp"))
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("change", ["same_size", "missing", "limit"])
def test_download_rechecks_output(phase3_workspace, tmp_path, change):
    target = tmp_path.parent / f"{uuid4()}.csv"
    try:
        receipt = export(phase3_workspace, target)
        if change == "missing":
            target.unlink()
        elif change == "same_size":
            target.write_bytes(b"x" * receipt.size)
        with pytest.raises(DataError):
            DataExportService.download_bytes(receipt, limit=1 if change == "limit" else 1024**2)
    finally:
        target.unlink(missing_ok=True)


def test_row_count_mismatch_never_publishes_output(phase3_workspace, tmp_path, monkeypatch):
    ws = phase3_workspace
    get_version = ws.catalog.get_version
    monkeypatch.setattr(
        ws.catalog, "get_version", lambda version_id: replace(get_version(version_id), row_count=99)
    )
    target = tmp_path.parent / f"{uuid4()}.parquet"
    with pytest.raises(DataError):
        export(ws, target)
    assert not target.exists()


def test_csv_unicode_quotes_and_line_breaks(phase3_workspace, tmp_path):
    ws = phase3_workspace
    imported = CSVIngestor(ws.catalog, ws.store).import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        source='"Türkçe,başlık",count\n"İstanbul\n""alıntı""",1\n"a,b",2\n'.encode(),
    )
    target = tmp_path.parent / f"{uuid4()}.csv"
    try:
        receipt = export(ws, target, version=imported.version)
        rows = list(
            csv.reader(
                io.StringIO(
                    DataExportService.download_bytes(receipt, limit=1024**2).decode("utf-8-sig")
                )
            )
        )
        assert rows == [["Türkçe,başlık", "count"], ['İstanbul\n"alıntı"', "1"], ["a,b", "2"]]
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_empty_prepared_table_exports_headers_and_schema(phase3_workspace, tmp_path, fmt):
    ws = phase3_workspace
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.imported.version.version_id,
        steps=[
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": ws.column("num")["column_id"],
                    "operator": "gt",
                    "value": 1e100,
                },
            }
        ],
    )
    preview = ws.preparation.preview_recipe(recipe.recipe_id)
    version = ws.preparation.apply_preview(preview.preview_id)
    # The source includes infinity; a text equality filter gives a truly empty result.
    if version.row_count:
        recipe = ws.preparation.create_recipe(
            project_id=ws.project.project_id,
            base_version_id=version.version_id,
            steps=[
                {
                    "operation": "filter_rows",
                    "parameters": {
                        "column_id": ws.column("group")["column_id"],
                        "operator": "eq",
                        "value": "absent",
                    },
                }
            ],
        )
        version = ws.preparation.apply_preview(
            ws.preparation.preview_recipe(recipe.recipe_id).preview_id
        )
    target = tmp_path.parent / f"{uuid4()}.{fmt}"
    try:
        receipt = export(ws, target, version=version)
        assert receipt.rows == 0
        if fmt == "parquet":
            assert pq.read_table(target).num_rows == 0
        else:
            assert len(list(csv.reader(io.StringIO(target.read_text(encoding="utf-8-sig"))))) == 1
    finally:
        target.unlink(missing_ok=True)


def test_multi_batch_export_preserves_every_row_in_order(phase3_workspace, tmp_path):
    ws = phase3_workspace
    source = ("value\n" + "\n".join(str(i) for i in range(66001)) + "\n").encode()
    imported = CSVIngestor(ws.catalog, ws.store).import_csv(
        project_id=ws.project.project_id, dataset_id=ws.dataset.dataset_id, source=source
    )
    target = tmp_path.parent / f"{uuid4()}.parquet"
    try:
        receipt = export(ws, target, version=imported.version)
        assert receipt.rows == 66001
        assert pq.read_table(target)["value"].to_pylist() == list(range(66001))
    finally:
        target.unlink(missing_ok=True)


def test_writer_failure_removes_only_private_temporary_file(
    phase3_workspace, tmp_path, monkeypatch
):
    import autoanalyst.application.data_export as module

    def fail(*args, **kwargs):
        raise OSError("simulated write error")

    monkeypatch.setattr(module.csv, "CSVWriter", fail)
    target = tmp_path.parent / f"{uuid4()}.csv"
    with pytest.raises(OSError, match="simulated write error"):
        export(phase3_workspace, target)
    assert not target.exists()
    assert not list(target.parent.glob(".autoanalyst-export-*.tmp"))


def test_parquet_export_preserves_dates_nulls_and_types(phase3_workspace, tmp_path):
    import pandas as pd

    from autoanalyst.data import XLSXIngestor

    ws = phase3_workspace
    data = io.BytesIO()
    frame = pd.DataFrame(
        {
            "when": pd.to_datetime(["2025-01-01", None, "2025-02-02"]),
            "amount": [1.5, None, 3.5],
            "flag": [True, False, None],
        }
    )
    with pd.ExcelWriter(data, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Data", index=False)
    imported = XLSXIngestor(ws.catalog, ws.store).import_xlsx(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        source=data.getvalue(),
        sheet_name="Data",
    )
    target = tmp_path.parent / f"{uuid4()}.parquet"
    try:
        export(ws, target, version=imported.version)
        table = pq.read_table(target)
        assert table["when"].null_count == 1 and table["amount"].null_count == 1
        assert table["when"][0].as_py() == pd.Timestamp("2025-01-01")
        source = TableAccess(ws.catalog, ws.store).resolve(imported.version.version_id)
        fields = [c for c in source.columns if not c.is_system]
        schema = pq.read_schema(source.parquet_path)
        assert table.schema.types == [schema.field(c.physical_name).type for c in fields]
    finally:
        target.unlink(missing_ok=True)


@pytest.mark.parametrize("filename", ["NUL.csv", "CON.csv", "data.csv:stream.csv", "COM1.parquet"])
def test_export_refuses_windows_device_and_stream_names(phase3_workspace, tmp_path, filename):
    target = tmp_path.parent / filename
    with pytest.raises(SchemaError):
        export(phase3_workspace, target)
