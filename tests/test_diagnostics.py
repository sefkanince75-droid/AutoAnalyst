import pandas as pd

from src.diagnostics import (
    binary_target_candidates,
    detect_imbalance,
    detect_possible_id_columns,
    diagnose_dataset,
    validate_binary_target,
)


def test_binary_target_validation_accepts_two_adequate_classes() -> None:
    result = validate_binary_target(pd.Series([0, 0, 0, 1, 1, 1]))
    assert result.is_valid
    assert result.class_summary["percentage"].sum() == 100


def test_binary_target_validation_rejects_multiclass_and_small_class() -> None:
    assert not validate_binary_target(pd.Series([0, 0, 0, 1, 1, 1, 2, 2, 2])).is_valid
    assert not validate_binary_target(pd.Series([0, 0, 0, 1, 1])).is_valid


def test_binary_target_validation_rejects_missing_values() -> None:
    assert not validate_binary_target(pd.Series([0, 0, 0, 1, 1, 1, None])).is_valid


def test_imbalance_detection() -> None:
    ratio, imbalanced, severe = detect_imbalance(pd.Series([0] * 95 + [1] * 5))
    assert ratio == 0.05
    assert imbalanced and severe


def test_dataset_diagnostics_detects_feature_issues() -> None:
    dataframe = pd.DataFrame(
        {
            "numeric": [1, 2, 3, 4, 1000, None],
            "constant": ["x"] * 6,
            "category": ["a", "b", "a", "b", "a", "b"],
            "target": [0, 0, 0, 1, 1, 1],
        }
    )
    result = diagnose_dataset(dataframe, "target")
    assert result.numerical_columns == ["numeric"]
    assert "constant" in result.constant_columns
    assert "numeric" in result.columns_with_missing


def test_binary_target_candidates_include_supported_encodings() -> None:
    dataframe = pd.DataFrame(
        {
            "zero_one": [0, 1, 0, 1],
            "boolean": [True, False, True, False],
            "text": ["yes", "no", "yes", "no"],
            "multiclass": ["a", "b", "c", "a"],
            "constant": [1, 1, 1, 1],
        }
    )
    assert binary_target_candidates(dataframe) == ["zero_one", "boolean", "text"]


def test_binary_target_candidates_count_distinct_non_null_values() -> None:
    dataframe = pd.DataFrame({"candidate": ["yes", "no", None], "not_binary": [1, 2, 3]})
    assert binary_target_candidates(dataframe) == ["candidate"]


def test_binary_target_candidates_can_return_one_or_none() -> None:
    one = pd.DataFrame({"feature": [1, 2, 3], "target": [0, 1, 0]})
    none = pd.DataFrame({"feature": [1, 2, 3], "constant": [0, 0, 0]})
    assert binary_target_candidates(one) == ["target"]
    assert binary_target_candidates(none) == []


def test_possible_id_detection_is_conservative() -> None:
    dataframe = pd.DataFrame(
        {
            "transaction_id": [f"tx-{index}" for index in range(100)],
            "free_text_key": [f"value-{index}" for index in range(100)],
            "continuous_measure": list(range(100)),
            "category": ["a", "b"] * 50,
        }
    )
    detected = detect_possible_id_columns(dataframe)
    assert "transaction_id" in detected
    assert "free_text_key" in detected
    assert "continuous_measure" not in detected
    assert "category" not in detected
