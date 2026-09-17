from __future__ import annotations

import pytest

from autoanalyst.domain.codec import encode_typed_label
from autoanalyst.domain.errors import SchemaError


def test_multistep_preview_runs_full_data_and_reports_counts(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    text = phase3_workspace.column("text")
    flag = phase3_workspace.column("flag")
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "filter_rows",
                "parameters": {"column_id": num["column_id"], "operator": "gte", "value": 2.0},
            },
            {
                "operation": "fill_missing",
                "parameters": {
                    "column_ids": [text["column_id"]],
                    "strategy": "constant",
                    "value": encode_typed_label("filled"),
                },
            },
            {
                "operation": "drop_columns",
                "parameters": {"column_ids": [flag["column_id"]]},
            },
        ],
    )

    preview = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)

    assert preview.before_row_count == 5
    assert preview.after_row_count == 3
    assert preview.before_column_count == 4
    assert preview.after_column_count == 3
    assert [result.affected_row_count for result in preview.step_results] == [2, 1, 3]
    assert len(preview.sample_changes) <= 100
    assert preview.base_version_id == phase3_workspace.imported.version.version_id
    assert preview.recipe_hash == recipe.recipe_hash


def test_final_step_failure_never_moves_head_or_publishes_preview(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    text = phase3_workspace.column("text")
    before = phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id)
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "filter_rows",
                "parameters": {"column_id": num["column_id"], "operator": "gt", "value": 1.0},
            },
            {
                "operation": "fill_missing",
                "parameters": {"column_ids": [text["column_id"]], "strategy": "mean"},
            },
        ],
    )

    with pytest.raises(SchemaError):
        phase3_workspace.preparation.preview_recipe(recipe.recipe_id)

    after = phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id)
    assert after == before
    with phase3_workspace.catalog.connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM preparation_previews WHERE recipe_id = ?", (recipe.recipe_id,)
        ).fetchone()[0] == 0


def test_candidate_artifact_is_verified_and_preview_round_trips(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "drop_missing_rows",
                "parameters": {"column_ids": [text["column_id"]]},
            }
        ],
    )
    preview = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    artifact = phase3_workspace.catalog.get_artifact(preview.candidate_artifact_id)

    assert phase3_workspace.store.verify(artifact)
    assert phase3_workspace.preparation.get_preview(preview.preview_id) == preview
    assert preview.before_row_count == 5
    assert preview.after_row_count == 4


def test_change_sample_is_deterministic_for_same_recipe_and_head(phase3_workspace) -> None:
    group = phase3_workspace.column("group")
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "remove_duplicates",
                "parameters": {"column_ids": [group["column_id"]]},
            }
        ],
    )
    first = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    second = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    assert first.sample_changes == second.sample_changes

    equivalent = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "remove_duplicates",
                "parameters": {"column_ids": [group["column_id"]]},
            }
        ],
    )
    equivalent_preview = phase3_workspace.preparation.preview_recipe(equivalent.recipe_id)
    assert equivalent.recipe_hash == recipe.recipe_hash
    assert equivalent_preview.sample_changes == first.sample_changes
    assert equivalent_preview.content_fingerprint == first.content_fingerprint


def test_recipe_hash_is_order_sensitive_and_dictionary_order_independent(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    text = phase3_workspace.column("text")
    first_steps = [
        {
            "operation": "filter_rows",
            "parameters": {"column_id": num["column_id"], "operator": "gte", "value": 2.0},
        },
        {
            "operation": "drop_missing_rows",
            "parameters": {"column_ids": [text["column_id"]]},
        },
    ]
    same_steps_different_dict_order = [
        {
            "parameters": {"value": 2.0, "operator": "gte", "column_id": num["column_id"]},
            "operation": "filter_rows",
        },
        {
            "parameters": {"column_ids": [text["column_id"]]},
            "operation": "drop_missing_rows",
        },
    ]
    first = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=first_steps,
    )
    same = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=same_steps_different_dict_order,
    )
    reversed_recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=list(reversed(first_steps)),
    )

    assert first.recipe_hash == same.recipe_hash
    assert first.recipe_hash != reversed_recipe.recipe_hash


def test_rename_cast_drop_schema_evolution_preserves_remaining_ids(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    text = phase3_workspace.column("text")
    flag = phase3_workspace.column("flag")
    original_ids = {
        row["display_name"]: row["column_id"]
        for row in phase3_workspace.catalog.list_columns(phase3_workspace.imported.version.version_id)
        if not row["is_system"]
    }
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "rename_column",
                "parameters": {"column_id": text["column_id"], "new_name": "label"},
            },
            {
                "operation": "cast_column",
                "parameters": {
                    "column_id": num["column_id"],
                    "target_type": "string",
                    "failure_policy": "strict",
                },
            },
            {
                "operation": "drop_columns",
                "parameters": {"column_ids": [flag["column_id"]]},
            },
        ],
    )
    preview = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    users = {column.display_name: column for column in preview.candidate_columns if not column.is_system}

    assert users["label"].column_id == original_ids["text"]
    assert users["num"].column_id == original_ids["num"]
    assert users["num"].semantic_hint == "categorical"
    assert "flag" not in users
