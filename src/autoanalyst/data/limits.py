"""Explicit V2 import limits used by ingestion and UI planning."""

from __future__ import annotations

from ..domain.errors import ResourceError

MIB = 1024 * 1024
CSV_MAX_BYTES = 256 * MIB
XLSX_MAX_BYTES = 64 * MIB
IMPORT_BATCH_MAX_BYTES = 512 * MIB
MAX_ROWS = 1_000_000
MAX_COLUMNS = 500
MAX_CELLS = 5_000_000
XLSX_UNPACKED_MAX_BYTES = 256 * MIB


def enforce_shape(rows: int, columns: int) -> None:
    if rows > MAX_ROWS or columns > MAX_COLUMNS or rows * columns > MAX_CELLS:
        raise ResourceError(
            {
                "reason": "data_shape_limit",
                "rows": rows,
                "columns": columns,
                "max_rows": MAX_ROWS,
                "max_columns": MAX_COLUMNS,
                "max_cells": MAX_CELLS,
            }
        )


def enforce_free_disk(root, required: int) -> None:
    import shutil

    if shutil.disk_usage(root).free < required:
        raise ResourceError({"reason": "data_disk_space", "required_bytes": required})


def enforce_xlsx_archive(raw: bytes) -> None:
    from io import BytesIO
    from zipfile import BadZipFile, ZipFile

    from ..domain.errors import DataError

    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10_000
                or sum(e.file_size for e in entries) > XLSX_UNPACKED_MAX_BYTES
                or any(e.file_size > 128 * MIB for e in entries)
            ):
                raise ResourceError({"reason": "xlsx_expansion_limit"})
    except BadZipFile as exc:
        raise DataError({"reason": "xlsx_open_failed"}) from exc


def enforce_source_size(byte_size: int, *, source_format: str) -> None:
    if byte_size < 0:
        raise ValueError("byte_size cannot be negative")
    normalized = source_format.lower()
    if normalized == "csv":
        limit = CSV_MAX_BYTES
    elif normalized == "xlsx":
        limit = XLSX_MAX_BYTES
    else:
        raise ValueError(f"unsupported source format: {source_format}")
    if byte_size > limit:
        raise ResourceError(
            {
                "reason": "source_file_too_large",
                "source_format": normalized,
                "byte_size": byte_size,
                "max_bytes": limit,
            }
        )


def enforce_batch_size(byte_sizes: tuple[int, ...] | list[int]) -> None:
    if any(size < 0 for size in byte_sizes):
        raise ValueError("byte sizes cannot be negative")
    total = sum(byte_sizes)
    if total > IMPORT_BATCH_MAX_BYTES:
        raise ResourceError(
            {
                "reason": "import_batch_too_large",
                "byte_size": total,
                "max_bytes": IMPORT_BATCH_MAX_BYTES,
            }
        )
