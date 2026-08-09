from io import BytesIO

import joblib
import numpy as np
import pandas as pd
from sklearn.datasets import make_classification

from src.final_evaluation import FinalTestResult
from src.models import train_baseline_models
from src.reporting import ReportData, generate_markdown_report, serialize_model_package


def _final_result() -> FinalTestResult:
    return FinalTestResult(
        model_name="Logistic Regression",
        threshold=0.7,
        positive_class=1,
        precision=0.6,
        recall=0.75,
        f1=2 * 0.6 * 0.75 / (0.6 + 0.75),
        roc_auc=0.9,
        pr_auc=0.7,
        accuracy=0.85,
        confusion_matrix=np.array([[80, 10], [5, 15]]),
        true_negatives=80,
        false_positives=10,
        false_negatives=5,
        true_positives=15,
        test_rows=110,
    )


def _report_data() -> ReportData:
    return ReportData(
        dataset_name="sample.csv",
        dataset_rows=1000,
        dataset_columns=5,
        target_name="target",
        target_distribution=pd.DataFrame({"count": [900, 100], "percentage": [90.0, 10.0]}, index=[0, 1]),
        minority_ratio=0.1,
        severe_imbalance=True,
        train_rows=700,
        validation_rows=150,
        test_rows=150,
        numerical_features=3,
        categorical_features=1,
        baseline_comparison=pd.DataFrame({"Model": ["Logistic Regression"], "PR-AUC": [0.7]}),
        minimum_recall=0.85,
        threshold_comparison=pd.DataFrame({"Model": ["Logistic Regression"], "Selected threshold": [0.7]}),
        recommended_model="Logistic Regression",
        selected_threshold=0.7,
        final_result=_final_result(),
    )


def test_markdown_report_contains_complete_workflow() -> None:
    report = generate_markdown_report(_report_data(), "en")
    assert "Dataset overview" in report
    assert "Validation comparison" in report
    assert "Final-test performance" in report
    assert "Logistic Regression" in report
    assert "True positives" not in report  # Confusion values are represented in the matrix.
    assert "80" in report and "15" in report


def test_markdown_report_is_localized() -> None:
    english = generate_markdown_report(_report_data(), "en")
    turkish = generate_markdown_report(_report_data(), "tr")
    assert "Methodological limitations" in english
    assert "Yöntemsel sınırlamalar" in turkish
    assert "Analiz Raporu" in turkish
    assert english != turkish


def test_model_export_contains_reuse_metadata_but_no_raw_data() -> None:
    X_array, y_array = make_classification(n_samples=80, n_features=4, random_state=5)
    X = pd.DataFrame(X_array, columns=["a", "b", "c", "d"])
    y = pd.Series(y_array)
    model = train_baseline_models(X, y)["Logistic Regression"]
    payload = serialize_model_package(
        model,
        model_name="Logistic Regression",
        selected_threshold=0.7,
        target_column="target",
        positive_class=1,
        feature_columns=list(X.columns),
    )
    package = joblib.load(BytesIO(payload))

    assert package["selected_threshold"] == 0.7
    assert package["target_column"] == "target"
    assert package["feature_columns"] == list(X.columns)
    assert "pipeline" in package
    assert not {"dataframe", "X_train", "y_train", "raw_data"} & set(package)
