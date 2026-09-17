"""Deterministic full-dataset profiling module."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pandas as pd

from ..data.table_access import TableAccess
from ..domain.errors import SchemaError
from ..domain.plans import AnalysisModuleId, AnalysisSpec
from ..domain.results import (
    ChartSpec,
    Finding,
    FindingSeverity,
    Metric,
    MetricValueState,
    ResultOutcome,
    ResultTable,
)
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .contract import (
    AnalysisDescription,
    ApplicabilityReport,
    ExecutionContext,
    ResourceEstimate,
    ResultDraft,
    ValidationIssue,
)


class ProfilingModule:
    MODULE_VERSION = "2.0"

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.table_access = TableAccess(catalog, artifact_store)

    def describe(self) -> AnalysisDescription:
        return AnalysisDescription(
            module_id=AnalysisModuleId.PROFILING,
            module_version=self.MODULE_VERSION,
            supported_operations=("profile",),
            parameter_schema={"histogram_bins": {"type": "integer", "minimum": 5, "maximum": 100}},
        )

    def validate(self, spec: AnalysisSpec) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if spec.module_id is not AnalysisModuleId.PROFILING:
            issues.append(ValidationIssue("profiling.module_mismatch"))
        if spec.operation != "profile":
            issues.append(ValidationIssue("profiling.unsupported_operation"))
        bins = spec.parameters.get("histogram_bins", 20)
        if isinstance(bins, bool) or not isinstance(bins, int) or not 5 <= bins <= 100:
            issues.append(ValidationIssue("profiling.invalid_histogram_bins"))
        return tuple(issues)

    def check_applicability(self, spec: AnalysisSpec) -> ApplicabilityReport:
        try:
            version = self.catalog.get_version(spec.input_version_id)
        except Exception:
            return ApplicabilityReport(blocking_issues=(ValidationIssue("profiling.dataset_missing"),))
        if version.column_count <= 0:
            return ApplicabilityReport(blocking_issues=(ValidationIssue("profiling.no_columns"),))
        warnings = () if version.row_count else (ValidationIssue("profiling.empty_dataset"),)
        return ApplicabilityReport(warnings=warnings)

    def estimate_resources(self, spec: AnalysisSpec) -> ResourceEstimate:
        version = self.catalog.get_version(spec.input_version_id)
        approximate_cells = max(1, version.row_count * max(1, version.column_count))
        return ResourceEstimate(
            memory_bytes=min(2_000_000_000, max(8_000_000, approximate_cells * 32)),
            disk_bytes=max(1_000_000, min(500_000_000, approximate_cells * 8)),
            duration_seconds=max(0.1, approximate_cells / 2_000_000),
            parallelism=1,
        )

    def run(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        issues = self.validate(spec)
        if issues:
            raise SchemaError({"reason": "invalid_profiling_spec", "codes": tuple(i.code for i in issues)})
        context.raise_if_cancelled()
        columns = tuple(row for row in self.catalog.list_columns(spec.input_version_id) if not row["is_system"])
        column_ids = tuple(str(row["column_id"]) for row in columns)
        arrow = self.table_access.selected_columns(spec.input_version_id, column_ids)
        frame = arrow.to_pandas()
        physical_to_meta = {str(row["physical_name"]): row for row in columns}
        frame = frame.rename(columns={name: str(meta["display_name"]) for name, meta in physical_to_meta.items()})
        display_to_meta = {str(row["display_name"]): row for row in columns}

        metrics = [
            _metric("row_count", len(frame)),
            _metric("column_count", len(columns)),
            _metric("missing_cells", int(frame.isna().sum().sum())),
        ]
        profile_rows: list[tuple[object, ...]] = []
        findings: list[Finding] = []
        tables: list[ResultTable] = []
        charts: list[ChartSpec] = []
        bins = int(spec.parameters.get("histogram_bins", 20))

        for display_name in frame.columns:
            context.raise_if_cancelled()
            series = frame[display_name]
            meta = display_to_meta[display_name]
            non_null = series.dropna()
            null_count = int(series.isna().sum())
            distinct = int(non_null.nunique(dropna=True))
            finite_non_null = non_null
            non_finite = 0
            if pd.api.types.is_numeric_dtype(non_null):
                numeric = pd.to_numeric(non_null, errors="coerce")
                finite_mask = np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan))
                non_finite = int((~finite_mask).sum())
                finite_non_null = numeric.iloc[np.flatnonzero(finite_mask)]
            is_constant = len(non_null) > 0 and distinct == 1
            is_all_missing = len(non_null) == 0
            profile_rows.append(
                (
                    meta["column_id"],
                    display_name,
                    meta["physical_type"],
                    meta["semantic_hint"],
                    len(series),
                    null_count,
                    distinct,
                    bool(is_constant),
                    bool(is_all_missing),
                    non_finite,
                )
            )
            if is_all_missing:
                findings.append(_finding("profiling.all_missing", FindingSeverity.WARNING, meta["column_id"]))
            elif is_constant:
                findings.append(_finding("profiling.constant", FindingSeverity.INFO, meta["column_id"]))
            if len(series) and distinct / max(1, len(non_null)) >= 0.95 and distinct >= 10:
                findings.append(_finding("profiling.high_cardinality", FindingSeverity.INFO, meta["column_id"]))
            if non_finite:
                findings.append(
                    _finding(
                        "profiling.non_finite",
                        FindingSeverity.WARNING,
                        meta["column_id"],
                        {"count": non_finite},
                    )
                )
            if pd.api.types.is_numeric_dtype(series) and len(finite_non_null) and len(charts) < 5:
                values = np.asarray(finite_non_null, dtype=float)
                counts, edges = np.histogram(values, bins=bins)
                table = ResultTable(
                    table_id=str(uuid4()),
                    columns=("bin_left", "bin_right", "count"),
                    rows=tuple((float(edges[i]), float(edges[i + 1]), int(counts[i])) for i in range(len(counts))),
                )
                tables.append(table)
                charts.append(
                    ChartSpec(
                        chart_id=str(uuid4()),
                        chart_type="histogram",
                        data_table_id=table.table_id,
                        encoding={"x": "bin_left", "x2": "bin_right", "y": "count", "column_id": meta["column_id"]},
                    )
                )

        profile_table = ResultTable(
            table_id=str(uuid4()),
            columns=(
                "column_id",
                "display_name",
                "physical_type",
                "semantic_hint",
                "row_count",
                "null_count",
                "distinct_non_null",
                "is_constant",
                "is_all_missing",
                "non_finite_count",
            ),
            rows=tuple(profile_rows),
        )
        tables.insert(0, profile_table)
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            metrics=tuple(metrics),
            findings=tuple(findings),
            tables=tuple(tables),
            charts=tuple(charts),
            methodology={"module": "profiling", "module_version": self.MODULE_VERSION, "full_dataset": True},
            sample_summary={"rows_used": len(frame), "rows_excluded": 0},
            provenance={"input_version_id": spec.input_version_id, "spec_hash": spec.spec_hash},
        )


def _metric(name: str, value: int | float) -> Metric:
    return Metric(str(uuid4()), name, MetricValueState.FINITE, value=value)


def _finding(
    code: str,
    severity: FindingSeverity,
    column_id: str,
    parameters: dict[str, object] | None = None,
) -> Finding:
    return Finding(
        finding_id=str(uuid4()),
        code=code,
        severity=severity,
        parameters=parameters or {},
        affected_column_ids=(str(column_id),),
    )
