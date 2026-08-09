import pandas as pd

from src.splitting import split_class_summary, stratified_train_validation_test_split


def test_stratified_split_has_expected_sizes_and_no_overlap() -> None:
    dataframe = pd.DataFrame(
        {
            "row_id": range(200),
            "feature": range(1000, 1200),
            "target": [0] * 160 + [1] * 40,
        }
    )
    splits = stratified_train_validation_test_split(dataframe, "target")

    assert (len(splits.X_train), len(splits.X_validation), len(splits.X_test)) == (140, 30, 30)
    train_ids = set(splits.X_train["row_id"])
    validation_ids = set(splits.X_validation["row_id"])
    test_ids = set(splits.X_test["row_id"])
    assert train_ids.isdisjoint(validation_ids)
    assert train_ids.isdisjoint(test_ids)
    assert validation_ids.isdisjoint(test_ids)
    assert train_ids | validation_ids | test_ids == set(dataframe["row_id"])


def test_stratified_split_preserves_class_distribution() -> None:
    dataframe = pd.DataFrame({"feature": range(200), "target": [0] * 160 + [1] * 40})
    splits = stratified_train_validation_test_split(dataframe, "target")

    for target in (splits.y_train, splits.y_validation, splits.y_test):
        assert target.value_counts(normalize=True).sort_index().to_dict() == {0: 0.8, 1: 0.2}

    summary = split_class_summary(splits)
    assert set(summary["split"]) == {"Train", "Validation", "Final test"}
