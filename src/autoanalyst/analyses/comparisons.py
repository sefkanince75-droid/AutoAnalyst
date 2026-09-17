"""Deterministic descriptive and limited inferential comparison module."""

from __future__ import annotations

import math
from uuid import uuid4

import numpy as np
import pandas as pd
from scipy import stats

from ..data.table_access import TableAccess
from ..domain.errors import MethodNotApplicableError, SchemaError
from ..domain.plans import AnalysisModuleId, AnalysisSpec
from ..domain.results import Finding, FindingSeverity, Metric, MetricValueState, ResultOutcome, ResultTable
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .contract import AnalysisDescription, ApplicabilityReport, ExecutionContext, ResourceEstimate, ResultDraft, ValidationIssue


OPERATIONS = (
    "group_summary",
    "distribution",
    "crosstab",
    "correlation",
    "welch_two_groups",
    "fisher_2x2",
)


class ComparisonModule:
    MODULE_VERSION = "2.0"

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.table_access = TableAccess(catalog, artifact_store)

    def describe(self) -> AnalysisDescription:
        return AnalysisDescription(AnalysisModuleId.COMPARISON, self.MODULE_VERSION, OPERATIONS)

    def validate(self, spec: AnalysisSpec) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if spec.module_id is not AnalysisModuleId.COMPARISON:
            issues.append(ValidationIssue("comparison.module_mismatch"))
        if spec.operation not in OPERATIONS:
            issues.append(ValidationIssue("comparison.unsupported_operation"))
        params = spec.parameters
        required = {
            "group_summary": ("group_columns", "measure_columns"),
            "distribution": ("column_id",),
            "crosstab": ("row_column_id", "column_column_id"),
            "correlation": ("x_column_id", "y_column_id"),
            "welch_two_groups": ("group_column_id", "measure_column_id", "group_a", "group_b"),
            "fisher_2x2": ("row_column_id", "column_column_id", "row_positive", "column_positive"),
        }.get(spec.operation, ())
        for key in required:
            if key not in params:
                issues.append(ValidationIssue("comparison.missing_parameter", {"parameter": key}))
        return tuple(issues)

    def check_applicability(self, spec: AnalysisSpec) -> ApplicabilityReport:
        if self.validate(spec):
            return ApplicabilityReport(blocking_issues=self.validate(spec))
        try:
            self.catalog.get_version(spec.input_version_id)
        except Exception:
            return ApplicabilityReport(blocking_issues=(ValidationIssue("comparison.dataset_missing"),))
        return ApplicabilityReport()

    def estimate_resources(self, spec: AnalysisSpec) -> ResourceEstimate:
        version = self.catalog.get_version(spec.input_version_id)
        cells = max(1, version.row_count * min(max(1, version.column_count), 8))
        return ResourceEstimate(max(8_000_000, cells * 24), max(1_000_000, cells * 4), max(0.1, cells / 3_000_000), 1)

    def run(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        issues = self.validate(spec)
        if issues:
            raise SchemaError({"reason": "invalid_comparison_spec", "codes": tuple(i.code for i in issues)})
        context.raise_if_cancelled()
        op = spec.operation
        if op == "group_summary":
            draft = self._group_summary(spec)
        elif op == "distribution":
            draft = self._distribution(spec)
        elif op == "crosstab":
            draft = self._crosstab(spec)
        elif op == "correlation":
            draft = self._correlation(spec)
        elif op == "welch_two_groups":
            draft = self._welch(spec)
        else:
            draft = self._fisher(spec)
        context.raise_if_cancelled()
        return draft

    def _load(self, spec: AnalysisSpec, column_ids: tuple[str, ...]) -> tuple[pd.DataFrame, dict[str, dict[str, object]]]:
        metadata = {str(row["column_id"]): row for row in self.catalog.list_columns(spec.input_version_id) if not row["is_system"]}
        missing = [column_id for column_id in column_ids if column_id not in metadata]
        if missing:
            raise SchemaError({"reason": "comparison_column_missing", "column_ids": tuple(missing)})
        table = self.table_access.selected_columns(spec.input_version_id, column_ids).to_pandas()
        rename = {str(metadata[c]["physical_name"]): c for c in column_ids}
        return table.rename(columns=rename), metadata

    def _group_summary(self, spec: AnalysisSpec) -> ResultDraft:
        groups = tuple(str(x) for x in spec.parameters["group_columns"])
        measures = tuple(str(x) for x in spec.parameters["measure_columns"])
        if not 1 <= len(groups) <= 2 or not 1 <= len(measures) <= 5:
            raise SchemaError({"reason": "comparison_group_summary_limits"})
        frame, _ = self._load(spec, groups + measures)
        if frame.groupby(list(groups), dropna=False).ngroups > 1000:
            raise MethodNotApplicableError({"reason": "comparison_too_many_groups"})
        rows: list[tuple[object, ...]] = []
        for keys, subset in frame.groupby(list(groups), dropna=False, sort=True):
            keys_tuple = keys if isinstance(keys, tuple) else (keys,)
            for measure in measures:
                numeric = pd.to_numeric(subset[measure], errors="coerce").replace([np.inf, -np.inf], np.nan)
                valid = numeric.dropna()
                rows.append(
                    tuple(_safe(x) for x in keys_tuple)
                    + (
                        measure,
                        int(len(valid)),
                        int(numeric.isna().sum()),
                        _safe(valid.mean()) if len(valid) else None,
                        _safe(valid.median()) if len(valid) else None,
                        _safe(valid.std(ddof=1)) if len(valid) > 1 else None,
                        _safe(valid.min()) if len(valid) else None,
                        _safe(valid.max()) if len(valid) else None,
                    )
                )
        table = ResultTable(str(uuid4()), tuple([f"group_{i+1}" for i in range(len(groups))] + ["measure_column_id", "n", "missing", "mean", "median", "std", "min", "max"]), tuple(rows))
        return self._draft(spec, tables=(table,), sample={"rows_used": len(frame)})

    def _distribution(self, spec: AnalysisSpec) -> ResultDraft:
        column_id = str(spec.parameters["column_id"])
        frame, _ = self._load(spec, (column_id,))
        series = frame[column_id]
        numeric = pd.to_numeric(series, errors="coerce")
        numeric_valid = numeric.replace([np.inf, -np.inf], np.nan).dropna()
        if len(numeric_valid) >= max(1, int(series.notna().sum() * 0.8)):
            counts, edges = np.histogram(numeric_valid.to_numpy(dtype=float), bins=int(spec.parameters.get("bins", 20)))
            table = ResultTable(str(uuid4()), ("bin_left", "bin_right", "count"), tuple((float(edges[i]), float(edges[i+1]), int(counts[i])) for i in range(len(counts))))
        else:
            counts = series.astype("object").where(series.notna(), "<MISSING>").value_counts(dropna=False).head(int(spec.parameters.get("top_k", 20)))
            table = ResultTable(str(uuid4()), ("value", "count", "fraction"), tuple((_safe(k), int(v), float(v / max(1, len(series)))) for k, v in counts.items()))
        return self._draft(spec, tables=(table,), sample={"rows_used": len(frame)})

    def _crosstab(self, spec: AnalysisSpec) -> ResultDraft:
        row_id = str(spec.parameters["row_column_id"])
        col_id = str(spec.parameters["column_column_id"])
        frame, _ = self._load(spec, (row_id, col_id))
        used = frame.dropna(subset=[row_id, col_id])
        ct = pd.crosstab(used[row_id], used[col_id], dropna=False)
        rows = tuple((_safe(r), _safe(c), int(ct.loc[r, c])) for r in ct.index for c in ct.columns)
        table = ResultTable(str(uuid4()), ("row_value", "column_value", "count"), rows)
        return self._draft(spec, tables=(table,), sample={"rows_used": len(used), "rows_excluded": len(frame)-len(used)})

    def _correlation(self, spec: AnalysisSpec) -> ResultDraft:
        x_id, y_id = str(spec.parameters["x_column_id"]), str(spec.parameters["y_column_id"])
        frame, _ = self._load(spec, (x_id, y_id))
        x = pd.to_numeric(frame[x_id], errors="coerce").replace([np.inf, -np.inf], np.nan)
        y = pd.to_numeric(frame[y_id], errors="coerce").replace([np.inf, -np.inf], np.nan)
        valid = pd.DataFrame({"x": x, "y": y}).dropna()
        if len(valid) < 3 or valid.x.nunique() < 2 or valid.y.nunique() < 2:
            raise MethodNotApplicableError({"reason": "correlation_insufficient_variation"})
        method = str(spec.parameters.get("method", "pearson"))
        if method == "pearson":
            value = float(stats.pearsonr(valid.x, valid.y).statistic)
        elif method == "spearman":
            value = float(stats.spearmanr(valid.x, valid.y).statistic)
        else:
            raise SchemaError({"reason": "unsupported_correlation_method"})
        metric = _metric("correlation", value, {"method": method, "x_column_id": x_id, "y_column_id": y_id}, len(valid))
        return self._draft(spec, metrics=(metric,), sample={"rows_used": len(valid), "rows_excluded": len(frame)-len(valid)})

    def _welch(self, spec: AnalysisSpec) -> ResultDraft:
        group_id, measure_id = str(spec.parameters["group_column_id"]), str(spec.parameters["measure_column_id"])
        a_label, b_label = spec.parameters["group_a"], spec.parameters["group_b"]
        frame, _ = self._load(spec, (group_id, measure_id))
        values = pd.to_numeric(frame[measure_id], errors="coerce").replace([np.inf, -np.inf], np.nan)
        a = values[frame[group_id] == a_label].dropna().to_numpy(dtype=float)
        b = values[frame[group_id] == b_label].dropna().to_numpy(dtype=float)
        if len(a) < 2 or len(b) < 2 or (np.var(a, ddof=1) == 0 and np.var(b, ddof=1) == 0):
            raise MethodNotApplicableError({"reason": "welch_insufficient_data"})
        result = stats.ttest_ind(a, b, equal_var=False)
        diff = float(a.mean() - b.mean())
        va, vb = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
        se2 = va/len(a) + vb/len(b)
        df = se2**2 / (((va/len(a))**2)/(len(a)-1) + ((vb/len(b))**2)/(len(b)-1))
        half = float(stats.t.ppf(0.975, df) * math.sqrt(se2))
        metrics = (
            _metric("mean_difference", diff, sample_size=len(a)+len(b), interval=(diff-half, diff+half)),
            _metric("welch_t", float(result.statistic), sample_size=len(a)+len(b)),
            _metric("welch_df", float(df), sample_size=len(a)+len(b)),
            _metric("p_value", float(result.pvalue), sample_size=len(a)+len(b)),
        )
        findings = ()
        if len(a) < 30 or len(b) < 30:
            findings = (Finding(str(uuid4()), "comparison.small_group_sample", FindingSeverity.WARNING, parameters={"n_a": len(a), "n_b": len(b)}),)
        return self._draft(spec, metrics=metrics, findings=findings, sample={"rows_used": len(a)+len(b), "rows_excluded": len(frame)-len(a)-len(b)})

    def _fisher(self, spec: AnalysisSpec) -> ResultDraft:
        row_id, col_id = str(spec.parameters["row_column_id"]), str(spec.parameters["column_column_id"])
        row_positive, col_positive = spec.parameters["row_positive"], spec.parameters["column_positive"]
        frame, _ = self._load(spec, (row_id, col_id))
        used = frame.dropna(subset=[row_id, col_id])
        row_values, col_values = list(used[row_id].drop_duplicates()), list(used[col_id].drop_duplicates())
        if len(row_values) != 2 or len(col_values) != 2 or row_positive not in row_values or col_positive not in col_values:
            raise MethodNotApplicableError({"reason": "fisher_requires_explicit_2x2"})
        row_negative = next(x for x in row_values if x != row_positive)
        col_negative = next(x for x in col_values if x != col_positive)
        matrix = np.array([
            [int(((used[row_id] == row_positive) & (used[col_id] == col_positive)).sum()), int(((used[row_id] == row_positive) & (used[col_id] == col_negative)).sum())],
            [int(((used[row_id] == row_negative) & (used[col_id] == col_positive)).sum()), int(((used[row_id] == row_negative) & (used[col_id] == col_negative)).sum())],
        ])
        if (matrix.sum(axis=0) == 0).any() or (matrix.sum(axis=1) == 0).any():
            raise MethodNotApplicableError({"reason": "fisher_zero_margin"})
        result = stats.fisher_exact(matrix, alternative="two-sided")
        odds = float(result.statistic)
        odds_metric = _stateful_metric("odds_ratio", odds, sample_size=int(matrix.sum()))
        p_metric = _metric("p_value", float(result.pvalue), sample_size=int(matrix.sum()))
        table = ResultTable(str(uuid4()), ("row", "column", "count"), (("positive", "positive", int(matrix[0,0])), ("positive", "negative", int(matrix[0,1])), ("negative", "positive", int(matrix[1,0])), ("negative", "negative", int(matrix[1,1]))))
        return self._draft(spec, metrics=(odds_metric, p_metric), tables=(table,), sample={"rows_used": len(used), "rows_excluded": len(frame)-len(used)})

    def _draft(self, spec: AnalysisSpec, *, metrics=(), findings=(), tables=(), sample=None) -> ResultDraft:
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            metrics=tuple(metrics), findings=tuple(findings), tables=tuple(tables),
            methodology={"module": "comparison", "module_version": self.MODULE_VERSION, "operation": spec.operation},
            sample_summary=sample or {},
            provenance={"input_version_id": spec.input_version_id, "spec_hash": spec.spec_hash},
        )


def _metric(name: str, value: float, dimensions: dict[str, object] | None = None, sample_size: int | None = None, interval: tuple[float,float] | None = None) -> Metric:
    if not math.isfinite(value):
        return _stateful_metric(name, value, dimensions=dimensions, sample_size=sample_size)
    return Metric(str(uuid4()), name, MetricValueState.FINITE, value=value, dimensions=dimensions or {}, sample_size=sample_size, interval=interval)


def _stateful_metric(name: str, value: float, dimensions: dict[str, object] | None = None, sample_size: int | None = None) -> Metric:
    if math.isfinite(value):
        return Metric(str(uuid4()), name, MetricValueState.FINITE, value=value, dimensions=dimensions or {}, sample_size=sample_size)
    state = MetricValueState.POSITIVE_INFINITY if value > 0 else MetricValueState.NEGATIVE_INFINITY
    return Metric(str(uuid4()), name, state, dimensions=dimensions or {}, sample_size=sample_size)


def _safe(value: object) -> object:
    if value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value
