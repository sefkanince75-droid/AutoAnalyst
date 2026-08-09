"""Position-independent CSV and XLSX ingestion utilities."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, ZipFile

import pandas as pd


logger = logging.getLogger(__name__)


class CSVLoadError(ValueError):
    """Raised when an uploaded CSV cannot be safely loaded."""


class ExcelLoadError(ValueError):
    """Raised when an Excel workbook or worksheet cannot be safely loaded."""


class ExcelDependencyError(ExcelLoadError):
    """Raised when the configured XLSX parsing dependency is unavailable."""


class EmptyWorksheetError(ExcelLoadError):
    """Raised when a selected worksheet has no usable tabular rows."""


class UnsupportedFileTypeError(ValueError):
    """Raised when an input is not a supported V1.1 format."""


@dataclass(frozen=True)
class DatasetSourceMetadata:
    """Compact source metadata for a loaded tabular dataset."""

    file_name: str
    file_type: str
    file_size_bytes: int
    worksheet: str | None
    rows: int
    columns: int


def detect_file_type(file_name: str) -> str:
    """Return a normalized supported file type from a filename."""
    suffix = Path(file_name).suffix.lower()
    if suffix == ".csv":
        return "CSV"
    if suffix == ".xlsx":
        return "XLSX"
    raise UnsupportedFileTypeError(f"Unsupported file type: {suffix or 'unknown'}")


def _validate_dataframe(dataframe: pd.DataFrame, *, excel: bool = False) -> pd.DataFrame:
    """Reject empty or structurally unusable tabular inputs."""
    if dataframe.empty or len(dataframe.index) == 0:
        if excel:
            raise EmptyWorksheetError("The selected worksheet contains no usable data rows.")
        raise CSVLoadError("The selected dataset contains no usable data rows.")
    error_type = ExcelLoadError if excel else CSVLoadError
    if len(dataframe.columns) < 2:
        raise error_type("The dataset needs at least one feature column and one target column.")
    if dataframe.columns.duplicated().any():
        raise error_type("The dataset contains duplicate column names.")
    return dataframe


def load_csv(source: str | Path | BinaryIO) -> pd.DataFrame:
    """Load a CSV source and reject empty or structurally invalid datasets."""
    try:
        dataframe = pd.read_csv(source)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError, OSError) as exc:
        raise CSVLoadError(f"Could not read this CSV: {exc}") from exc

    return _validate_dataframe(dataframe)


def _excel_content(source: bytes | bytearray | str | Path | BinaryIO) -> bytes:
    """Snapshot XLSX content without relying on or changing a caller's stream position."""
    if isinstance(source, bytes):
        return source
    if isinstance(source, bytearray):
        return bytes(source)
    if isinstance(source, (str, Path)):
        try:
            return Path(source).read_bytes()
        except OSError as exc:
            raise ExcelLoadError("The Excel workbook could not be read.") from exc
    getvalue = getattr(source, "getvalue", None)
    if callable(getvalue):
        try:
            return bytes(getvalue())
        except (TypeError, ValueError, OSError) as exc:
            raise ExcelLoadError("The Excel upload could not be read.") from exc

    original_position: int | None = None
    try:
        if hasattr(source, "tell"):
            original_position = source.tell()
        if hasattr(source, "seek"):
            source.seek(0)
        content = source.read()
        if original_position is not None and hasattr(source, "seek"):
            source.seek(original_position)
        return bytes(content)
    except (AttributeError, TypeError, ValueError, OSError) as exc:
        raise ExcelLoadError("The Excel upload could not be read.") from exc


def _validate_xlsx_package(content: bytes) -> None:
    """Verify that captured content is a complete XLSX ZIP package."""
    if not isinstance(content, bytes) or not content:
        raise ExcelLoadError("The Excel upload did not contain valid bytes.")
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            corrupted_member = archive.testzip()
    except (BadZipFile, OSError, ValueError) as exc:
        raise ExcelLoadError("The Excel upload is not a valid ZIP package.") from exc
    required = {"[Content_Types].xml", "xl/workbook.xml"}
    if corrupted_member is not None or not required.issubset(names):
        raise ExcelLoadError("The Excel upload is not a complete XLSX package.")


def list_excel_sheets(source: bytes | bytearray | str | Path | BinaryIO) -> list[str]:
    """Read worksheet names from a private buffer without merging worksheets."""
    content = _excel_content(source)
    _validate_xlsx_package(content)
    try:
        with pd.ExcelFile(BytesIO(content), engine="openpyxl") as workbook:
            sheets = list(workbook.sheet_names)
    except ImportError as exc:
        logger.exception("XLSX dependency failure during workbook inspection")
        raise ExcelDependencyError("The Excel parsing dependency is unavailable.") from exc
    except Exception as exc:
        logger.exception(
            "XLSX workbook inspection failed (exception=%s, bytes=%d, zip_signature=%s)",
            type(exc).__name__,
            len(content),
            content[:4] == b"PK\x03\x04",
        )
        raise ExcelLoadError("The Excel workbook could not be opened.") from exc
    if not sheets:
        raise ExcelLoadError("The Excel workbook contains no worksheets.")
    return sheets


def load_excel_sheet(
    source: bytes | bytearray | str | Path | BinaryIO,
    sheet_name: str,
) -> pd.DataFrame:
    """Load one selected worksheet from a new private buffer."""
    content = _excel_content(source)
    _validate_xlsx_package(content)
    try:
        dataframe = pd.read_excel(BytesIO(content), sheet_name=sheet_name, engine="openpyxl")
    except ImportError as exc:
        logger.exception("XLSX dependency failure during worksheet loading")
        raise ExcelDependencyError("The Excel parsing dependency is unavailable.") from exc
    except Exception as exc:
        logger.exception(
            "XLSX worksheet loading failed (exception=%s, bytes=%d, sheet=%r)",
            type(exc).__name__,
            len(content),
            sheet_name,
        )
        raise ExcelLoadError("The selected worksheet could not be read.") from exc
    return _validate_dataframe(dataframe, excel=True)


def build_source_metadata(
    dataframe: pd.DataFrame,
    *,
    file_name: str,
    file_size_bytes: int,
    worksheet: str | None = None,
) -> DatasetSourceMetadata:
    """Build deterministic display metadata for the loaded source."""
    return DatasetSourceMetadata(
        file_name=file_name,
        file_type=detect_file_type(file_name),
        file_size_bytes=int(file_size_bytes),
        worksheet=worksheet,
        rows=len(dataframe),
        columns=len(dataframe.columns),
    )
