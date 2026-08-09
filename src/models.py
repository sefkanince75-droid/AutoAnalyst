"""Baseline binary-classification model pipelines."""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from src.preprocessing import build_preprocessor, infer_feature_types


RANDOM_STATE = 42


def build_baseline_models(X_train: pd.DataFrame) -> dict[str, Pipeline]:
    """Build unfitted baseline pipelines using feature roles from training data."""
    numerical, categorical = infer_feature_types(X_train)
    return {
        "Logistic Regression": Pipeline(
            [
                ("preprocessor", build_preprocessor(numerical, categorical, scale_numeric=True)),
                (
                    "classifier",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=500,
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        ),
        "Random Forest": Pipeline(
            [
                ("preprocessor", build_preprocessor(numerical, categorical, scale_numeric=False)),
                (
                    "classifier",
                    RandomForestClassifier(
                        n_estimators=100,
                        max_depth=12,
                        min_samples_leaf=2,
                        max_features="sqrt",
                        class_weight="balanced_subsample",
                        n_jobs=-1,
                        random_state=RANDOM_STATE,
                    ),
                ),
            ]
        ),
    }


def train_baseline_models(X_train: pd.DataFrame, y_train: pd.Series) -> dict[str, Pipeline]:
    """Fit both baseline pipelines using only the training partition."""
    models = build_baseline_models(X_train)
    for model in models.values():
        model.fit(X_train, y_train)
    return models
