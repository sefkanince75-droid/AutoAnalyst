"""Bounded, advisory import samples that never publish dataset versions."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

IMPORT_PREVIEW_ROWS = 100


@dataclass(frozen=True, slots=True)
class ImportPreview:
    frame: pd.DataFrame
    has_more_rows: bool

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> ImportPreview:
        return cls(frame.head(IMPORT_PREVIEW_ROWS).copy(), len(frame) > IMPORT_PREVIEW_ROWS)
