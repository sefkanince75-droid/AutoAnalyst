from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from autoanalyst.data import CSVIngestor
from autoanalyst.domain.codec import encode_typed_label
from autoanalyst.domain.errors import MethodNotApplicableError, SchemaError


def _preview(workspace, operation: str, parameters: dict, **step_options):
    recipe = workspace.preparation.create_recipe(
        project_id=workspace.project.project_id,
        base_version_id=workspace.imported.version.version_id,
        steps=[{"operation": operation, "parameters": parameters, **step_options}],
    )
    return workspace.preparation.preview_recipe(recipe.recipe_id)


def _candidate_table(workspace, preview):
    artifact = workspace.catalog.get_artifact(preview.candidate_artifact_id)
    return pq.read_table(workspace.store.resolve_relative_path(artifact.relative_path))


def test_drop_last_user_column_and_system_drop_are_rejected(phase3_workspace) -> None:
    user_ids = tuple(
        row["column_id"]
        for row in phase3_workspace.catalog.list_columns(phase3_workspace.imported.version.version_id)
        if not row["is_system"]
    )
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[{"operation": "drop_columns", "parameters": {"column_ids": user_ids}}],
    )
    with pytest.raises(SchemaError) as last:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert last.value.context["reason"] == "at_least_one_user_column_required"

    system_id = next(
        row["column_id"]
        for row in phase3_workspace.catalog.list_columns(phase3_workspace.imported.version.version_id)
        if row["is_system"]
    )
    system_recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[{"operation": "drop_columns", "parameters": {"column_ids": [system_id]}}],
    )
    with pytest.raises(SchemaError) as system:
        phase3_workspace.preparation.preview_recipe(system_recipe.recipe_id)
    assert system.value.context["reason"] == "system_column_cannot_be_dropped"


def test_rename_preserves_column_id_and_duplicate_name_is_rejected(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    preview = _preview(
        phase3_workspace,
        "rename_column",
        {"column_id": text["column_id"], "new_name": "description"},
    )
    renamed = next(column for column in preview.candidate_columns if column.column_id == text["column_id"])
    assert renamed.display_name == "description"

    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "rename_column",
                "parameters": {"column_id": text["column_id"], "new_name": "group"},
            }
        ],
    )
    with pytest.raises(SchemaError) as duplicate:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert duplicate.value.context["reason"] == "duplicate_column_names"


def test_cast_strict_fails_and_invalid_to_null_is_explicit(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    strict = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "cast_column",
                "parameters": {
                    "column_id": text["column_id"],
                    "target_type": "numeric",
                    "failure_policy": "strict",
                },
            }
        ],
    )
    with pytest.raises(SchemaError) as failed:
        phase3_workspace.preparation.preview_recipe(strict.recipe_id)
    assert failed.value.context["reason"] == "cast_failure"

    preview = _preview(
        phase3_workspace,
        "cast_column",
        {
            "column_id": text["column_id"],
            "target_type": "numeric",
            "failure_policy": "invalid_to_null",
        },
    )
    column = next(item for item in preview.candidate_columns if item.column_id == text["column_id"])
    values = _candidate_table(phase3_workspace, preview)[column.physical_name].to_pylist()
    assert values == [1.0, None, None, None, None]
    assert column.semantic_hint == "numeric"


def test_learned_fills_are_typed_deterministic_and_reject_wrong_schema(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    group = phase3_workspace.column("group")
    text = phase3_workspace.column("text")

    median = _preview(
        phase3_workspace,
        "fill_missing",
        {"column_ids": [num["column_id"]], "strategy": "median"},
    )
    resolved_median = median.step_results[0].resolved_values[num["column_id"]]
    assert resolved_median["value"] == 3.0

    mode = _preview(
        phase3_workspace,
        "fill_missing",
        {"column_ids": [group["column_id"]], "strategy": "mode"},
    )
    assert mode.step_results[0].resolved_values[group["column_id"]]["value"] == "a"

    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "fill_missing",
                "parameters": {"column_ids": [text["column_id"]], "strategy": "mean"},
            }
        ],
    )
    with pytest.raises(SchemaError) as non_numeric:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert non_numeric.value.context["reason"] == "numeric_fill_required"


def test_constant_fill_preserves_typed_value(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    preview = _preview(
        phase3_workspace,
        "fill_missing",
        {
            "column_ids": [text["column_id"]],
            "strategy": "constant",
            "value": encode_typed_label("missing"),
        },
    )
    column = next(item for item in preview.candidate_columns if item.column_id == text["column_id"])
    assert _candidate_table(phase3_workspace, preview)[column.physical_name].to_pylist()[1] == "missing"
    assert preview.step_results[0].resolved_values[text["column_id"]]["type"] == "string"


def test_duplicate_removal_uses_row_order_deterministically(phase3_workspace) -> None:
    group = phase3_workspace.column("group")
    preview = _preview(
        phase3_workspace,
        "remove_duplicates",
        {"column_ids": [group["column_id"]], "keep": "first"},
    )
    table = _candidate_table(phase3_workspace, preview)
    assert table["__aa_internal_row_order__"].to_pylist() == [0, 2, 4]
    assert preview.step_results[0].affected_row_count == 2


def test_typed_replace_does_not_conflate_string_integer_and_boolean(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    num = phase3_workspace.column("num")
    preview = _preview(
        phase3_workspace,
        "replace_values",
        {
            "column_id": text["column_id"],
            "mapping": [
                {"from": encode_typed_label("1"), "to": encode_typed_label("one")},
            ],
        },
    )
    column = next(item for item in preview.candidate_columns if item.column_id == text["column_id"])
    assert _candidate_table(phase3_workspace, preview)[column.physical_name].to_pylist()[0] == "one"

    numeric_mapping = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "replace_values",
                "parameters": {
                    "column_id": text["column_id"],
                    "mapping": [
                        {"from": encode_typed_label(1), "to": encode_typed_label("wrong")},
                    ],
                },
            }
        ],
    )
    with pytest.raises(SchemaError) as mismatch:
        phase3_workspace.preparation.preview_recipe(numeric_mapping.recipe_id)
    assert mismatch.value.context["reason"] == "typed_value_schema_mismatch"

    boolean_mapping = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "replace_values",
                "parameters": {
                    "column_id": num["column_id"],
                    "mapping": [
                        {"from": encode_typed_label(True), "to": encode_typed_label(0)},
                    ],
                },
            }
        ],
    )
    with pytest.raises(SchemaError) as boolean_mismatch:
        phase3_workspace.preparation.preview_recipe(boolean_mapping.recipe_id)
    assert boolean_mismatch.value.context["reason"] == "typed_value_schema_mismatch"


def test_non_finite_numeric_values_become_null(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    preview = _preview(
        phase3_workspace,
        "replace_non_finite",
        {"column_ids": [num["column_id"]]},
    )
    column = next(item for item in preview.candidate_columns if item.column_id == num["column_id"])
    values = _candidate_table(phase3_workspace, preview)[column.physical_name].to_pylist()
    assert values[-1] is None
    assert preview.step_results[0].affected_cell_count == 1


def test_append_rejects_incompatible_schema(phase3_workspace) -> None:
    other_dataset = phase3_workspace.datasets.create(phase3_workspace.project.project_id, "Other")
    other = CSVIngestor(phase3_workspace.catalog, phase3_workspace.store).import_csv(
        project_id=phase3_workspace.project.project_id,
        dataset_id=other_dataset.dataset_id,
        source=b"num,text,flag,group\n9,q,true,z\n",
        original_name="other.csv",
    )
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "append_rows",
                "parameters": {"input_version_ids": [other.version.version_id]},
            }
        ],
    )
    with pytest.raises(SchemaError) as incompatible:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert incompatible.value.context["reason"] == "append_schema_incompatible"


def test_append_rejects_duplicate_or_base_version_inputs(phase3_workspace) -> None:
    base_id = phase3_workspace.imported.version.version_id
    with pytest.raises(SchemaError) as duplicate:
        phase3_workspace.preparation.create_recipe(
            project_id=phase3_workspace.project.project_id,
            base_version_id=base_id,
            steps=[
                {
                    "operation": "append_rows",
                    "parameters": {"input_version_ids": [base_id, base_id]},
                }
            ],
        )
    assert duplicate.value.context["reason"] == "duplicate_input_version_ids"

    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=base_id,
        steps=[
            {"operation": "append_rows", "parameters": {"input_version_ids": [base_id]}}
        ],
    )
    with pytest.raises(SchemaError) as base:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert base.value.context["reason"] == "base_version_cannot_be_appended"


def test_train_only_step_is_rejected_by_preparation_engine(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "fill_missing",
                "parameters": {
                    "column_ids": [text["column_id"]],
                    "strategy": "constant",
                    "value": encode_typed_label("future-train-value"),
                },
                "learning_scope": "train_only",
            }
        ],
    )
    with pytest.raises(MethodNotApplicableError) as rejected:
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert rejected.value.context["reason"] == "train_only_preparation"
