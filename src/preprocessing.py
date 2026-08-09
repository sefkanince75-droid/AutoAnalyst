"""Training-only preprocessing for mixed tabular features."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


@dataclass(frozen=True)
class FittedPreprocessor:
    """A fitted transformer and the feature roles learned from training data."""

    transformer: ColumnTransformer
    numerical_columns: list[str]
    categorical_columns: list[str]


def infer_feature_types(X_train: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Infer numerical and categorical columns from training data dtypes only."""
    numerical = [
        str(column)
        for column in X_train.columns
        if is_numeric_dtype(X_train[column]) and not is_bool_dtype(X_train[column])
    ]
    categorical = [str(column) for column in X_train.columns if column not in numerical]
    return numerical, categorical


def build_preprocessor(
    numerical_columns: list[str],
    categorical_columns: list[str],
    *,
    scale_numeric: bool = True,
) -> ColumnTransformer:
    """Build an unfitted leakage-safe mixed-feature transformer."""
    numeric_steps: list[tuple[str, object]] = [("imputer", SimpleImputer(strategy="median"))]
    if scale_numeric:
        numeric_steps.append(("scaler", StandardScaler()))

    transformers: list[tuple[str, object, list[str]]] = []
    if numerical_columns:
        transformers.append(("numeric", Pipeline(numeric_steps), numerical_columns))
    if categorical_columns:
        categorical_pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("encoder", OneHotEncoder(handle_unknown="ignore")),
            ]
        )
        transformers.append(("categorical", categorical_pipeline, categorical_columns))
    if not transformers:
        raise ValueError("At least one feature column is required for preprocessing.")

    return ColumnTransformer(transformers=transformers, remainder="drop")


def fit_preprocessor(X_train: pd.DataFrame, *, scale_numeric: bool = True) -> FittedPreprocessor:
    """Build and fit preprocessing exclusively on the training partition."""
    numerical, categorical = infer_feature_types(X_train)
    transformer = build_preprocessor(numerical, categorical, scale_numeric=scale_numeric)
    transformer.fit(X_train)
    return FittedPreprocessor(transformer, numerical, categorical)
