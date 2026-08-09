"""Leakage-safe train, validation, and final-test splitting."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.model_selection import train_test_split


class SplitError(ValueError):
    """Raised when a dataset cannot support the required stratified split."""


@dataclass(frozen=True)
class DataSplits:
    """Feature and target partitions for the 70/15/15 workflow."""

    X_train: pd.DataFrame
    X_validation: pd.DataFrame
    X_test: pd.DataFrame
    y_train: pd.Series
    y_validation: pd.Series
    y_test: pd.Series


def stratified_train_validation_test_split(
    dataframe: pd.DataFrame,
    target_column: str,
    random_state: int = 42,
) -> DataSplits:
    """Create 70/15/15 partitions, preserving target ratios in both split stages."""
    if target_column not in dataframe.columns:
        raise SplitError(f"Unknown target column: {target_column}")

    X = dataframe.drop(columns=[target_column])
    y = dataframe[target_column]
    try:
        X_train, X_remainder, y_train, y_remainder = train_test_split(
            X,
            y,
            test_size=0.30,
            random_state=random_state,
            stratify=y,
        )
        X_validation, X_test, y_validation, y_test = train_test_split(
            X_remainder,
            y_remainder,
            test_size=0.50,
            random_state=random_state,
            stratify=y_remainder,
        )
    except ValueError as exc:
        raise SplitError(
            "The class counts are too small for a stratified 70/15/15 split. "
            "Provide more observations for each target class."
        ) from exc

    return DataSplits(X_train, X_validation, X_test, y_train, y_validation, y_test)


def split_class_summary(splits: DataSplits) -> pd.DataFrame:
    """Return row counts and within-split class percentages for display."""
    rows: list[dict[str, object]] = []
    for split_name, target in (
        ("Train", splits.y_train),
        ("Validation", splits.y_validation),
        ("Final test", splits.y_test),
    ):
        counts = target.value_counts(dropna=False)
        for class_value, count in counts.items():
            rows.append(
                {
                    "split": split_name,
                    "rows": len(target),
                    "class": str(class_value),
                    "class count": int(count),
                    "class percentage": round(float(count / len(target) * 100), 2),
                }
            )
    return pd.DataFrame(rows)
