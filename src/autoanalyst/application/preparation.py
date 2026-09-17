"""Preparation recipe, full preview, and atomic apply orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from uuid import uuid4

from ..data.table_access import TableAccess
from ..domain.codec import fingerprint, utc_now
from ..domain.datasets import DatasetVersion, DatasetVersionKind
from ..domain.errors import SchemaError
from ..domain.plans import LearningScope, PreparationRecipe, PreparationStep
from ..preparation.engine import PreparationEngine, PreparationPreview, PreviewStatus
from ..preparation.operations import (
    affected_column_ids,
    effective_learning_scope,
    operation_definition,
    validate_parameters,
    validate_step,
)
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog


class PreparationService:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.table_access = TableAccess(catalog, artifact_store)
        self.engine = PreparationEngine(self.table_access, artifact_store)

    def create_recipe(
        self,
        *,
        project_id: str,
        base_version_id: str,
        steps: Sequence[PreparationStep | Mapping[str, object]],
    ) -> PreparationRecipe:
        if not steps:
            raise SchemaError({"reason": "preparation_steps_required"})
        project = self.catalog.get_project(project_id)
        base = self.catalog.get_version(base_version_id)
        dataset = self.catalog.get_dataset(base.dataset_id)
        if dataset.project_id != project.project_id:
            raise SchemaError({"reason": "preparation_project_mismatch"})
        normalized: list[PreparationStep] = []
        for position, item in enumerate(steps):
            if isinstance(item, PreparationStep):
                step = item
                if step.position != position:
                    raise SchemaError({"reason": "preparation_step_position_mismatch"})
            else:
                operation = str(item.get("operation", ""))
                definition = operation_definition(operation)
                raw_parameters = item.get("parameters", {})
                if not isinstance(raw_parameters, Mapping):
                    raise SchemaError({"reason": "operation_parameters_must_be_mapping"})
                parameters = validate_parameters(operation, dict(raw_parameters))
                requested_scope = item.get("learning_scope")
                expected_scope = effective_learning_scope(operation, parameters)
                try:
                    scope = (
                        LearningScope(requested_scope)
                        if requested_scope is not None
                        else expected_scope
                    )
                except ValueError as exc:
                    raise SchemaError({"reason": "invalid_learning_scope", "operation": operation}) from exc
                if scope not in {expected_scope, LearningScope.TRAIN_ONLY}:
                    raise SchemaError(
                        {"reason": "invalid_learning_scope", "operation": operation, "scope": scope.value}
                    )
                step = PreparationStep(
                    step_id=str(uuid4()),
                    position=position,
                    operation=operation,
                    operation_version=definition.version,
                    parameters=parameters,
                    affected_column_ids=affected_column_ids(operation, parameters),
                    learning_scope=scope,
                )
            validate_step(step)
            normalized.append(step)
        recipe_hash = fingerprint(
            {
                "base_version_id": base_version_id,
                "steps": [
                    {
                        "position": step.position,
                        "operation": step.operation,
                        "operation_version": step.operation_version,
                        "parameters": step.parameters,
                        "affected_column_ids": step.affected_column_ids,
                        "learning_scope": step.learning_scope.value,
                    }
                    for step in normalized
                ],
            }
        )
        recipe = PreparationRecipe(
            recipe_id=str(uuid4()),
            project_id=project_id,
            base_version_id=base_version_id,
            ordered_steps=tuple(normalized),
            recipe_hash=recipe_hash,
            created_at=utc_now(),
        )
        self.catalog.insert_preparation_recipe(recipe)
        return recipe

    def get_recipe(self, recipe_id: str) -> PreparationRecipe:
        return self.catalog.get_preparation_recipe(recipe_id)

    def preview_recipe(
        self,
        recipe_id: str,
        *,
        ttl: timedelta = timedelta(hours=1),
    ) -> PreparationPreview:
        if ttl.total_seconds() <= 0:
            raise SchemaError({"reason": "preview_ttl_must_be_positive"})
        recipe = self.get_recipe(recipe_id)
        base = self.catalog.get_version(recipe.base_version_id)
        dataset = self.catalog.get_dataset(base.dataset_id)
        if dataset.head_version_id != recipe.base_version_id:
            raise SchemaError({"reason": "preparation_recipe_base_not_head"})
        preview_id = str(uuid4())
        computation = self.engine.preview(
            recipe,
            preview_id=preview_id,
            expected_head_revision=dataset.head_revision,
            expires_at=utc_now() + ttl,
        )
        self.catalog.insert_preparation_preview(
            computation.preview, computation.candidate_artifact
        )
        return computation.preview

    def get_preview(self, preview_id: str) -> PreparationPreview:
        return self.catalog.get_preparation_preview(preview_id)

    def apply_preview(self, preview_id: str) -> DatasetVersion:
        preview = self.get_preview(preview_id)
        if preview.status is PreviewStatus.APPLIED:
            return self.catalog.get_version(preview.applied_version_id)
        base = self.catalog.get_version(preview.base_version_id)
        version = DatasetVersion(
            version_id=str(uuid4()),
            dataset_id=base.dataset_id,
            kind=DatasetVersionKind.PREPARED,
            created_at=utc_now(),
            created_by_run_id=preview.preview_id,
            table_artifact_id=preview.candidate_artifact_id,
            row_count=preview.after_row_count,
            column_count=preview.after_column_count,
            schema_hash=preview.schema_hash,
            content_fingerprint=preview.content_fingerprint,
            recipe_id=preview.recipe_id,
            parse_contract=base.parse_contract,
        )
        return self.catalog.apply_preparation_preview(
            preview_id,
            version,
            verify_artifact=self.artifact_store.verify,
            now=utc_now(),
        )

    def get_lineage(self, version_id: str) -> tuple[dict[str, object], ...]:
        return self.catalog.get_version_lineage(version_id)
