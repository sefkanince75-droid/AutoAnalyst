"""Deterministic target and dataset diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype


@dataclass(frozen=True)
class TargetValidation:
    """Result of validating a user-selected binary target."""

    is_valid: bool
    message: str
    class_summary: pd.DataFrame
    translation_key: str = ""
    message_values: dict[str, object] | None = None


@dataclass(frozen=True)
class DatasetDiagnostics:
    """Compact deterministic diagnosis of a binary-classification dataset."""

    numerical_columns: list[str]
    categorical_columns: list[str]
    columns_with_missing: list[str]
    constant_columns: list[str]
    high_cardinality_columns: list[str]
    outlier_counts: dict[str, int]
    minority_class_ratio: float
    class_imbalance: bool
    severe_class_imbalance: bool
    scaling_likely_useful: bool


def binary_target_candidates(dataframe: pd.DataFrame) -> list[str]:
    """Return columns containing exactly two distinct non-null values."""
    return [str(column) for column in dataframe.columns if dataframe[column].nunique(dropna=True) == 2]


def validate_binary_target(target: pd.Series, minimum_class_size: int = 3) -> TargetValidation:
    """Validate that a target has exactly two non-missing, splittable classes."""
    if target.isna().any():
        return TargetValidation(False, "The target contains missing values. Choose or clean another target.", pd.DataFrame(), "target_missing")

    counts = target.value_counts(dropna=False)
    summary = pd.DataFrame({"count": counts, "percentage": (counts / len(target) * 100).round(2)})
    summary.index.name = "class"

    if len(counts) != 2:
        return TargetValidation(False, f"Binary classification requires exactly two target classes; found {len(counts)}.", summary, "target_not_binary", {"count": len(counts)})
    if int(counts.min()) < minimum_class_size:
        return TargetValidation(
            False,
            f"Each target class needs at least {minimum_class_size} observations for splitting; the smallest has {int(counts.min())}.",
            summary,
            "target_too_small",
            {"minimum": minimum_class_size, "smallest": int(counts.min())},
        )
    return TargetValidation(True, "The selected target is suitable for the binary-classification workflow.", summary, "target_valid")


def detect_imbalance(target: pd.Series, severe_threshold: float = 0.10, imbalance_threshold: float = 0.30) -> tuple[float, bool, bool]:
    """Return minority ratio and deterministic imbalance flags."""
    proportions = target.value_counts(normalize=True)
    minority_ratio = float(proportions.min())
    return minority_ratio, minority_ratio < imbalance_threshold, minority_ratio < severe_threshold


def _outlier_counts(dataframe: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    """Count extreme values using the robust 3×IQR rule."""
    results: dict[str, int] = {}
    for column in columns:
        values = dataframe[column].dropna()
        if values.empty:
            continue
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr <= 0:
            continue
        count = int(((values < q1 - 3 * iqr) | (values > q3 + 3 * iqr)).sum())
        if count:
            results[column] = count
    return results


def diagnose_dataset(dataframe: pd.DataFrame, target_column: str) -> DatasetDiagnostics:
    """Inspect feature types, quality issues, imbalance, and preprocessing hints."""
    if target_column not in dataframe.columns:
        raise KeyError(f"Unknown target column: {target_column}")

    features = dataframe.drop(columns=[target_column])
    numerical = [str(c) for c in features.columns if is_numeric_dtype(features[c]) and not is_bool_dtype(features[c])]
    categorical = [str(c) for c in features.columns if c not in numerical]
    missing = [str(c) for c in features.columns if features[c].isna().any()]
    constant = [str(c) for c in features.columns if features[c].nunique(dropna=False) <= 1]
    high_cardinality = [
        c for c in categorical
        if features[c].nunique(dropna=True) > max(20, int(len(features) * 0.20))
    ]
    minority_ratio, imbalanced, severe = detect_imbalance(dataframe[target_column])

    scaling_useful = False
    if numerical:
        nonzero_std = features[numerical].std(numeric_only=True).dropna()
        scaling_useful = len(nonzero_std) > 1 and float(nonzero_std.max() / nonzero_std.min()) > 10

    return DatasetDiagnostics(
        numerical_columns=numerical,
        categorical_columns=categorical,
        columns_with_missing=missing,
        constant_columns=constant,
        high_cardinality_columns=high_cardinality,
        outlier_counts=_outlier_counts(features, numerical),
        minority_class_ratio=minority_ratio,
        class_imbalance=imbalanced,
        severe_class_imbalance=severe,
        scaling_likely_useful=scaling_useful,
    )
