from io import BytesIO, StringIO

import pandas as pd
import pytest

from src.data_loader import (
    CSVLoadError,
    EmptyWorksheetError,
    ExcelDependencyError,
    ExcelLoadError,
    UnsupportedFileTypeError,
    build_source_metadata,
    list_excel_sheets,
    load_csv,
    load_excel_sheet,
    detect_file_type,
)


def test_load_csv_accepts_tabular_data() -> None:
    dataframe = load_csv(StringIO("feature,target\n1,no\n2,yes\n"))
    assert dataframe.shape == (2, 2)


def test_load_csv_rejects_featureless_dataset() -> None:
    with pytest.raises(CSVLoadError, match="at least one feature"):
        load_csv(StringIO("target\nyes\nno\n"))


def _workbook_bytes(sheets: dict[str, pd.DataFrame]) -> BytesIO:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, dataframe in sheets.items():
            dataframe.to_excel(writer, sheet_name=name, index=False)
    buffer.seek(0)
    return buffer


def test_xlsx_single_sheet_loading() -> None:
    workbook = _workbook_bytes({"Data": pd.DataFrame({"feature": [1, 2], "target": [0, 1]})})
    assert list_excel_sheets(workbook) == ["Data"]
    workbook.seek(0)
    loaded = load_excel_sheet(workbook, "Data")
    assert loaded.shape == (2, 2)


def test_xlsx_multi_sheet_selection_loads_only_requested_sheet() -> None:
    workbook = _workbook_bytes(
        {
            "First": pd.DataFrame({"feature": [1, 2], "target": [0, 1]}),
            "Second": pd.DataFrame({"feature": [10, 20, 30], "target": [1, 0, 1]}),
        }
    )
    assert list_excel_sheets(workbook) == ["First", "Second"]
    workbook.seek(0)
    loaded = load_excel_sheet(workbook, "Second")
    assert loaded["feature"].tolist() == [10, 20, 30]


def test_xlsx_empty_sheet_and_invalid_workbook_are_rejected() -> None:
    workbook = _workbook_bytes({"Empty": pd.DataFrame()})
    with pytest.raises(EmptyWorksheetError):
        load_excel_sheet(workbook, "Empty")
    with pytest.raises(ExcelLoadError):
        list_excel_sheets(BytesIO(b"not an xlsx workbook"))


def test_dataset_source_metadata() -> None:
    dataframe = pd.DataFrame({"feature": [1, 2], "target": [0, 1]})
    metadata = build_source_metadata(
        dataframe,
        file_name="sample.xlsx",
        file_size_bytes=2048,
        worksheet="Data",
    )
    assert metadata.file_type == "XLSX"
    assert metadata.file_size_bytes == 2048
    assert metadata.worksheet == "Data"
    assert (metadata.rows, metadata.columns) == (2, 2)


def test_file_type_detection_rejects_unsupported_extensions() -> None:
    assert detect_file_type("sample.csv") == "CSV"
    assert detect_file_type("sample.XLSX") == "XLSX"
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type("sample.xls")


def test_repeated_inspection_and_loading_ignore_shared_stream_position() -> None:
    """Regression: worksheet discovery must not consume the upload used for loading."""
    content = _workbook_bytes(
        {
            "Overview": pd.DataFrame({"name": ["a", "b"], "target": [0, 1]}),
            "Transactions": pd.DataFrame({"amount": [10, 20, 30], "target": [1, 0, 1]}),
        }
    ).getvalue()
    shared_upload = BytesIO(content)
    shared_upload.seek(len(content))
    original_position = shared_upload.tell()

    assert list_excel_sheets(shared_upload) == ["Overview", "Transactions"]
    assert shared_upload.tell() == original_position
    assert list_excel_sheets(content) == ["Overview", "Transactions"]
    selected = load_excel_sheet(shared_upload, "Transactions")

    assert shared_upload.tell() == original_position
    assert selected["amount"].tolist() == [10, 20, 30]


def test_excel_dependency_failure_is_distinct_from_invalid_workbook(monkeypatch) -> None:
    workbook = _workbook_bytes(
        {"Data": pd.DataFrame({"feature": [1, 2], "target": [0, 1]})}
    ).getvalue()

    def missing_dependency(*args, **kwargs):
        raise ImportError("openpyxl is unavailable")

    monkeypatch.setattr(pd, "ExcelFile", missing_dependency)
    with pytest.raises(ExcelDependencyError) as error:
        list_excel_sheets(workbook)
    assert isinstance(error.value.__cause__, ImportError)


def test_excel_worksheet_dependency_failure_is_distinct(monkeypatch) -> None:
    workbook = _workbook_bytes(
        {"Data": pd.DataFrame({"feature": [1, 2], "target": [0, 1]})}
    ).getvalue()

    def missing_dependency(*args, **kwargs):
        raise ImportError("openpyxl is unavailable")

    monkeypatch.setattr(pd, "read_excel", missing_dependency)
    with pytest.raises(ExcelDependencyError):
        load_excel_sheet(workbook, "Data")
