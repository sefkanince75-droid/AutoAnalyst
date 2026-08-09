import inspect
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.datasets import make_classification
from sklearn.metrics import confusion_matrix

from src.final_evaluation import evaluate_locked_model_on_test
from src.models import train_baseline_models
from src.splitting import stratified_train_validation_test_split


def _trained_workflow():
    X_array, y_array = make_classification(
        n_samples=240,
        n_features=6,
        n_informative=4,
        weights=[0.80, 0.20],
        random_state=19,
    )
    dataframe = pd.DataFrame(X_array, columns=[f"x{index}" for index in range(6)])
    dataframe["target"] = y_array
    splits = stratified_train_validation_test_split(dataframe, "target")
    models = train_baseline_models(splits.X_train, splits.y_train)
    return models, splits


def test_final_evaluation_uses_locked_model_and_threshold() -> None:
    models, splits = _trained_workflow()
    locked_threshold = 0.73
    result = evaluate_locked_model_on_test(
        models,
        "Logistic Regression",
        locked_threshold,
        splits.X_test,
        splits.y_test,
    )

    probabilities = models["Logistic Regression"].predict_proba(splits.X_test)[:, 1]
    expected = confusion_matrix(splits.y_test, probabilities >= locked_threshold, labels=[0, 1])
    assert result.model_name == "Logistic Regression"
    assert result.threshold == locked_threshold
    np.testing.assert_array_equal(result.confusion_matrix, expected)


def test_final_evaluation_predicts_once_and_does_not_recalculate_threshold() -> None:
    models, splits = _trained_workflow()
    selected_model = models["Random Forest"]
    with patch.object(selected_model, "predict_proba", wraps=selected_model.predict_proba) as predictor:
        result = evaluate_locked_model_on_test(
            models,
            "Random Forest",
            0.61,
            splits.X_test,
            splits.y_test,
        )
    predictor.assert_called_once_with(splits.X_test)
    assert result.threshold == 0.61
    assert "optimize" not in inspect.getsource(evaluate_locked_model_on_test)


def test_final_metrics_are_complete_and_bounded() -> None:
    models, splits = _trained_workflow()
    result = evaluate_locked_model_on_test(
        models,
        "Logistic Regression",
        0.5,
        splits.X_test,
        splits.y_test,
    )
    assert result.test_rows == len(splits.y_test)
    assert result.true_positives + result.true_negatives + result.false_positives + result.false_negatives == result.test_rows
    assert all(
        0 <= metric <= 1
        for metric in (result.precision, result.recall, result.f1, result.roc_auc, result.pr_auc, result.accuracy)
    )
