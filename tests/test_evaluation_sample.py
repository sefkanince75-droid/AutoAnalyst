import pandas as pd
import pytest

from src.evaluation_sample import (
    SEVERE_CLASS_COUNT_THRESHOLD,
    SUFFICIENT_CLASS_COUNT_THRESHOLD,
    diagnose_binary_partition,
    diagnose_evaluation_sample,
    should_render_final_test_warning,
    should_render_validation_warning,
)
from src.splitting import stratified_train_validation_test_split


@pytest.mark.parametrize(
    ("positive", "negative", "severity", "limited"),
    [
        (2, 73, "severe", True),
        (9, 100, "severe", True),
        (10, 100, "limited", True),
        (29, 100, "limited", True),
        (30, 100, "none", False),
        (100, 9, "severe", True),
        (100, 29, "limited", True),
        (30, 30, "none", False),
    ],
)
def test_evaluation_sample_boundaries(
    positive: int, negative: int, severity: str, limited: bool
) -> None:
    result = diagnose_evaluation_sample(positive, negative)
    assert result.severity == severity
    assert result.limited_sample is limited


def test_threshold_constants_and_recall_resolution() -> None:
    assert SEVERE_CLASS_COUNT_THRESHOLD == 10
    assert SUFFICIENT_CLASS_COUNT_THRESHOLD == 30
    assert diagnose_evaluation_sample(2, 73).recall_resolution == 0.5
    assert diagnose_evaluation_sample(9, 100).recall_resolution == pytest.approx(1 / 9)


def test_warning_render_conditions_follow_diagnostic() -> None:
    limited = diagnose_evaluation_sample(2, 73)
    sufficient = diagnose_evaluation_sample(30, 30)
    assert should_render_validation_warning(limited)
    assert should_render_final_test_warning(limited)
    assert not should_render_validation_warning(sufficient)
    assert not should_render_final_test_warning(sufficient)


def test_500_row_three_percent_regression_sample_warns_for_both_partitions() -> None:
    # Fourteen positives display as approximately 3% and reproduce the workbook's 2/73 sets.
    dataframe = pd.DataFrame({"feature": range(500), "target": [0] * 486 + [1] * 14})
    splits = stratified_train_validation_test_split(dataframe, "target")
    validation = diagnose_binary_partition(splits.y_validation, 1)
    final_test = diagnose_binary_partition(splits.y_test, 1)

    assert (validation.positive_count, validation.negative_count) == (2, 73)
    assert (final_test.positive_count, final_test.negative_count) == (2, 73)
    assert validation.limited_sample and final_test.limited_sample
    assert validation.recall_resolution == final_test.recall_resolution == 0.5
