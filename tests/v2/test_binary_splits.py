from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoanalyst.analyses.binary.split import (
    _advance_equal_timestamp,
    _validate_partitions,
    make_partitions,
)
from autoanalyst.domain.errors import MethodNotApplicableError, SchemaError


def _balanced_frame(rows: int = 240) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "target": [index % 2 for index in range(rows)],
            "group": [f"g{index // 8:03d}" for index in range(rows)],
            "time": pd.date_range("2026-01-01", periods=rows, freq="h", tz="UTC"),
        }
    )


def _assert_complete_disjoint(parts, row_count: int) -> None:
    train = set(parts.train.tolist())
    validation = set(parts.validation.tolist())
    test = set(parts.test.tolist())
    assert not train & validation
    assert not train & test
    assert not validation & test
    assert train | validation | test == set(range(row_count))


def test_stratified_split_is_deterministic_complete_and_class_safe() -> None:
    frame = _balanced_frame()
    first = make_partitions(
        frame,
        target="target",
        strategy="stratified",
        seed=42,
    )
    second = make_partitions(
        frame,
        target="target",
        strategy="stratified",
        seed=42,
    )

    assert np.array_equal(first.train, second.train)
    assert np.array_equal(first.validation, second.validation)
    assert np.array_equal(first.test, second.test)
    _assert_complete_disjoint(first, len(frame))
    for membership in (first.train, first.validation, first.test):
        assert set(frame.iloc[membership]["target"]) == {0, 1}


def test_group_split_is_deterministic_and_never_leaks_groups() -> None:
    frame = _balanced_frame()
    first = make_partitions(
        frame,
        target="target",
        strategy="group",
        group="group",
        seed=42,
    )
    second = make_partitions(
        frame,
        target="target",
        strategy="group",
        group="group",
        seed=42,
    )

    assert np.array_equal(first.train, second.train)
    assert np.array_equal(first.validation, second.validation)
    assert np.array_equal(first.test, second.test)
    _assert_complete_disjoint(first, len(frame))

    train_groups = set(frame.iloc[first.train]["group"])
    validation_groups = set(frame.iloc[first.validation]["group"])
    test_groups = set(frame.iloc[first.test]["group"])
    assert not train_groups & validation_groups
    assert not train_groups & test_groups
    assert not validation_groups & test_groups


def test_group_split_requires_non_null_group_column() -> None:
    frame = _balanced_frame()

    with pytest.raises(SchemaError) as missing:
        make_partitions(
            frame,
            target="target",
            strategy="group",
            seed=42,
        )
    assert missing.value.context["reason"] == "group_split_requires_group_column"

    frame.loc[0, "group"] = None
    with pytest.raises(MethodNotApplicableError) as null_group:
        make_partitions(
            frame,
            target="target",
            strategy="group",
            group="group",
            seed=42,
        )
    assert null_group.value.context["reason"] == "group_split_null_group"


def test_time_split_is_chronological_and_equal_timestamps_stay_together() -> None:
    frame = _balanced_frame()
    frame.loc[166:172, "time"] = frame.loc[166, "time"]
    frame.loc[202:208, "time"] = frame.loc[202, "time"]

    parts = make_partitions(
        frame,
        target="target",
        strategy="time",
        time="time",
        seed=42,
    )
    repeated = make_partitions(
        frame,
        target="target",
        strategy="time",
        time="time",
        seed=999,
    )

    assert np.array_equal(parts.train, repeated.train)
    assert np.array_equal(parts.validation, repeated.validation)
    assert np.array_equal(parts.test, repeated.test)
    _assert_complete_disjoint(parts, len(frame))

    parsed = pd.to_datetime(frame["time"], utc=True)
    assert parsed.iloc[parts.train].max() < parsed.iloc[parts.validation].min()
    assert parsed.iloc[parts.validation].max() < parsed.iloc[parts.test].min()


def test_time_split_rejects_missing_invalid_tiny_and_degenerate_boundaries() -> None:
    frame = _balanced_frame()

    with pytest.raises(SchemaError) as missing:
        make_partitions(
            frame,
            target="target",
            strategy="time",
            seed=42,
        )
    assert missing.value.context["reason"] == "time_split_requires_time_column"

    invalid = frame.copy()
    invalid.loc[0, "time"] = "not-a-date"
    with pytest.raises(MethodNotApplicableError) as bad_time:
        make_partitions(
            invalid,
            target="target",
            strategy="time",
            time="time",
            seed=42,
        )
    assert bad_time.value.context["reason"] == "time_split_invalid_or_null_time"

    tiny = pd.DataFrame(
        {
            "target": [0, 1],
            "time": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
        }
    )
    with pytest.raises(MethodNotApplicableError) as too_small:
        make_partitions(
            tiny,
            target="target",
            strategy="time",
            time="time",
            seed=42,
        )
    assert too_small.value.context["reason"] == "split_too_small"

    same_time = _balanced_frame()
    same_time["time"] = pd.Timestamp("2026-01-01", tz="UTC")
    with pytest.raises(MethodNotApplicableError) as empty:
        make_partitions(
            same_time,
            target="target",
            strategy="time",
            time="time",
            seed=42,
        )
    assert empty.value.context["reason"] == "time_split_empty_partition"


def test_split_strategy_contract_rejects_combined_and_unknown_policies() -> None:
    frame = _balanced_frame()

    with pytest.raises(MethodNotApplicableError) as combined:
        make_partitions(
            frame,
            target="target",
            strategy="group_time",
            group="group",
            time="time",
            seed=42,
        )
    assert combined.value.context["reason"] == "combined_group_time_not_supported"

    with pytest.raises(SchemaError) as unsupported:
        make_partitions(
            frame,
            target="target",
            strategy="random_fallback",
            seed=42,
        )
    assert unsupported.value.context["reason"] == "unsupported_split_strategy"


def test_partition_validator_rejects_empty_overlap_and_small_classes() -> None:
    target = pd.Series([0, 1] * 20)

    with pytest.raises(MethodNotApplicableError) as empty:
        _validate_partitions(
            target,
            np.array([], dtype=int),
            np.arange(10),
            np.arange(10, 40),
        )
    assert empty.value.context["reason"] == "split_empty_partition"

    with pytest.raises(SchemaError) as overlap:
        _validate_partitions(
            target,
            np.arange(20),
            np.arange(10, 20),
            np.arange(20, 40),
        )
    assert overlap.value.context["reason"] == "split_membership_invalid"

    target = pd.Series([0] * 35 + [1] * 5)
    with pytest.raises(MethodNotApplicableError) as class_small:
        _validate_partitions(
            target,
            np.arange(20),
            np.arange(20, 30),
            np.arange(30, 40),
        )
    assert class_small.value.context["reason"] == "split_class_too_small"


def test_equal_timestamp_boundary_helper_handles_edges() -> None:
    series = pd.Series(pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"], utc=True))
    assert _advance_equal_timestamp(series, 0) == 0
    assert _advance_equal_timestamp(series, len(series)) == len(series)
    assert _advance_equal_timestamp(series, 1) == 2
