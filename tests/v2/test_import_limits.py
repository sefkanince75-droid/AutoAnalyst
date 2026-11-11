from __future__ import annotations

import pytest

from autoanalyst.data.limits import (
    CSV_MAX_BYTES,
    IMPORT_BATCH_MAX_BYTES,
    XLSX_MAX_BYTES,
    enforce_batch_size,
    enforce_source_size,
)
from autoanalyst.domain.errors import ResourceError


def test_csv_and_xlsx_limits_accept_exact_ceiling() -> None:
    enforce_source_size(CSV_MAX_BYTES, source_format="csv")
    enforce_source_size(XLSX_MAX_BYTES, source_format="xlsx")


def test_source_limit_reports_structured_resource_error() -> None:
    with pytest.raises(ResourceError) as too_large:
        enforce_source_size(XLSX_MAX_BYTES + 1, source_format="xlsx")
    assert too_large.value.context["reason"] == "source_file_too_large"
    assert too_large.value.context["max_bytes"] == XLSX_MAX_BYTES


def test_batch_limit_rejects_negative_and_oversized_totals() -> None:
    with pytest.raises(ValueError, match="negative"):
        enforce_batch_size([10, -1])
    enforce_batch_size([IMPORT_BATCH_MAX_BYTES // 2, IMPORT_BATCH_MAX_BYTES // 2])
    with pytest.raises(ResourceError) as too_large:
        enforce_batch_size([IMPORT_BATCH_MAX_BYTES, 1])
    assert too_large.value.context["reason"] == "import_batch_too_large"
