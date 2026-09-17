"""Shared contract for deterministic, UI-independent V2 analysis modules."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..domain.codec import FrozenDict, freeze_json, require_uuid
from ..domain.errors import CancellationError, MethodNotApplicableError
from ..domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from ..domain.results import Artifact, ChartSpec, Finding, Metric, ResultOutcome, ResultTable


@runtime_checkable
class CancellationToken(Protocol):
    def is_cancelled(self) -> bool: ...


ProgressEmitter = Callable[[str, float, FrozenDict], None]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: str
    input_version_id: str
    seed: int
    cancellation: CancellationToken
    environment: FrozenDict = field(default_factory=FrozenDict)
    resource_budget: ResourceBudget | None = None
    methodology_version: str = "2.0"
    deadline_monotonic: float | None = None
    progress: ProgressEmitter | None = None
    data_access: object | None = None
    artifact_writer: object | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_uuid(self.run_id, "run_id"))
        object.__setattr__(
            self, "input_version_id", require_uuid(self.input_version_id, "input_version_id")
        )
        object.__setattr__(self, "environment", freeze_json(self.environment))
        if self.seed < 0:
            raise ValueError("ExecutionContext seed cannot be negative")
        if not self.methodology_version.strip():
            raise ValueError("ExecutionContext methodology_version is required")
        if self.deadline_monotonic is not None and self.deadline_monotonic <= 0:
            raise ValueError("ExecutionContext deadline must be a positive monotonic timestamp")

    def raise_if_cancelled(self) -> None:
        if self.cancellation.is_cancelled():
            raise CancellationError({"run_id": self.run_id})
        if self.deadline_monotonic is not None and time.monotonic() >= self.deadline_monotonic:
            raise CancellationError({"run_id": self.run_id, "reason": "deadline_exceeded"})

    def emit_progress(
        self, stage: str, fraction: float, payload: FrozenDict | dict[str, object] | None = None
    ) -> None:
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("progress fraction must be between zero and one")
        if not stage.strip():
            raise ValueError("progress stage is required")
        self.raise_if_cancelled()
        if self.progress is not None:
            self.progress(stage, fraction, freeze_json({} if payload is None else payload))


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
    required_acknowledgements: tuple[str, ...] = ()
    sample_summary: FrozenDict = field(default_factory=FrozenDict)
    resolved_parameters: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_summary", freeze_json(self.sample_summary))
        object.__setattr__(self, "resolved_parameters", freeze_json(self.resolved_parameters))
        acknowledgements = tuple(str(item).strip() for item in self.required_acknowledgements)
        if any(not item for item in acknowledgements):
            raise ValueError("required acknowledgement codes cannot be empty")
        object.__setattr__(self, "required_acknowledgements", acknowledgements)

    @property
    def applicable(self) -> bool:
        return not self.blocking_issues

    @property
    def can_run(self) -> bool:
        return self.applicable

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
    output_bytes: int = 0
    seconds_range: tuple[float, float] | None = None
    estimate_basis: FrozenDict = field(default_factory=FrozenDict)
    hard_limits_exceeded: tuple[str, ...] = ()
    uncertainty: str = "heuristic"

    def __post_init__(self) -> None:
        if min(self.memory_bytes, self.disk_bytes, self.duration_seconds, self.output_bytes) < 0:
            raise ValueError("Resource estimates cannot be negative")
        if self.parallelism < 1:
            raise ValueError("Resource estimate parallelism must be positive")
        if self.seconds_range is None:
            object.__setattr__(
                self, "seconds_range", (float(self.duration_seconds), float(self.duration_seconds))
            )
        else:
            low, high = self.seconds_range
            if low < 0 or high < low:
                raise ValueError("Resource estimate seconds_range is invalid")
        object.__setattr__(self, "estimate_basis", freeze_json(self.estimate_basis))
        if not self.uncertainty.strip():
            raise ValueError("Resource estimate uncertainty is required")

    @property
    def estimated_peak_memory_bytes(self) -> int:
        return self.memory_bytes

    @property
    def estimated_temp_disk_bytes(self) -> int:
        return self.disk_bytes

    @property
    def estimated_output_bytes(self) -> int:
        return self.output_bytes

    @property
    def estimated_seconds_range(self) -> tuple[float, float]:
        assert self.seconds_range is not None
        return self.seconds_range


@dataclass(frozen=True, slots=True)
class ResultDraft:
    outcome: ResultOutcome
    metrics: tuple[Metric, ...] = ()
    findings: tuple[Finding, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
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
