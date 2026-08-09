import inspect
from types import SimpleNamespace

import numpy as np

from src.thresholding import optimize_model_thresholds, optimize_threshold, recommend_model


def test_threshold_optimization_maximizes_precision_under_recall_constraint() -> None:
    truth = np.array([1, 1, 1, 1, 0, 0, 0, 0])
    probabilities = np.array([0.95, 0.80, 0.70, 0.40, 0.90, 0.30, 0.20, 0.10])
    result = optimize_threshold("model", truth, probabilities, minimum_recall=0.75)

    assert result.is_feasible
    assert result.recall >= 0.75
    feasible_precisions = result.precisions[result.recalls >= 0.75]
    assert result.precision == feasible_precisions.max()
    assert result.confusion_matrix.shape == (2, 2)


def test_threshold_optimization_reports_no_valid_candidate() -> None:
    truth = np.array([1, 1, 0, 0])
    probabilities = np.array([0.60, 0.55, 0.20, 0.10])
    result = optimize_threshold(
        "model",
        truth,
        probabilities,
        minimum_recall=0.90,
        candidate_thresholds=np.array([0.70, 0.80]),
    )

    assert not result.is_feasible
    assert result.threshold is None
    assert result.confusion_matrix is None


def test_recommendation_uses_precision_then_pr_auc() -> None:
    truth = np.array([1, 1, 0, 0])
    first = optimize_threshold("first", truth, np.array([0.9, 0.8, 0.2, 0.1]), 0.5)
    second = optimize_threshold("second", truth, np.array([0.8, 0.7, 0.2, 0.1]), 0.5)
    validation = {
        "first": SimpleNamespace(pr_auc=0.80),
        "second": SimpleNamespace(pr_auc=0.90),
    }
    recommendation = recommend_model({"first": first, "second": second}, validation)

    assert recommendation is not None
    assert recommendation.model_name == "second"
    assert recommendation.pr_auc == 0.90


def test_threshold_comparison_interface_has_no_final_test_inputs() -> None:
    parameters = inspect.signature(optimize_model_thresholds).parameters
    assert set(parameters) == {"validation_results", "minimum_recall"}
