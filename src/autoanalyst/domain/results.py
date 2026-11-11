"""Shared, UI-independent analysis result model."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .codec import (
    SCHEMA_VERSION,
    FrozenDict,
    freeze_json,
    require_sha256,
    require_utc,
    require_uuid,
)
from .plans import AnalysisModuleId


class ResultOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    INSUFFICIENT_DATA = "insufficient_data"
    METHOD_NOT_APPLICABLE = "method_not_applicable"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MetricValueState(str, Enum):
    FINITE = "finite"
    NOT_DEFINED = "not_defined"
    POSITIVE_INFINITY = "positive_infinity"
    NEGATIVE_INFINITY = "negative_infinity"


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    code: str
    severity: FindingSeverity
    parameters: FrozenDict = field(default_factory=FrozenDict)
    evidence_refs: tuple[str, ...] = ()
    affected_column_ids: tuple[str, ...] = ()
    action_code: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", require_uuid(self.finding_id, "finding_id"))
        object.__setattr__(self, "severity", FindingSeverity(self.severity))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))
        object.__setattr__(
            self,
            "affected_column_ids",
            tuple(require_uuid(item, "affected_column_id") for item in self.affected_column_ids),
        )
        if not self.code:
            raise ValueError("Finding code is required")


@dataclass(frozen=True, slots=True)
class Metric:
    metric_id: str
    name: str
    value_state: MetricValueState
    value: int | float | None = None
    unit: str | None = None
    dimensions: FrozenDict = field(default_factory=FrozenDict)
    sample_size: int | None = None
    numerator: int | float | None = None
    denominator: int | float | None = None
    interval: tuple[float, float] | None = None
    reason_code: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", require_uuid(self.metric_id, "metric_id"))
        object.__setattr__(self, "value_state", MetricValueState(self.value_state))
        object.__setattr__(self, "dimensions", freeze_json(self.dimensions))
        if not self.name:
            raise ValueError("Metric name is required")
        if self.value_state is MetricValueState.FINITE:
            if (
                self.value is None
                or isinstance(self.value, bool)
                or not math.isfinite(float(self.value))
            ):
                raise ValueError("finite metrics require a finite numeric value")
        elif self.value is not None:
            raise ValueError("Non-finite metric states must not carry a raw numeric value")
        if self.value_state is MetricValueState.NOT_DEFINED and not self.reason_code:
            raise ValueError("not_defined metrics require reason_code")
        for field_name in ("numerator", "denominator"):
            item = getattr(self, field_name)
            if item is not None and (isinstance(item, bool) or not math.isfinite(float(item))):
                raise ValueError(f"{field_name} must be finite")
        if self.interval is not None and any(not math.isfinite(item) for item in self.interval):
            raise ValueError("Metric interval bounds must be finite")
        if self.sample_size is not None and self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")


@dataclass(frozen=True, slots=True)
class ResultTable:
    table_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "table_id", require_uuid(self.table_id, "table_id"))
        object.__setattr__(
            self, "rows", tuple(tuple(freeze_json(item) for item in row) for row in self.rows)
        )
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("Every result table row must match the column count")


@dataclass(frozen=True, slots=True)
class ChartSpec:
    chart_id: str
    chart_type: str
    data_table_id: str
    encoding: FrozenDict
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "chart_id", require_uuid(self.chart_id, "chart_id"))
        object.__setattr__(self, "data_table_id", require_uuid(self.data_table_id, "data_table_id"))
        object.__setattr__(self, "encoding", freeze_json(self.encoding))
        if not self.chart_type:
            raise ValueError("chart_type is required")


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    project_id: str
    owner_run_id: str
    kind: str
    relative_path: str
    media_type: str
    byte_size: int
    sha256: str
    format_version: str
    created_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("artifact_id", "project_id", "owner_run_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "sha256", require_sha256(self.sha256, "sha256"))
        require_utc(self.created_at, "created_at")
        if (
            self.byte_size < 0
            or not self.relative_path
            or self.relative_path.startswith(("/", "\\"))
            or ".." in self.relative_path.split("/")
        ):
            raise ValueError("Artifact requires a safe relative path and non-negative byte_size")


@dataclass(frozen=True, slots=True)
class Report:
    report_id: str
    root_result_id: str
    format: str
    language: str
    template_version: str
    render_options_hash: str
    artifact_id: str
    created_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("report_id", "root_result_id", "artifact_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "render_options_hash",
            require_sha256(self.render_options_hash, "render_options_hash"),
        )
        require_utc(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    result_id: str
    run_id: str
    module_id: AnalysisModuleId
    outcome: ResultOutcome
    metrics: tuple[Metric, ...] = ()
    findings: tuple[Finding, ...] = ()
    tables: tuple[ResultTable, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    methodology: FrozenDict = field(default_factory=FrozenDict)
    sample_summary: FrozenDict = field(default_factory=FrozenDict)
    provenance: FrozenDict = field(default_factory=FrozenDict)
    related_result_ids: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_id", require_uuid(self.result_id, "result_id"))
        object.__setattr__(self, "run_id", require_uuid(self.run_id, "run_id"))
        object.__setattr__(self, "module_id", AnalysisModuleId(self.module_id))
        object.__setattr__(self, "outcome", ResultOutcome(self.outcome))
        for field_name in ("methodology", "sample_summary", "provenance"):
            object.__setattr__(self, field_name, freeze_json(getattr(self, field_name)))
        object.__setattr__(
            self,
            "related_result_ids",
            tuple(require_uuid(item, "related_result_id") for item in self.related_result_ids),
        )
