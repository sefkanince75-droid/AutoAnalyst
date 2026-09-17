"""Shared contract for deterministic, UI-independent V2 analysis modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.codec import FrozenDict, freeze_json, require_uuid
from ..domain.errors import CancellationError, MethodNotApplicableError
from ..domain.plans import AnalysisModuleId, AnalysisSpec
from ..domain.results import ChartSpec, Finding, Metric, ResultOutcome, ResultTable


@runtime_checkable
class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: str
    input_version_id: str
    seed: int
    cancellation: CancellationToken
    environment: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_uuid(self.run_id, "run_id"))
        object.__setattr__(
            self,
            "input_version_id",
            require_uuid(self.input_version_id, "input_version_id"),
        )
        object.__setattr__(self, "environment", freeze_json(self.environment))
        if self.seed < 0:
            raise ValueError("ExecutionContext seed cannot be negative")

    def raise_if_cancelled(self) -> None:
        if self.cancellation.is_cancelled():
            raise CancellationError({"run_id": self.run_id})


@dataclass(frozen=True, slots=True)
class AnalysisDescription:
    module_id: AnalysisModuleId
    module_version: str
    supported_operations: tuple[str, ...]
    parameter_schema: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "module_id", AnalysisModuleId(self.module_id))
        object.__setattr__(self, "parameter_schema", freeze_json(self.parameter_schema))
        if not self.module_version.strip() or not self.supported_operations:
            raise ValueError("AnalysisDescription requires a version and supported operations")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    context: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", freeze_json(self.context))
        if not self.code.strip():
            raise ValueError("ValidationIssue code is required")


@dataclass(frozen=True, slots=True)
class ApplicabilityReport:
    blocking_issues: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def can_run(self) -> bool:
        return not self.blocking_issues

    def require_runnable(self) -> None:
        if not self.can_run:
            raise MethodNotApplicableError(
                {"blocking_codes": tuple(issue.code for issue in self.blocking_issues)}
            )


@dataclass(frozen=True, slots=True)
class ResourceEstimate:
    memory_bytes: int
    disk_bytes: int
    duration_seconds: float
    parallelism: int = 1

    def __post_init__(self) -> None:
        if min(self.memory_bytes, self.disk_bytes, self.duration_seconds) < 0:
            raise ValueError("Resource estimates cannot be negative")
        if self.parallelism < 1:
            raise ValueError("Resource estimate parallelism must be positive")


@dataclass(frozen=True, slots=True)
class ResultDraft:
    outcome: ResultOutcome
    metrics: tuple[Metric, ...] = ()
    findings: tuple[Finding, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    methodology: FrozenDict = field(default_factory=FrozenDict)
    sample_summary: FrozenDict = field(default_factory=FrozenDict)
    provenance: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", ResultOutcome(self.outcome))
        for field_name in ("methodology", "sample_summary", "provenance"):
            object.__setattr__(self, field_name, freeze_json(getattr(self, field_name)))


@runtime_checkable
class AnalysisModule(Protocol):
    def describe(self) -> AnalysisDescription: ...

    def validate(self, spec: AnalysisSpec) -> tuple[ValidationIssue, ...]: ...

    def check_applicability(self, spec: AnalysisSpec) -> ApplicabilityReport: ...

    def estimate_resources(self, spec: AnalysisSpec) -> ResourceEstimate: ...

    def run(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft: ...
