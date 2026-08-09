import inspect

import pandas as pd
from sklearn.datasets import make_classification

from src.evaluation import compare_models_on_validation, comparison_table
from src.models import train_baseline_models
from src.splitting import stratified_train_validation_test_split


def _split_data():
    X_array, y_array = make_classification(
        n_samples=240,
        n_features=6,
        n_informative=4,
        weights=[0.85, 0.15],
        random_state=11,
    )
    dataframe = pd.DataFrame(X_array, columns=[f"x{index}" for index in range(6)])
    dataframe["target"] = y_array
    return stratified_train_validation_test_split(dataframe, "target")


def test_validation_metrics_and_comparison_table() -> None:
    splits = _split_data()
    models = train_baseline_models(splits.X_train, splits.y_train)
    results = compare_models_on_validation(models, splits.X_validation, splits.y_validation)
    table = comparison_table(results)

    assert set(results) == {"Logistic Regression", "Random Forest"}
    assert list(table.columns) == [
        "Model",
        "PR-AUC",
        "ROC-AUC",
        "Precision @ 0.5",
        "Recall @ 0.5",
        "F1 @ 0.5",
        "False positives",
        "False negatives",
    ]
    for result in results.values():
        assert result.confusion_matrix.shape == (2, 2)
        assert all(0.0 <= metric <= 1.0 for metric in (result.roc_auc, result.pr_auc, result.precision, result.recall, result.f1))


def test_comparison_interface_cannot_receive_final_test_data() -> None:
    parameters = inspect.signature(compare_models_on_validation).parameters
    assert set(parameters) == {"models", "X_validation", "y_validation"}

    splits = _split_data()
    original_test_features = splits.X_test.copy(deep=True)
    original_test_target = splits.y_test.copy(deep=True)
    models = train_baseline_models(splits.X_train, splits.y_train)
    compare_models_on_validation(models, splits.X_validation, splits.y_validation)
    pd.testing.assert_frame_equal(splits.X_test, original_test_features)
    pd.testing.assert_series_equal(splits.y_test, original_test_target)
