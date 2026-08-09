"""Deterministic diagnostics for limited validation and test samples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


SEVERE_CLASS_COUNT_THRESHOLD = 10
SUFFICIENT_CLASS_COUNT_THRESHOLD = 30

SampleSeverity = Literal["severe", "limited", "none"]


@dataclass(frozen=True)
class EvaluationSampleDiagnostic:
    """Class-count evidence supporting evaluation metrics."""

    severity: SampleSeverity
    positive_count: int
    negative_count: int
    recall_resolution: float | None
    limited_sample: bool


def diagnose_evaluation_sample(
    positive_count: int,
    negative_count: int,
) -> EvaluationSampleDiagnostic:
    """Classify evaluation support using centralized conservative boundaries."""
    if positive_count < 0 or negative_count < 0:
        raise ValueError("Class counts cannot be negative.")
    minimum_count = min(positive_count, negative_count)
    if minimum_count < SEVERE_CLASS_COUNT_THRESHOLD:
        severity: SampleSeverity = "severe"
    elif minimum_count < SUFFICIENT_CLASS_COUNT_THRESHOLD:
        severity = "limited"
    else:
        severity = "none"
    return EvaluationSampleDiagnostic(
        severity=severity,
        positive_count=int(positive_count),
        negative_count=int(negative_count),
        recall_resolution=(1.0 / positive_count) if positive_count else None,
        limited_sample=severity != "none",
    )


def diagnose_binary_partition(
    target: Iterable[object],
    positive_class: object,
) -> EvaluationSampleDiagnostic:
    """Count the selected positive class and all remaining observations."""
    values = list(target)
    positive_count = sum(value == positive_class for value in values)
    return diagnose_evaluation_sample(positive_count, len(values) - positive_count)


def should_render_validation_warning(diagnostic: EvaluationSampleDiagnostic) -> bool:
    """Expose the validation-warning condition without UI policy duplication."""
    return diagnostic.limited_sample


def should_render_final_test_warning(diagnostic: EvaluationSampleDiagnostic) -> bool:
    """Expose the final-warning condition without UI policy duplication."""
    return diagnostic.limited_sample
