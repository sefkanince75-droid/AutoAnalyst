"""Validation-only threshold selection."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import precision_score, recall_score


@dataclass(frozen=True, slots=True)
class ThresholdSelection:
    threshold: float
    precision: float
    recall: float


def select_threshold(y_true, scores, *, positive_label, minimum_recall: float) -> ThresholdSelection | None:
    if not 0 < minimum_recall <= 1:
        raise ValueError("minimum_recall must be in (0, 1]")
    values = np.asarray(scores, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("threshold scores must be finite")
    candidates = sorted(set([0.0, 1.0, *values.tolist()]))
    truth = np.asarray([value == positive_label for value in y_true], dtype=bool)
    feasible: list[ThresholdSelection] = []
    for threshold in candidates:
        pred = values >= threshold
        recall = float(recall_score(truth, pred, zero_division=0))
        if recall + 1e-15 < minimum_recall:
            continue
        precision = float(precision_score(truth, pred, zero_division=0))
        feasible.append(ThresholdSelection(float(threshold), precision, recall))
    if not feasible:
        return None
    feasible.sort(key=lambda item: (item.precision, item.recall, item.threshold), reverse=True)
    return feasible[0]
