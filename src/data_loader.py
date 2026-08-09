"""CSV ingestion utilities."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import pandas as pd


class CSVLoadError(ValueError):
    """Raised when an uploaded CSV cannot be safely loaded."""


def load_csv(source: str | Path | BinaryIO) -> pd.DataFrame:
    """Load a CSV source and reject empty or structurally invalid datasets."""
    try:
        dataframe = pd.read_csv(source)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError, OSError) as exc:
        raise CSVLoadError(f"Could not read this CSV: {exc}") from exc

    if dataframe.empty:
        raise CSVLoadError("The CSV contains no data rows.")
    if len(dataframe.columns) < 2:
        raise CSVLoadError("The dataset needs at least one feature column and one target column.")
    if dataframe.columns.duplicated().any():
        raise CSVLoadError("The CSV contains duplicate column names.")
    return dataframe
