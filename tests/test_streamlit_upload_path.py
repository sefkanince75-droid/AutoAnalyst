"""Regression coverage for the real Streamlit multi-file upload order."""

from io import BytesIO
from zipfile import ZipFile

import pandas as pd
from streamlit.proto.Common_pb2 import FileURLs
from streamlit.runtime.uploaded_file_manager import UploadedFile, UploadedFileRec

from src.data_loader import detect_file_type, list_excel_sheets, load_csv, load_excel_sheet
from src.dataset_combination import DatasetPart, dataset_identity, human_readable_bytes
from src.upload_processing import CapturedUpload, capture_uploads


class CountingUploadedFile(UploadedFile):
    """A real UploadedFile that records caller-owned content reads."""

    def __init__(self, record: UploadedFileRec) -> None:
        super().__init__(record, FileURLs())
        self.getvalue_calls = 0

    def getvalue(self) -> bytes:
        self.getvalue_calls += 1
        return super().getvalue()


def _xlsx_content() -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"feature": [1, 2], "target": [0, 1]}).to_excel(
            writer, sheet_name="Overview", index=False
        )
        pd.DataFrame({"amount": [10, 20, 30], "target": [1, 0, 1]}).to_excel(
            writer, sheet_name="Transactions", index=False
        )
    return buffer.getvalue()


def _upload(name: str, content: bytes) -> CountingUploadedFile:
    media_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if name.endswith(".xlsx")
        else "text/csv"
    )
    return CountingUploadedFile(UploadedFileRec(name, name, media_type, content))


def _exercise_app_order(uploads: list[CountingUploadedFile]) -> list[CapturedUpload]:
    records = capture_uploads(uploads)

    # The UI displays metadata and constructs identity inputs before loading.
    assert all(human_readable_bytes(record.size_bytes) for record in records)
    parts: list[DatasetPart] = []
    for record in records:
        worksheet = None
        if detect_file_type(record.name) == "XLSX":
            assert record.content[:4] == b"PK\x03\x04"
            with ZipFile(BytesIO(record.content)) as archive:
                assert "xl/workbook.xml" in archive.namelist()
            assert list_excel_sheets(record.content) == ["Overview", "Transactions"]
            worksheet = "Transactions"
        parts.append(
            DatasetPart(record.name, record.digest, record.size_bytes, pd.DataFrame(), worksheet)
        )
    assert dataset_identity(parts, mode="single", add_source_column=False)

    for record in records:
        if detect_file_type(record.name) == "XLSX":
            loaded = load_excel_sheet(record.content, "Transactions")
            assert loaded["amount"].tolist() == [10, 20, 30]
        else:
            assert load_csv(BytesIO(record.content)).shape == (2, 2)
    return records


def test_real_streamlit_uploaded_xlsx_is_captured_once_without_rewind() -> None:
    upload = _upload("book.xlsx", _xlsx_content())
    upload.read(11)  # Simulate prior Streamlit/internal inspection.

    records = _exercise_app_order([upload])

    assert upload.getvalue_calls == 1
    assert upload.tell() == 11
    assert isinstance(records[0].content, bytes)


def test_real_streamlit_multiple_xlsx_uploads_follow_the_same_safe_path() -> None:
    uploads = [_upload("one.xlsx", _xlsx_content()), _upload("two.xlsx", _xlsx_content())]
    _exercise_app_order(uploads)
    assert [upload.getvalue_calls for upload in uploads] == [1, 1]


def test_real_streamlit_csv_and_xlsx_uploads_follow_the_same_safe_path() -> None:
    uploads = [
        _upload("rows.csv", b"feature,target\n1,0\n2,1\n"),
        _upload("book.xlsx", _xlsx_content()),
    ]
    _exercise_app_order(uploads)
    assert [upload.getvalue_calls for upload in uploads] == [1, 1]


def test_hashing_and_metadata_never_read_the_uploaded_file_again() -> None:
    upload = _upload("book.xlsx", _xlsx_content())
    records = capture_uploads([upload])
    record = records[0]

    for _ in range(3):
        human_readable_bytes(record.size_bytes)
        DatasetPart(record.name, record.digest, record.size_bytes, pd.DataFrame(), "Overview")
    assert list_excel_sheets(record.content) == ["Overview", "Transactions"]
    assert load_excel_sheet(record.content, "Overview").shape == (2, 2)
    assert upload.getvalue_calls == 1
