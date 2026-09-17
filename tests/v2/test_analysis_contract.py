from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from autoanalyst.analyses.contract import (
    AnalysisDescription,
    ApplicabilityReport,
    ExecutionContext,
    ResourceEstimate,
    ResultDraft,
    ValidationIssue,
)
from autoanalyst.analyses.registry import ALLOWED_MODULE_IDS, AnalysisRegistry
from autoanalyst.domain.errors import (
    CancellationError,
    DependencyError,
    ErrorCode,
    MethodNotApplicableError,
    SchemaError,
)
from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.domain.results import ResultOutcome


@dataclass
class StubModule:
    module_id: AnalysisModuleId

    def describe(self) -> AnalysisDescription:
        return AnalysisDescription(self.module_id, "2.0", ("run",))

    def validate(self, spec):
        return ()

    def check_applicability(self, spec):
        return ApplicabilityReport()

    def estimate_resources(self, spec):
        return ResourceEstimate(1, 1, 0.1)

    def run(self, spec, context):
        context.raise_if_cancelled()
        return ResultDraft(ResultOutcome.SUCCEEDED)


class InvalidModule:
    def describe(self):
        return SimpleNamespace(module_id="external_plugin")


@dataclass(frozen=True)
class CancellationState:
    cancelled: bool

    def is_cancelled(self) -> bool:
        return self.cancelled


def test_registry_is_closed_to_the_four_v2_module_ids() -> None:
    assert ALLOWED_MODULE_IDS == {
        "profiling",
        "preparation",
        "comparison",
        "binary_classification",
    }
    registry = AnalysisRegistry([StubModule(AnalysisModuleId.PROFILING)])
    assert registry.module_ids == ("profiling",)
    assert registry.get("profiling").describe().module_id is AnalysisModuleId.PROFILING

    with pytest.raises(SchemaError) as rejected:
        AnalysisRegistry([InvalidModule()])
    assert rejected.value.code is ErrorCode.SCHEMA_ERROR


def test_registry_distinguishes_allowed_but_unregistered_module() -> None:
    with pytest.raises(DependencyError):
        AnalysisRegistry().get(AnalysisModuleId.COMPARISON)


def test_blocking_applicability_report_prevents_run() -> None:
    report = ApplicabilityReport(
        blocking_issues=(ValidationIssue("target_has_one_class", {"classes": 1}),)
    )

    assert report.can_run is False
    with pytest.raises(MethodNotApplicableError) as blocked:
        report.require_runnable()
    assert blocked.value.context["blocking_codes"] == ("target_has_one_class",)


def test_non_blocking_applicability_report_allows_run() -> None:
    report = ApplicabilityReport(warnings=(ValidationIssue("small_sample"),))
    assert report.can_run is True
    assert report.require_runnable() is None


def test_cancellation_is_cooperative_through_execution_context() -> None:
    context = ExecutionContext(
        run_id=str(uuid4()),
        input_version_id=str(uuid4()),
        seed=42,
        cancellation=CancellationState(cancelled=True),
    )

    with pytest.raises(CancellationError) as cancelled:
        context.raise_if_cancelled()
    assert cancelled.value.code is ErrorCode.CANCELLATION
    assert cancelled.value.context["run_id"] == context.run_id
