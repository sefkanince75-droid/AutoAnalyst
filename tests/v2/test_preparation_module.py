from __future__ import annotations

from uuid import uuid4

from autoanalyst.analyses.preparation import PreparationModule
from autoanalyst.bootstrap import build_registry
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage.runs import RunStore


class _Event:
    def is_set(self) -> bool:
        return False


def test_registry_contains_exactly_the_four_closed_v2_modules(phase3_workspace) -> None:
    registry = build_registry(phase3_workspace.catalog, phase3_workspace.store)
    assert registry.module_ids == (
        "binary_classification",
        "comparison",
        "preparation",
        "profiling",
    )


def test_preparation_module_preview_publishes_result_without_moving_head(phase3_workspace) -> None:
    ws = phase3_workspace
    num_id = str(ws.column("num")["column_id"])
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.imported.version.version_id,
        steps=(
            {
                "operation": "fill_missing",
                "parameters": {
                    "column_ids": [num_id],
                    "strategy": "constant",
                    "value": {"type": "integer", "value": 0},
                },
            },
        ),
    )
    before = ws.datasets.get(ws.dataset.dataset_id)
    payload = {
        "module": "preparation",
        "operation": "preview_recipe",
        "version": ws.imported.version.version_id,
        "recipe_id": recipe.recipe_id,
    }
    spec = AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=ws.project.project_id,
        module_id=AnalysisModuleId.PREPARATION,
        module_version="2.0",
        operation="preview_recipe",
        input_version_id=ws.imported.version.version_id,
        column_roles=(),
        parameters={"recipe_id": recipe.recipe_id},
        seed=42,
        resource_budget=ResourceBudget(1_000_000_000, 1_000_000_000, 60, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )
    store = RunStore(ws.catalog)
    coordinator = ExecutionCoordinator(store)
    run = coordinator.create_run(spec, request_key="preparation-preview-module")
    completed = coordinator.execute_inline(
        run.run_id,
        PreparationModule(ws.catalog, ws.store),
        cancellation_event=_Event(),
    )

    assert completed.status is RunStatus.COMPLETED
    result = store.result_for_run(completed.run_id)
    assert result is not None
    assert result.module_id is AnalysisModuleId.PREPARATION
    assert result.provenance["recipe_id"] == recipe.recipe_id
    assert result.provenance["candidate_artifact_id"]
    assert result.metrics

    after = ws.datasets.get(ws.dataset.dataset_id)
    assert after.head_version_id == before.head_version_id
    assert after.head_revision == before.head_revision
