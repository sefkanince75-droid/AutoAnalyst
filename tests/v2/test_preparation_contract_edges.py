from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from autoanalyst.analyses.contract import ApplicabilityReport, ExecutionContext
from autoanalyst.analyses.preparation import PreparationModule
from autoanalyst.data import CSVIngestor
from autoanalyst.domain.codec import encode_typed_label, fingerprint, utc_now
from autoanalyst.domain.errors import MethodNotApplicableError, SchemaError
from autoanalyst.domain.plans import (
    AnalysisModuleId,
    AnalysisSpec,
    LearningScope,
    PreparationStep,
    ResourceBudget,
)


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _analysis_spec(
    workspace,
    *,
    module_id: AnalysisModuleId = AnalysisModuleId.PREPARATION,
    operation: str = "preview_recipe",
    project_id: str | None = None,
    input_version_id: str | None = None,
    recipe_id: str | None = None,
) -> AnalysisSpec:
    version_id = input_version_id or workspace.imported.version.version_id
    parameters = {} if recipe_id is None else {"recipe_id": recipe_id}
    payload = {
        "module": module_id.value,
        "operation": operation,
        "version": version_id,
        "parameters": parameters,
    }
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project_id or workspace.project.project_id,
        module_id=module_id,
        module_version="2.0",
        operation=operation,
        input_version_id=version_id,
        column_roles=(),
        parameters=parameters,
        seed=42,
        resource_budget=ResourceBudget(1_000_000_000, 1_000_000_000, 60, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _rename_recipe(workspace):
    text = workspace.column("text")
    return workspace.preparation.create_recipe(
        project_id=workspace.project.project_id,
        base_version_id=workspace.imported.version.version_id,
        steps=[
            {
                "operation": "rename_column",
                "parameters": {
                    "column_id": text["column_id"],
                    "new_name": "renamed",
                },
            }
        ],
    )


def test_preparation_service_rejects_empty_steps_and_cross_project_base(
    phase3_workspace,
) -> None:
    workspace = phase3_workspace

    with pytest.raises(SchemaError) as empty:
        workspace.preparation.create_recipe(
            project_id=workspace.project.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[],
        )
    assert empty.value.context["reason"] == "preparation_steps_required"

    other = workspace.projects.create("Other project")
    with pytest.raises(SchemaError) as mismatch:
        workspace.preparation.create_recipe(
            project_id=other.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[
                {
                    "operation": "rename_column",
                    "parameters": {
                        "column_id": workspace.column("text")["column_id"],
                        "new_name": "x",
                    },
                }
            ],
        )
    assert mismatch.value.context["reason"] == "preparation_project_mismatch"


def test_preparation_service_rejects_invalid_step_metadata(phase3_workspace) -> None:
    workspace = phase3_workspace
    text_id = str(workspace.column("text")["column_id"])
    positioned = PreparationStep(
        step_id=str(uuid4()),
        position=1,
        operation="rename_column",
        operation_version="1.0",
        parameters={"column_id": text_id, "new_name": "x"},
        affected_column_ids=(text_id,),
        learning_scope=LearningScope.NONE,
    )

    with pytest.raises(SchemaError) as position:
        workspace.preparation.create_recipe(
            project_id=workspace.project.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[positioned],
        )
    assert position.value.context["reason"] == "preparation_step_position_mismatch"

    with pytest.raises(SchemaError) as parameters:
        workspace.preparation.create_recipe(
            project_id=workspace.project.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[{"operation": "rename_column", "parameters": []}],
        )
    assert parameters.value.context["reason"] == "operation_parameters_must_be_mapping"


def test_preparation_service_rejects_invalid_learning_scope_requests(
    phase3_workspace,
) -> None:
    workspace = phase3_workspace
    num_id = str(workspace.column("num")["column_id"])

    with pytest.raises(SchemaError) as unknown:
        workspace.preparation.create_recipe(
            project_id=workspace.project.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[
                {
                    "operation": "fill_missing",
                    "parameters": {
                        "column_ids": [num_id],
                        "strategy": "median",
                    },
                    "learning_scope": "unknown",
                }
            ],
        )
    assert unknown.value.context["reason"] == "invalid_learning_scope"

    with pytest.raises(SchemaError) as incompatible:
        workspace.preparation.create_recipe(
            project_id=workspace.project.project_id,
            base_version_id=workspace.imported.version.version_id,
            steps=[
                {
                    "operation": "fill_missing",
                    "parameters": {
                        "column_ids": [num_id],
                        "strategy": "median",
                    },
                    "learning_scope": "none",
                }
            ],
        )
    assert incompatible.value.context["reason"] == "invalid_learning_scope"


def test_preview_rejects_nonpositive_ttl_and_recipe_whose_base_is_not_head(
    phase3_workspace,
) -> None:
    workspace = phase3_workspace
    recipe = _rename_recipe(workspace)

    with pytest.raises(SchemaError) as ttl:
        workspace.preparation.preview_recipe(
            recipe.recipe_id,
            ttl=timedelta(seconds=0),
        )
    assert ttl.value.context["reason"] == "preview_ttl_must_be_positive"

    CSVIngestor(workspace.catalog, workspace.store).import_csv(
        project_id=workspace.project.project_id,
        dataset_id=workspace.dataset.dataset_id,
        source=b"value\nnew\n",
        original_name="new-head.csv",
    )
    with pytest.raises(SchemaError) as stale:
        workspace.preparation.preview_recipe(recipe.recipe_id)
    assert stale.value.context["reason"] == "preparation_recipe_base_not_head"


def test_preparation_module_validation_and_missing_recipe_paths(
    phase3_workspace,
) -> None:
    workspace = phase3_workspace
    module = PreparationModule(workspace.catalog, workspace.store)

    wrong_module = _analysis_spec(
        workspace,
        module_id=AnalysisModuleId.PROFILING,
        operation="profile",
    )
    codes = {issue.code for issue in module.validate(wrong_module)}
    assert {
        "preparation.module_mismatch",
        "preparation.unsupported_operation",
        "preparation.recipe_id_required",
    } <= codes
    report = module.check_applicability(wrong_module)
    assert report.blocking_issues

    missing_recipe = _analysis_spec(workspace, recipe_id=str(uuid4()))
    report = module.check_applicability(missing_recipe)
    assert {issue.code for issue in report.blocking_issues} == {
        "preparation.recipe_or_dataset_missing"
    }

    with pytest.raises(SchemaError) as invalid_run:
        module.run(wrong_module, None)
    assert invalid_run.value.context["reason"] == "invalid_preparation_spec"


def test_preparation_module_reports_project_and_base_mismatch(phase3_workspace) -> None:
    workspace = phase3_workspace
    module = PreparationModule(workspace.catalog, workspace.store)
    recipe = _rename_recipe(workspace)
    other_project = workspace.projects.create("Analysis mismatch")

    wrong_project = _analysis_spec(
        workspace,
        project_id=other_project.project_id,
        recipe_id=recipe.recipe_id,
    )
    report = module.check_applicability(wrong_project)
    assert "preparation.project_mismatch" in {
        issue.code for issue in report.blocking_issues
    }

    newer = CSVIngestor(workspace.catalog, workspace.store).import_csv(
        project_id=workspace.project.project_id,
        dataset_id=workspace.dataset.dataset_id,
        source=b"value\nnew\n",
        original_name="new-head.csv",
    )
    wrong_base = _analysis_spec(
        workspace,
        input_version_id=newer.version.version_id,
        recipe_id=recipe.recipe_id,
    )
    report = module.check_applicability(wrong_base)
    assert "preparation.base_version_mismatch" in {
        issue.code for issue in report.blocking_issues
    }

    stale_base = _analysis_spec(workspace, recipe_id=recipe.recipe_id)
    report = module.check_applicability(stale_base)
    assert "preparation.base_not_current_head" in {
        issue.code for issue in report.blocking_issues
    }


def test_preparation_module_blocks_train_only_recipe(phase3_workspace) -> None:
    workspace = phase3_workspace
    text_id = str(workspace.column("text")["column_id"])
    recipe = workspace.preparation.create_recipe(
        project_id=workspace.project.project_id,
        base_version_id=workspace.imported.version.version_id,
        steps=[
            {
                "operation": "fill_missing",
                "parameters": {
                    "column_ids": [text_id],
                    "strategy": "constant",
                    "value": encode_typed_label("future"),
                },
                "learning_scope": "train_only",
            }
        ],
    )
    module = PreparationModule(workspace.catalog, workspace.store)
    spec = _analysis_spec(workspace, recipe_id=recipe.recipe_id)

    report = module.check_applicability(spec)
    assert "preparation.train_only_step_not_previewable" in {
        issue.code for issue in report.blocking_issues
    }


def test_preparation_module_rechecks_head_after_applicability_for_race(
    phase3_workspace,
    monkeypatch,
) -> None:
    workspace = phase3_workspace
    recipe = _rename_recipe(workspace)
    module = PreparationModule(workspace.catalog, workspace.store)
    spec = _analysis_spec(workspace, recipe_id=recipe.recipe_id)

    CSVIngestor(workspace.catalog, workspace.store).import_csv(
        project_id=workspace.project.project_id,
        dataset_id=workspace.dataset.dataset_id,
        source=b"value\nnew\n",
        original_name="race-head.csv",
    )
    monkeypatch.setattr(
        module,
        "check_applicability",
        lambda _spec: ApplicabilityReport(),
    )
    context = ExecutionContext(
        run_id=str(uuid4()),
        input_version_id=spec.input_version_id,
        seed=42,
        cancellation=_NeverCancelled(),
    )

    with pytest.raises(MethodNotApplicableError) as raced:
        module.run(spec, context)
    assert raced.value.context["reason"] == "preparation_base_not_current_head"
