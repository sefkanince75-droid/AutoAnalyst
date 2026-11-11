"""Localized import feedback with diagnostics that exclude file contents and paths."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

import streamlit as st

from ..domain.codec import utc_now
from ..domain.errors import AutoAnalystError, ResourceError, SchemaError
from ..localization import I18N

REASONS = {
    "csv_has_no_rows": "empty",
    "xlsx_has_no_rows": "empty",
    "csv_header_parse_failed": "parse",
    "csv_parse_failed": "parse",
    "xlsx_open_failed": "parse",
    "xlsx_parse_failed": "parse",
    "source_file_too_large": "size",
    "source_read_failed": "storage",
}


def show_import_error(error: Exception, language: str, log_directory: Path) -> None:
    category = "unexpected"
    reason = "unexpected_import_failure"
    if isinstance(error, AutoAnalystError):
        candidate = str(error.context.get("reason", ""))
        if candidate in REASONS:
            category, reason = REASONS[candidate], candidate
        elif isinstance(error, SchemaError):
            category, reason = "schema", "invalid_import_schema"
        elif isinstance(error, ResourceError):
            category, reason = "size", "import_resource_limit"
    elif isinstance(error, OSError):
        category, reason = "storage", "import_storage_failure"

    reference = uuid4().hex[:12]
    entry = {
        "reference": reference,
        "created_at": utc_now().isoformat(),
        "reason": reason,
        "exception_type": type(error).__name__,
    }
    try:
        with (log_directory / "import-errors.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Could not write import diagnostic %s", reference)
    messages = I18N[language]
    st.error(
        messages["import_error." + category]
        + " "
        + messages["import_error.reference"].format(reference=reference)
    )
