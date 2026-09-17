"""Preparation module adapter for the closed V2 analysis contract.

The mutating preview/apply lifecycle remains an application-service concern.  This
module performs a result-only full-data recipe preview so preparation participates
in the same deterministic module registry without moving the dataset head or
writing catalog state from analysis code.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from ..data.table_access import TableAccess
from ..domain.codec import utc_now
from ..domain.errors import DataError, MethodNotApplicableError, SchemaError
from ..domain.plans import AnalysisModuleId, AnalysisSpec, LearningScope
from ..domain.results import Finding, FindingSeverity, Metric, MetricValueState, ResultOutcome, ResultTable
from ..preparation.engine import PreparationEngine
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .contract import AnalysisDescription, ApplicabilityReport, ExecutionContext, ResourceEstimate, ResultDraft, ValidationIssue


class PreparationModule:
    MODULE_VERSION = "2.0"
    OPERATIONS = ("preview_recipe",)

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.engine = PreparationEngine(TableAccess(catalog, artifact_store), artifact_store)

    def describe(self) -> AnalysisDescription:
        return AnalysisDescription(
            AnalysisModuleId.PREPARATION,
            self.MODULE_VERSION,
            self.OPERATIONS,
            parameter_schema={"preview_recipe": {"required": ("recipe_id",)}},
        )

    def validate(self, spec: AnalysisSpec) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if spec.module_id is not AnalysisModuleId.PREPARATION:
            issues.append(ValidationIssue("preparation.module_mismatch"))
        if spec.operation not in self.OPERATIONS:
            issues.append(ValidationIssue("preparation.unsupported_operation"))
        recipe_id = spec.parameters.get("recipe_id")
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            issues.append(ValidationIssue("preparation.recipe_id_required"))
        return tuple(issues)

    def check_applicability(self, spec: AnalysisSpec) -> ApplicabilityReport:
        issues = self.validate(spec)
        if issues:
            return ApplicabilityReport(blocking_issues=issues)
        try:
            recipe = self.catalog.get_preparation_recipe(str(spec.parameters["recipe_id"]))
            version = self.catalog.get_version(spec.input_version_id)
            dataset = self.catalog.get_dataset(version.dataset_id)
        except Exception:
            return ApplicabilityReport(
                blocking_issues=(ValidationIssue("preparation.recipe_or_dataset_missing"),)
            )
        blocking: list[ValidationIssue] = []
        if recipe.project_id != spec.project_id:
            blocking.append(ValidationIssue("preparation.project_mismatch"))
        if recipe.base_version_id != spec.input_version_id:
            blocking.append(ValidationIssue("preparation.base_version_mismatch"))
        if dataset.head_version_id != spec.input_version_id:
            blocking.append(ValidationIssue("preparation.base_not_current_head"))
        train_only = tuple(
            step.step_id for step in recipe.ordered_steps if step.learning_scope is LearningScope.TRAIN_ONLY
        )
        if train_only:
            blocking.append(
                ValidationIssue("preparation.train_only_step_not_previewable", {"step_ids": train_only})
            )
        return ApplicabilityReport(blocking_issues=tuple(blocking))

    def estimate_resources(self, spec: AnalysisSpec) -> ResourceEstimate:
        version = self.catalog.get_version(spec.input_version_id)
        cells = max(1, version.row_count * max(1, version.column_count))
        return ResourceEstimate(
            memory_bytes=max(16_000_000, min(4_000_000_000, cells * 48)),
            disk_bytes=max(4_000_000, min(4_000_000_000, cells * 24)),
            duration_seconds=max(0.1, cells / 2_000_000),
            parallelism=1,
        )

    def run(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        issues = self.validate(spec)
        if issues:
            raise SchemaError(
                {"reason": "invalid_preparation_spec", "codes": tuple(issue.code for issue in issues)}
            )
        applicability = self.check_applicability(spec)
        applicability.require_runnable()
        context.raise_if_cancelled()

        recipe = self.catalog.get_preparation_recipe(str(spec.parameters["recipe_id"]))
        version = self.catalog.get_version(spec.input_version_id)
        dataset = self.catalog.get_dataset(version.dataset_id)
        if dataset.head_version_id != spec.input_version_id:
            raise MethodNotApplicableError({"reason": "preparation_base_not_current_head"})

        computation = self.engine.preview(
            recipe,
            preview_id=context.run_id,
            expected_head_revision=dataset.head_revision,
            expires_at=utc_now() + timedelta(hours=1),
        )
        context.raise_if_cancelled()
        preview = computation.preview

        metrics = (
            _count_metric("rows_before", preview.before_row_count),
            _count_metric("rows_after", preview.after_row_count),
            _count_metric("columns_before", preview.before_column_count),
            _count_metric("columns_after", preview.after_column_count),
        )
        rows = tuple(
            (
                step.step_id,
                step.affected_row_count,
                step.affected_cell_count,
                tuple(step.warnings),
            )
            for step in preview.step_results
        )
        table = ResultTable(
            str(uuid4()),
            ("step_id", "affected_rows", "affected_cells", "warnings"),
            rows,
        )
        findings = tuple(
            Finding(
                str(uuid4()),
                "preparation.preview_warning",
                FindingSeverity.WARNING,
                parameters={"warning": warning},
            )
            for warning in preview.warnings
        )
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            metrics=metrics,
            findings=findings,
            tables=(table,),
            artifacts=(computation.candidate_artifact,),
            methodology={
                "module": "preparation",
                "module_version": self.MODULE_VERSION,
                "operation": "preview_recipe",
                "full_dataset_preview": True,
                "mutates_dataset_head": False,
            },
            sample_summary={
                "rows_before": preview.before_row_count,
                "rows_after": preview.after_row_count,
                "sample_change_count": len(preview.sample_changes),
            },
            provenance={
                "input_version_id": spec.input_version_id,
                "recipe_id": recipe.recipe_id,
                "recipe_hash": recipe.recipe_hash,
                "expected_head_revision": preview.expected_head_revision,
                "candidate_artifact_id": preview.candidate_artifact_id,
                "content_fingerprint": preview.content_fingerprint,
                "schema_hash": preview.schema_hash,
            },
        )


def _count_metric(name: str, value: int) -> Metric:
    return Metric(
        metric_id=str(uuid4()),
        name=name,
        value_state=MetricValueState.FINITE,
        value=value,
        unit="count",
    )
