"""Deterministic row partitioning for binary classification."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split

from ...domain.errors import MethodNotApplicableError, SchemaError


@dataclass(frozen=True, slots=True)
class Partitions:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray
    strategy: str


def make_partitions(
    frame: pd.DataFrame,
    *,
    target: str,
    strategy: str,
    seed: int,
    group: str | None = None,
    time: str | None = None,
) -> Partitions:
    if strategy == "stratified":
        indices = np.arange(len(frame))
        y = frame[target].to_numpy()
        train, temp = train_test_split(indices, test_size=0.30, random_state=seed, stratify=y)
        temp_y = y[temp]
        validation, test = train_test_split(
            temp, test_size=0.50, random_state=seed, stratify=temp_y
        )
    elif strategy == "group":
        if not group:
            raise SchemaError({"reason": "group_split_requires_group_column"})
        if frame[group].isna().any():
            raise MethodNotApplicableError({"reason": "group_split_null_group"})
        outer = GroupShuffleSplit(n_splits=1, test_size=0.30, random_state=seed)
        train_pos, temp_pos = next(outer.split(frame, frame[target], groups=frame[group]))
        temp = frame.iloc[temp_pos]
        inner = GroupShuffleSplit(n_splits=1, test_size=0.50, random_state=seed)
        val_rel, test_rel = next(inner.split(temp, temp[target], groups=temp[group]))
        train = np.asarray(train_pos)
        validation = np.asarray(temp_pos)[val_rel]
        test = np.asarray(temp_pos)[test_rel]
    elif strategy == "time":
        if not time:
            raise SchemaError({"reason": "time_split_requires_time_column"})
        parsed = pd.to_datetime(frame[time], errors="coerce", utc=True)
        if parsed.isna().any():
            raise MethodNotApplicableError({"reason": "time_split_invalid_or_null_time"})
        order = np.argsort(parsed.to_numpy(), kind="stable")
        sorted_time = parsed.iloc[order].reset_index(drop=True)
        if len(order) < 3:
            raise MethodNotApplicableError({"reason": "split_too_small"})
        b1 = _advance_equal_timestamp(sorted_time, max(1, int(len(order) * 0.70)))
        b2 = _advance_equal_timestamp(sorted_time, max(b1 + 1, int(len(order) * 0.85)))
        if b1 >= len(order) or b2 >= len(order):
            raise MethodNotApplicableError({"reason": "time_split_empty_partition"})
        train, validation, test = order[:b1], order[b1:b2], order[b2:]
    elif strategy == "group_time":
        raise MethodNotApplicableError({"reason": "combined_group_time_not_supported"})
    else:
        raise SchemaError({"reason": "unsupported_split_strategy", "strategy": strategy})

    _validate_partitions(frame[target], train, validation, test)
    return Partitions(np.sort(train), np.sort(validation), np.sort(test), strategy)


def _advance_equal_timestamp(series: pd.Series, index: int) -> int:
    if index <= 0 or index >= len(series):
        return index
    boundary = series.iloc[index]
    while index < len(series) and series.iloc[index] == boundary:
        index += 1
    return index


def _validate_partitions(
    target: pd.Series, train: np.ndarray, validation: np.ndarray, test: np.ndarray
) -> None:
    if not len(train) or not len(validation) or not len(test):
        raise MethodNotApplicableError({"reason": "split_empty_partition"})
    combined = np.concatenate([train, validation, test])
    if len(np.unique(combined)) != len(combined) or len(combined) != len(target):
        raise SchemaError({"reason": "split_membership_invalid"})
    minimums = ((train, 10, "train"), (validation, 5, "validation"), (test, 5, "test"))
    for indices, minimum, name in minimums:
        counts = target.iloc[indices].value_counts(dropna=False)
        if len(counts) != 2 or int(counts.min()) < minimum:
            raise MethodNotApplicableError(
                {"reason": "split_class_too_small", "partition": name, "minimum": minimum}
            )
