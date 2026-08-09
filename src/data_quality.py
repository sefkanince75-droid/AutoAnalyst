"""Concrete, non-scored data-quality findings for UI review."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.diagnostics import DatasetDiagnostics


@dataclass(frozen=True)
class DataQualityFinding:
    """One deterministic quality observation with a localized message key."""

    severity: str
    message_key: str
    count: int
    columns: tuple[str, ...] = ()


def build_data_quality_findings(
    dataframe: pd.DataFrame,
    diagnostics: DatasetDiagnostics,
) -> list[DataQualityFinding]:
    """Summarize concrete findings without scoring or modifying data."""
    findings: list[DataQualityFinding] = []
    missing_count = int(dataframe.isna().sum().sum())
    duplicate_count = int(dataframe.duplicated().sum())
    if missing_count:
        findings.append(DataQualityFinding("warning", "quality_missing", missing_count, tuple(diagnostics.columns_with_missing)))
    if duplicate_count:
        findings.append(DataQualityFinding("warning", "quality_duplicates", duplicate_count))
    if diagnostics.constant_columns:
        findings.append(DataQualityFinding("issue", "quality_constants", len(diagnostics.constant_columns), tuple(diagnostics.constant_columns)))
    if diagnostics.high_cardinality_columns:
        findings.append(DataQualityFinding("warning", "quality_high_cardinality", len(diagnostics.high_cardinality_columns), tuple(diagnostics.high_cardinality_columns)))
    if diagnostics.possible_id_columns:
        findings.append(DataQualityFinding("warning", "quality_ids", len(diagnostics.possible_id_columns), tuple(diagnostics.possible_id_columns)))
    if diagnostics.outlier_counts:
        findings.append(DataQualityFinding("warning", "quality_outliers", len(diagnostics.outlier_counts), tuple(diagnostics.outlier_counts)))
    if diagnostics.severe_class_imbalance:
        findings.append(DataQualityFinding("issue", "quality_imbalance", 1))
    if not findings:
        findings.append(DataQualityFinding("ok", "quality_ok", 0))
    return findings
