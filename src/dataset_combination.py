"""Deterministic, row-wise combination of compatible tabular sources."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

import pandas as pd
from pandas.api.types import (
    is_bool_dtype,
    is_datetime64_any_dtype,
    is_numeric_dtype,
    is_string_dtype,
)


@dataclass(frozen=True)
class DatasetPart:
    """One loaded file or selected Excel worksheet."""

    file_name: str
    content_digest: str
    file_size_bytes: int
    dataframe: pd.DataFrame
    worksheet: str | None = None


@dataclass(frozen=True)
class SchemaDifference:
    """Schema differences relative to the first selected source."""

    file_name: str
    missing_columns: tuple[str, ...]
    additional_columns: tuple[str, ...]
    incompatible_types: tuple[tuple[str, str, str], ...]

    @property
    def is_compatible(self) -> bool:
        return not (self.missing_columns or self.additional_columns or self.incompatible_types)


@dataclass(frozen=True)
class SchemaReport:
    """Compatibility report for a sequence of source parts."""

    reference_file: str
    reference_columns: tuple[str, ...]
    differences: tuple[SchemaDifference, ...]

    @property
    def is_compatible(self) -> bool:
        return all(difference.is_compatible for difference in self.differences)


@dataclass(frozen=True)
class CombinedDataset:
    """Combined frame plus generated source-column metadata."""

    dataframe: pd.DataFrame
    source_column: str | None
    source_count: int


def content_digest(content: bytes) -> str:
    """Return a stable SHA-256 digest for uploaded content."""
    return sha256(content).hexdigest()


def _dtype_family(dtype: object) -> str:
    if is_bool_dtype(dtype):
        return "boolean"
    if is_numeric_dtype(dtype):
        return "numeric"
    if is_datetime64_any_dtype(dtype):
        return "datetime"
    if is_string_dtype(dtype) or str(dtype) in {"object", "category"}:
        return "categorical"
    return str(dtype)


def validate_schema_compatibility(parts: list[DatasetPart]) -> SchemaReport:
    """Compare names and obvious dtype families, ignoring column order."""
    if not parts:
        raise ValueError("At least one dataset part is required.")
    reference = parts[0]
    reference_columns = tuple(str(column) for column in reference.dataframe.columns)
    reference_set = set(reference_columns)
    reference_families = {
        str(column): _dtype_family(reference.dataframe[column].dtype)
        for column in reference.dataframe.columns
    }
    differences: list[SchemaDifference] = []
    for part in parts:
        columns = tuple(str(column) for column in part.dataframe.columns)
        column_set = set(columns)
        missing = tuple(sorted(reference_set - column_set))
        additional = tuple(sorted(column_set - reference_set))
        incompatible: list[tuple[str, str, str]] = []
        for column in sorted(reference_set & column_set):
            actual = _dtype_family(part.dataframe[column].dtype)
            expected = reference_families[column]
            if actual != expected:
                incompatible.append((column, expected, actual))
        differences.append(
            SchemaDifference(part.file_name, missing, additional, tuple(incompatible))
        )
    return SchemaReport(reference.file_name, reference_columns, tuple(differences))


def safe_source_column(existing_columns: list[str] | tuple[str, ...]) -> str:
    """Choose a deterministic metadata-column name without collisions."""
    existing = set(existing_columns)
    base = "__source_file"
    if base not in existing:
        return base
    suffix = 1
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


def combine_row_wise(parts: list[DatasetPart], *, add_source_column: bool = True) -> CombinedDataset:
    """Concatenate compatible parts after normalizing to reference column order."""
    report = validate_schema_compatibility(parts)
    if not report.is_compatible:
        raise ValueError("Dataset schemas are incompatible.")
    source_column = safe_source_column(report.reference_columns) if add_source_column else None
    frames: list[pd.DataFrame] = []
    for part in parts:
        frame = part.dataframe.loc[:, list(report.reference_columns)].copy()
        if source_column is not None:
            frame[source_column] = part.file_name
        frames.append(frame)
    combined = pd.concat(frames, axis=0, ignore_index=True, copy=False)
    return CombinedDataset(combined, source_column, len(parts))


def dataframe_memory_bytes(dataframe: pd.DataFrame) -> int:
    """Estimate in-memory DataFrame size using pandas deep memory accounting."""
    return int(dataframe.memory_usage(index=True, deep=True).sum())


def human_readable_bytes(size_bytes: int) -> str:
    """Format a byte count with binary units."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def dataset_identity(
    parts: list[DatasetPart],
    *,
    mode: str,
    add_source_column: bool,
) -> str:
    """Hash ordered sources, content, worksheets, mode, and source-column choice."""
    payload = {
        "mode": mode,
        "add_source_column": bool(add_source_column),
        "parts": [
            {
                "file_name": part.file_name,
                "digest": part.content_digest,
                "worksheet": part.worksheet,
            }
            for part in parts
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
