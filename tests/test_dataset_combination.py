import pandas as pd
import pytest

from src.dataset_combination import (
    DatasetPart,
    combine_row_wise,
    content_digest,
    dataframe_memory_bytes,
    dataset_identity,
    safe_source_column,
    validate_schema_compatibility,
)


def _part(name: str, dataframe: pd.DataFrame, worksheet: str | None = None) -> DatasetPart:
    content = f"{name}:{worksheet}".encode()
    return DatasetPart(name, content_digest(content), len(content), dataframe, worksheet)


def test_multiple_csv_files_with_identical_schemas_combine_row_wise() -> None:
    parts = [
        _part("jan.csv", pd.DataFrame({"amount": [1, 2], "target": [0, 1]})),
        _part("feb.csv", pd.DataFrame({"amount": [3], "target": [1]})),
    ]
    combined = combine_row_wise(parts, add_source_column=False)
    assert combined.dataframe.to_dict("list") == {"amount": [1, 2, 3], "target": [0, 1, 1]}


def test_same_columns_in_different_order_are_normalized() -> None:
    parts = [
        _part("a.csv", pd.DataFrame({"amount": [1], "target": [0]})),
        _part("b.csv", pd.DataFrame({"target": [1], "amount": [2]})),
    ]
    report = validate_schema_compatibility(parts)
    combined = combine_row_wise(parts, add_source_column=False)
    assert report.is_compatible
    assert list(combined.dataframe.columns) == ["amount", "target"]


def test_incompatible_schema_reports_missing_and_additional_columns() -> None:
    parts = [
        _part("reference.csv", pd.DataFrame({"amount": [1], "target": [0]})),
        _part("march.csv", pd.DataFrame({"target": [1], "branch_id": [4]})),
    ]
    difference = validate_schema_compatibility(parts).differences[1]
    assert difference.missing_columns == ("amount",)
    assert difference.additional_columns == ("branch_id",)
    with pytest.raises(ValueError, match="incompatible"):
        combine_row_wise(parts)


def test_obviously_incompatible_dtypes_are_reported() -> None:
    parts = [
        _part("a.csv", pd.DataFrame({"amount": [1.0], "target": [0]})),
        _part("b.csv", pd.DataFrame({"amount": ["unknown"], "target": [1]})),
    ]
    difference = validate_schema_compatibility(parts).differences[1]
    assert difference.incompatible_types == (("amount", "numeric", "categorical"),)


def test_mixed_csv_and_selected_xlsx_sheet_combine() -> None:
    parts = [
        _part("jan.csv", pd.DataFrame({"amount": [1], "target": [0]})),
        _part("march.xlsx", pd.DataFrame({"amount": [2], "target": [1]}), "Transactions"),
    ]
    combined = combine_row_wise(parts)
    assert combined.dataframe["amount"].tolist() == [1, 2]
    assert parts[1].worksheet == "Transactions"


def test_source_file_metadata_and_collision_safe_name() -> None:
    parts = [
        _part("a.csv", pd.DataFrame({"value": [1], "__source_file": ["original"], "target": [0]})),
        _part("b.csv", pd.DataFrame({"value": [2], "__source_file": ["original"], "target": [1]})),
    ]
    combined = combine_row_wise(parts)
    assert combined.source_column == "__source_file_1"
    assert combined.dataframe["__source_file_1"].tolist() == ["a.csv", "b.csv"]
    assert safe_source_column(["__source_file", "__source_file_1"]) == "__source_file_2"


def test_combined_identity_depends_on_order_sheet_mode_and_source_option() -> None:
    first = _part("a.csv", pd.DataFrame({"x": [1], "target": [0]}))
    second = _part("b.xlsx", pd.DataFrame({"x": [2], "target": [1]}), "Sheet1")
    base = dataset_identity([first, second], mode="combine", add_source_column=True)
    assert base == dataset_identity([first, second], mode="combine", add_source_column=True)
    assert base != dataset_identity([second, first], mode="combine", add_source_column=True)
    assert base != dataset_identity([first, second], mode="combine", add_source_column=False)
    changed_sheet = _part("b.xlsx", second.dataframe, "Sheet2")
    assert base != dataset_identity([first, changed_sheet], mode="combine", add_source_column=True)


def test_dataframe_memory_estimation_uses_deep_usage() -> None:
    dataframe = pd.DataFrame({"text": ["long text value"] * 100, "number": range(100)})
    assert dataframe_memory_bytes(dataframe) == int(dataframe.memory_usage(index=True, deep=True).sum())
    assert dataframe_memory_bytes(dataframe) > dataframe.memory_usage(index=True, deep=False).sum()
