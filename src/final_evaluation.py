"""One-shot evaluation of a validation-locked model on the final test set."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline


@dataclass(frozen=True)
class FinalTestResult:
    """Final metrics at the model and threshold locked during validation."""

    model_name: str
    threshold: float
    positive_class: object
    precision: float
    recall: float
    f1: float
    roc_auc: float
    pr_auc: float
    accuracy: float
    confusion_matrix: np.ndarray
    true_negatives: int
    false_positives: int
    false_negatives: int
    true_positives: int
    test_rows: int


def evaluate_locked_model_on_test(
    trained_models: dict[str, Pipeline],
    locked_model_name: str,
    locked_threshold: float,
    X_test: pd.DataFrame,
    y_test: pd.Series,
) -> FinalTestResult:
    """Evaluate exactly the supplied model and threshold without selection or optimization."""
    if locked_model_name not in trained_models:
        raise KeyError(f"Locked model is unavailable: {locked_model_name}")
    if not 0 <= locked_threshold <= 1:
        raise ValueError("Locked threshold must be between 0 and 1.")

    model = trained_models[locked_model_name]
    classifier = model.named_steps["classifier"]
    negative_class, positive_class = classifier.classes_
    probabilities = model.predict_proba(X_test)[:, 1]
    binary_truth = (np.asarray(y_test) == positive_class).astype(int)
    predictions = (probabilities >= locked_threshold).astype(int)
    matrix = confusion_matrix(binary_truth, predictions, labels=[0, 1])
    tn, fp, fn, tp = (int(value) for value in matrix.ravel())

    return FinalTestResult(
        model_name=locked_model_name,
        threshold=float(locked_threshold),
        positive_class=positive_class,
        precision=float(precision_score(binary_truth, predictions, zero_division=0)),
        recall=float(recall_score(binary_truth, predictions, zero_division=0)),
        f1=float(f1_score(binary_truth, predictions, zero_division=0)),
        roc_auc=float(roc_auc_score(binary_truth, probabilities)),
        pr_auc=float(average_precision_score(binary_truth, probabilities)),
        accuracy=float(accuracy_score(binary_truth, predictions)),
        confusion_matrix=matrix,
        true_negatives=tn,
        false_positives=fp,
        false_negatives=fn,
        true_positives=tp,
        test_rows=len(y_test),
    )
