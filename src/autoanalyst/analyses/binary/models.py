"""Fixed V2.0 binary model catalog."""

from __future__ import annotations

from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .preprocess import build_preprocessor


def build_models(
    numeric: list[str], categorical: list[str], *, seed: int, class_weight_policy: str
) -> dict[str, Pipeline]:
    if class_weight_policy not in {"none", "balanced"}:
        raise ValueError("class_weight_policy must be 'none' or 'balanced'")
    class_weight = None if class_weight_policy == "none" else "balanced"
    return {
        "dummy": Pipeline(
            [
                ("preprocess", build_preprocessor(numeric, categorical, scale_numeric=False)),
                ("model", DummyClassifier(strategy="prior", random_state=seed)),
            ]
        ),
        "logistic_regression": Pipeline(
            [
                ("preprocess", build_preprocessor(numeric, categorical, scale_numeric=True)),
                (
                    "model",
                    LogisticRegression(
                        penalty="l2",
                        C=1.0,
                        solver="lbfgs",
                        max_iter=1000,
                        random_state=seed,
                        class_weight=class_weight,
                    ),
                ),
            ]
        ),
        "random_forest": Pipeline(
            [
                ("preprocess", build_preprocessor(numeric, categorical, scale_numeric=False)),
                (
                    "model",
                    RandomForestClassifier(
                        n_estimators=100,
                        max_depth=12,
                        min_samples_leaf=2,
                        max_features="sqrt",
                        random_state=seed,
                        n_jobs=1,
                        class_weight=class_weight,
                    ),
                ),
            ]
        ),
    }
