"""Explicit V2 import limits used by ingestion and UI planning."""

from __future__ import annotations

from ..domain.errors import ResourceError


MIB = 1024 * 1024
CSV_MAX_BYTES = 256 * MIB
XLSX_MAX_BYTES = 64 * MIB
IMPORT_BATCH_MAX_BYTES = 512 * MIB


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
