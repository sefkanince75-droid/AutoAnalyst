"""Validation-only evaluation for binary classifiers."""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.pipeline import Pipeline


@dataclass(frozen=True)
class ValidationMetrics:
    """Metrics and curve coordinates calculated at the default 0.5 threshold."""

    model_name: str
    positive_class: object
    roc_auc: float
    pr_auc: float
    precision: float
    recall: float
    f1: float
    confusion_matrix: np.ndarray
    false_positive_rate: np.ndarray
    true_positive_rate: np.ndarray
    curve_precision: np.ndarray
    curve_recall: np.ndarray
    binary_truth: np.ndarray
    probabilities: np.ndarray


def evaluate_model_on_validation(
    model_name: str,
    model: Pipeline,
    X_validation: pd.DataFrame,
    y_validation: pd.Series,
) -> ValidationMetrics:
    """Evaluate one fitted model solely against validation data."""
    classifier = model.named_steps["classifier"]
    negative_class, positive_class = classifier.classes_
    probabilities = model.predict_proba(X_validation)[:, 1]
    predictions = np.where(probabilities >= 0.5, positive_class, negative_class)
    binary_truth = (np.asarray(y_validation) == positive_class).astype(int)
    binary_predictions = (predictions == positive_class).astype(int)

    fpr, tpr, _ = roc_curve(binary_truth, probabilities)
    curve_precision, curve_recall, _ = precision_recall_curve(binary_truth, probabilities)
    matrix = confusion_matrix(binary_truth, binary_predictions, labels=[0, 1])
    return ValidationMetrics(
        model_name=model_name,
        positive_class=positive_class,
        roc_auc=float(roc_auc_score(binary_truth, probabilities)),
        pr_auc=float(average_precision_score(binary_truth, probabilities)),
        precision=float(precision_score(binary_truth, binary_predictions, zero_division=0)),
        recall=float(recall_score(binary_truth, binary_predictions, zero_division=0)),
        f1=float(f1_score(binary_truth, binary_predictions, zero_division=0)),
        confusion_matrix=matrix,
        false_positive_rate=fpr,
        true_positive_rate=tpr,
        curve_precision=curve_precision,
        curve_recall=curve_recall,
        binary_truth=binary_truth,
        probabilities=probabilities,
    )


def compare_models_on_validation(
    models: dict[str, Pipeline],
    X_validation: pd.DataFrame,
    y_validation: pd.Series,
) -> dict[str, ValidationMetrics]:
    """Evaluate fitted models through a validation-only interface."""
    return {
        name: evaluate_model_on_validation(name, model, X_validation, y_validation)
        for name, model in models.items()
    }


def comparison_table(results: dict[str, ValidationMetrics]) -> pd.DataFrame:
    """Create a compact validation comparison table without ranking by accuracy."""
    rows = []
    for result in results.values():
        tn, fp, fn, tp = result.confusion_matrix.ravel()
        rows.append(
            {
                "Model": result.model_name,
                "PR-AUC": result.pr_auc,
                "ROC-AUC": result.roc_auc,
                "Precision @ 0.5": result.precision,
                "Recall @ 0.5": result.recall,
                "F1 @ 0.5": result.f1,
                "False positives": int(fp),
                "False negatives": int(fn),
            }
        )
    return pd.DataFrame(rows).sort_values("PR-AUC", ascending=False).reset_index(drop=True)


def plot_roc_curves(results: dict[str, ValidationMetrics], text: dict[str, str] | None = None) -> Figure:
    """Plot validation ROC curves for all baseline models."""
    labels = text or {}
    figure, axis = plt.subplots(figsize=(7, 5))
    for result in results.values():
        axis.plot(
            result.false_positive_rate,
            result.true_positive_rate,
            label=f"{result.model_name} (AUC={result.roc_auc:.3f})",
        )
    axis.plot([0, 1], [0, 1], linestyle="--", color="gray", label=labels.get("random", "Random baseline"))
    axis.set(
        xlabel=labels.get("fpr", "False positive rate"),
        ylabel=labels.get("tpr", "True positive rate"),
        title=labels.get("title", "Validation ROC curves"),
    )
    axis.legend()
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure


def plot_precision_recall_curves(
    results: dict[str, ValidationMetrics],
    positive_prevalence: float,
    text: dict[str, str] | None = None,
) -> Figure:
    """Plot validation precision-recall curves and the prevalence baseline."""
    labels = text or {}
    figure, axis = plt.subplots(figsize=(7, 5))
    for result in results.values():
        axis.plot(
            result.curve_recall,
            result.curve_precision,
            label=f"{result.model_name} (AP={result.pr_auc:.3f})",
        )
    axis.axhline(positive_prevalence, linestyle="--", color="gray", label=labels.get("baseline", "Prevalence baseline"))
    axis.set(
        xlabel="Recall",
        ylabel="Precision",
        title=labels.get("title", "Validation precision-recall curves"),
    )
    axis.legend()
    axis.grid(alpha=0.2)
    figure.tight_layout()
    return figure
