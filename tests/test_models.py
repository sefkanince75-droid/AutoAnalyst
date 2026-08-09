import pandas as pd
from sklearn.datasets import make_classification

from src.models import build_baseline_models, train_baseline_models


def _training_data() -> tuple[pd.DataFrame, pd.Series]:
    X_array, y_array = make_classification(
        n_samples=160,
        n_features=5,
        n_informative=3,
        weights=[0.85, 0.15],
        random_state=7,
    )
    X = pd.DataFrame(X_array, columns=[f"feature_{index}" for index in range(5)])
    X["category"] = ["a" if value > 0 else "b" for value in X["feature_0"]]
    return X, pd.Series(y_array, name="target")


def test_baseline_model_configurations_are_imbalance_aware() -> None:
    X, _ = _training_data()
    models = build_baseline_models(X)

    logistic = models["Logistic Regression"].named_steps["classifier"]
    forest = models["Random Forest"].named_steps["classifier"]
    assert logistic.class_weight == "balanced"
    assert forest.class_weight == "balanced_subsample"
    assert forest.random_state == 42
    assert forest.n_estimators == 100


def test_models_train_and_predict_probabilities() -> None:
    X, y = _training_data()
    models = train_baseline_models(X, y)

    for model in models.values():
        probabilities = model.predict_proba(X.iloc[:8])
        assert probabilities.shape == (8, 2)
        assert ((probabilities >= 0) & (probabilities <= 1)).all()
