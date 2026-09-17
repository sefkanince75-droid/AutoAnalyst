"""Binary classification metrics with explicit positive-class semantics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


@dataclass(frozen=True, slots=True)
class BinaryMetrics:
    average_precision: float
    roc_auc: float
    precision: float
    recall: float
    f1: float
    tn: int
    fp: int
    fn: int
    tp: int
    predicted_positive_rate: float


def positive_scores(model, X, positive_label):
    classes = list(model.classes_)
    if positive_label not in classes:
        raise ValueError("positive label is absent from fitted model classes")
    index = classes.index(positive_label)
    return np.asarray(model.predict_proba(X)[:, index], dtype=float)


def evaluate_scores(y_true, scores, *, positive_label, negative_label, threshold: float) -> BinaryMetrics:
    y_binary = np.asarray([1 if value == positive_label else 0 for value in y_true], dtype=int)
    predictions_binary = (np.asarray(scores, dtype=float) >= threshold).astype(int)
    labels = np.where(predictions_binary == 1, positive_label, negative_label)
    tn, fp, fn, tp = confusion_matrix(y_true, labels, labels=[negative_label, positive_label]).ravel()
    return BinaryMetrics(
        average_precision=float(average_precision_score(y_binary, scores)),
        roc_auc=float(roc_auc_score(y_binary, scores)),
        precision=float(precision_score(y_true, labels, pos_label=positive_label, zero_division=0)),
        recall=float(recall_score(y_true, labels, pos_label=positive_label, zero_division=0)),
        f1=float(f1_score(y_true, labels, pos_label=positive_label, zero_division=0)),
        tn=int(tn), fp=int(fp), fn=int(fn), tp=int(tp),
        predicted_positive_rate=float(predictions_binary.mean()),
    )
