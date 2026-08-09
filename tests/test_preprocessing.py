import numpy as np
import pandas as pd

from src.preprocessing import fit_preprocessor


def test_preprocessor_learns_statistics_only_from_training_data() -> None:
    X_train = pd.DataFrame(
        {
            "number": [1.0, 2.0, np.nan, 3.0],
            "category": ["a", "a", "b", None],
        }
    )
    X_validation = pd.DataFrame({"number": [10_000.0], "category": ["validation-only"]})
    X_test = pd.DataFrame({"number": [-10_000.0], "category": ["test-only"]})

    fitted = fit_preprocessor(X_train)
    numeric_pipeline = fitted.transformer.named_transformers_["numeric"]
    categorical_pipeline = fitted.transformer.named_transformers_["categorical"]

    assert numeric_pipeline.named_steps["imputer"].statistics_[0] == 2.0
    learned_categories = set(categorical_pipeline.named_steps["encoder"].categories_[0])
    assert "validation-only" not in learned_categories
    assert "test-only" not in learned_categories
    assert fitted.transformer.transform(X_validation).shape[0] == 1
    assert fitted.transformer.transform(X_test).shape[0] == 1


def test_preprocessor_imputes_scales_and_encodes_mixed_features() -> None:
    X_train = pd.DataFrame(
        {
            "number": [1.0, np.nan, 3.0],
            "category": ["a", "b", None],
        }
    )
    fitted = fit_preprocessor(X_train)
    transformed = fitted.transformer.transform(X_train)

    assert transformed.shape[0] == 3
    assert not np.isnan(transformed.toarray() if hasattr(transformed, "toarray") else transformed).any()
