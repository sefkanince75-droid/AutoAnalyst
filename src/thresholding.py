"""Validation-only decision-threshold optimization."""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.figure import Figure
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
)

from src.evaluation import ValidationMetrics


@dataclass(frozen=True)
class ThresholdResult:
    """Best feasible operating point and its validation tradeoff curve."""

    model_name: str
    is_feasible: bool
    threshold: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    confusion_matrix: np.ndarray | None
    thresholds: np.ndarray
    precisions: np.ndarray
    recalls: np.ndarray


@dataclass(frozen=True)
class Recommendation:
    """Recommended feasible model for a minimum-recall objective."""

    model_name: str
    threshold: float
    precision: float
    recall: float
    pr_auc: float


def optimize_threshold(
    model_name: str,
    binary_truth: np.ndarray,
    probabilities: np.ndarray,
    minimum_recall: float,
    *,
    candidate_thresholds: np.ndarray | None = None,
) -> ThresholdResult:
    """Maximize validation precision subject to the requested minimum recall."""
    truth = np.asarray(binary_truth, dtype=int)
    scores = np.asarray(probabilities, dtype=float)
    if truth.shape != scores.shape:
        raise ValueError("Targets and probabilities must have the same shape.")
    if not 0 <= minimum_recall <= 1:
        raise ValueError("minimum_recall must be between 0 and 1.")
    if len(truth) == 0 or truth.sum() == 0:
        return ThresholdResult(model_name, False, None, None, None, None, None, np.array([]), np.array([]), np.array([]))

    if candidate_thresholds is None:
        curve_precision, curve_recall, thresholds = precision_recall_curve(truth, scores)
        precision_array = curve_precision[:-1]
        recall_array = curve_recall[:-1]
    else:
        thresholds = np.unique(candidate_thresholds)
        thresholds = thresholds[(thresholds >= 0) & (thresholds <= 1)]
        precision_array = np.asarray(
            [precision_score(truth, scores >= threshold, zero_division=0) for threshold in thresholds],
            dtype=float,
        )
        recall_array = np.asarray(
            [recall_score(truth, scores >= threshold, zero_division=0) for threshold in thresholds],
            dtype=float,
        )
    feasible_indices = np.flatnonzero(recall_array >= minimum_recall)
    if len(feasible_indices) == 0:
        return ThresholdResult(
            model_name, False, None, None, None, None, None, thresholds, precision_array, recall_array
        )

    best_index = max(
        feasible_indices,
        key=lambda index: (precision_array[index], recall_array[index], thresholds[index]),
    )
    best_predictions = (scores >= thresholds[best_index]).astype(int)
    return ThresholdResult(
        model_name=model_name,
        is_feasible=True,
        threshold=float(thresholds[best_index]),
        precision=float(precision_array[best_index]),
        recall=float(recall_array[best_index]),
        f1=float(f1_score(truth, best_predictions, zero_division=0)),
        confusion_matrix=confusion_matrix(truth, best_predictions, labels=[0, 1]),
        thresholds=thresholds,
        precisions=precision_array,
        recalls=recall_array,
    )


def optimize_model_thresholds(
    validation_results: dict[str, ValidationMetrics],
    minimum_recall: float,
) -> dict[str, ThresholdResult]:
    """Optimize every model using stored validation targets and probabilities only."""
    return {
        name: optimize_threshold(
            name,
            result.binary_truth,
            result.probabilities,
            minimum_recall,
        )
        for name, result in validation_results.items()
    }


def recommend_model(
    threshold_results: dict[str, ThresholdResult],
    validation_results: dict[str, ValidationMetrics],
) -> Recommendation | None:
    """Recommend highest feasible precision, with PR-AUC as the first tie-breaker."""
    feasible = [result for result in threshold_results.values() if result.is_feasible]
    if not feasible:
        return None
    best = max(
        feasible,
        key=lambda result: (
            float(result.precision),
            validation_results[result.model_name].pr_auc,
            float(result.recall),
        ),
    )
    return Recommendation(
        model_name=best.model_name,
        threshold=float(best.threshold),
        precision=float(best.precision),
        recall=float(best.recall),
        pr_auc=validation_results[best.model_name].pr_auc,
    )


def plot_threshold_tradeoff(
    result: ThresholdResult,
    minimum_recall: float,
    text: dict[str, str] | None = None,
) -> Figure:
    """Plot validation precision and recall across available thresholds."""
    labels = text or {}
    figure, axis = plt.subplots(figsize=(7, 4.5))
    axis.plot(result.thresholds, result.precisions, label="Precision")
    axis.plot(result.thresholds, result.recalls, label="Recall")
    axis.axhline(minimum_recall, color="gray", linestyle="--", label=labels.get("minimum", "Minimum Recall"))
    if result.is_feasible:
        axis.axvline(float(result.threshold), color="black", linestyle=":", label=labels.get("selected", "Selected threshold"))
    axis.set(
        xlabel=labels.get("threshold", "Threshold"),
        ylabel=labels.get("metric", "Metric value"),
        ylim=(0, 1.02),
        title=result.model_name,
    )
    axis.grid(alpha=0.2)
    axis.legend()
    figure.tight_layout()
    return figure
