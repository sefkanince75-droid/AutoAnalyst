from __future__ import annotations

from io import BytesIO

import pandas as pd
import pytest

from autoanalyst.data import CSVIngestor, XLSXIngestor
from autoanalyst.domain.errors import DataError, SchemaError


@pytest.mark.parametrize(
    ("delimiter", "encoding"),
    [(";", "cp1254"), ("\t", "utf-16"), ("|", "iso8859-9"), (",", "utf-8-sig")],
)
def test_csv_preview_and_import_use_same_explicit_parsing_options(
    phase3_workspace, delimiter, encoding
):
    ws = phase3_workspace
    ingestor = CSVIngestor(ws.catalog, ws.store)
    content = f"şehir{delimiter}adet\nİzmir{delimiter}10\nIğdır{delimiter}20\n".encode(encoding)
    preview = ingestor.preview_csv(content, delimiter=delimiter, encoding=encoding)
    assert preview.frame.columns.tolist() == ["şehir", "adet"]
    assert preview.frame["şehir"].tolist() == ["İzmir", "Iğdır"]
    assert preview.frame["adet"].tolist() == [10, 20]
    assert not preview.has_more_rows
    assert ws.datasets.get(ws.dataset.dataset_id).head_revision == 1
    imported = ingestor.import_csv(
        project_id=ws.project.project_id,
        dataset_id=ws.dataset.dataset_id,
        source=content,
        delimiter=delimiter,
        encoding=encoding,
    )
    assert imported.version.column_count == 2
    assert imported.version.row_count == len(preview.frame)
    assert imported.version.parse_contract is not None


def test_csv_preview_is_bounded_does_not_publish_and_handles_quoted_newlines(phase3_workspace):
    ws = phase3_workspace
    content = "value,text\n" + "".join(f'{i},"line {i}\nsecond line"\n' for i in range(240))
    before = {path for path in ws.catalog.paths.root.rglob("*") if path.is_file()}
    preview = CSVIngestor(ws.catalog, ws.store).preview_csv(content.encode("utf-8"))
    assert len(preview.frame) == 100
    assert preview.has_more_rows
    assert preview.frame.value.tolist() == list(range(100))
    assert preview.frame.text.iloc[0] == "line 0\nsecond line"
    assert {path for path in ws.catalog.paths.root.rglob("*") if path.is_file()} == before
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version


@pytest.mark.parametrize("content", [b"", b"a,b\n", b"a,a\n1,2\n"])
def test_invalid_csv_preview_is_rejected(phase3_workspace, content):
    ws = phase3_workspace
    with pytest.raises((DataError, SchemaError)):
        CSVIngestor(ws.catalog, ws.store).preview_csv(content)
    assert ws.datasets.get(ws.dataset.dataset_id).head_revision == 1


def test_xlsx_preview_requires_selected_sheet_and_limits_rows(phase3_workspace):
    ws = phase3_workspace
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"wrong": [999]}).to_excel(writer, sheet_name="First", index=False)
        pd.DataFrame({"value": range(240)}).to_excel(writer, sheet_name="Selected", index=False)
    ingestor = XLSXIngestor(ws.catalog, ws.store)
    preview = ingestor.preview_xlsx(buffer.getvalue(), sheet_name="Selected")
    assert preview.frame.columns.tolist() == ["value"]
    assert preview.frame.value.tolist() == list(range(100))
    assert preview.has_more_rows
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    with pytest.raises(SchemaError):
        ingestor.preview_xlsx(buffer.getvalue(), sheet_name="Missing")
    with pytest.raises(SchemaError):
        ingestor.preview_xlsx(buffer.getvalue(), sheet_name="")


@pytest.mark.parametrize("row_count", [1, 100, 101])
def test_preview_reports_extra_rows_only_when_sample_is_truncated(phase3_workspace, row_count):
    ws = phase3_workspace
    content = ("value\n" + "".join(f"{index}\n" for index in range(row_count))).encode()
    sample = CSVIngestor(ws.catalog, ws.store).preview_csv(content)
    assert len(sample.frame) == min(100, row_count)
    assert sample.has_more_rows is (row_count > 100)


def test_successful_preview_does_not_skip_full_file_validation(phase3_workspace):
    ws = phase3_workspace
    ingestor = CSVIngestor(ws.catalog, ws.store)
    content = ("a,b\n" + "".join(f"{index},valid\n" for index in range(200)) + "1,2,3\n").encode()
    assert len(ingestor.preview_csv(content).frame) == 100
    with pytest.raises(DataError):
        ingestor.import_csv(
            project_id=ws.project.project_id, dataset_id=ws.dataset.dataset_id, source=content
        )
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
