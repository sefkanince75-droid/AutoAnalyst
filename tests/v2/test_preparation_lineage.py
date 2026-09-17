from __future__ import annotations


def test_applied_version_lineage_points_to_parent_and_recipe(phase3_workspace) -> None:
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
    version = phase3_workspace.preparation.apply_preview(preview.preview_id)

    lineage = phase3_workspace.preparation.get_lineage(version.version_id)
    assert len(lineage) == 1
    assert lineage[0]["input_version_id"] == phase3_workspace.imported.version.version_id
    assert lineage[0]["recipe_id"] == recipe.recipe_id
    assert lineage[0]["role"] == "base"


def test_dataset_learned_value_survives_preview_persistence_and_apply(phase3_workspace) -> None:
    num = phase3_workspace.column("num")
    recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "fill_missing",
                "parameters": {"column_ids": [num["column_id"]], "strategy": "median"},
            }
        ],
    )
    preview = phase3_workspace.preparation.preview_recipe(recipe.recipe_id)
    reloaded = phase3_workspace.preparation.get_preview(preview.preview_id)
    resolved = reloaded.step_results[0].resolved_values[num["column_id"]]

    assert resolved["type"] == "float"
    assert resolved["value"] == 3.0
    version = phase3_workspace.preparation.apply_preview(preview.preview_id)
    assert version.recipe_id == recipe.recipe_id
    assert (
        phase3_workspace.preparation.get_preview(preview.preview_id).step_results
        == reloaded.step_results
    )


def test_append_input_order_is_recorded_in_lineage(phase3_workspace) -> None:
    text = phase3_workspace.column("text")
    first_recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "rename_column",
                "parameters": {"column_id": text["column_id"], "new_name": "text"},
            }
        ],
    )
    first_preview = phase3_workspace.preparation.preview_recipe(first_recipe.recipe_id)
    branch_input = phase3_workspace.preparation.apply_preview(first_preview.preview_id)
    phase3_workspace.datasets.move_head(
        phase3_workspace.dataset.dataset_id,
        phase3_workspace.imported.version.version_id,
        reason="branch_from_base",
    )
    append_recipe = phase3_workspace.preparation.create_recipe(
        project_id=phase3_workspace.project.project_id,
        base_version_id=phase3_workspace.imported.version.version_id,
        steps=[
            {
                "operation": "append_rows",
                "parameters": {"input_version_ids": [branch_input.version_id]},
            }
        ],
    )
    append_preview = phase3_workspace.preparation.preview_recipe(append_recipe.recipe_id)
    assert append_preview.before_row_count == 5
    assert append_preview.after_row_count == 10
    appended = phase3_workspace.preparation.apply_preview(append_preview.preview_id)

    lineage = phase3_workspace.preparation.get_lineage(appended.version_id)
    assert [(row["role"], row["input_version_id"], row["input_order"]) for row in lineage] == [
        ("base", phase3_workspace.imported.version.version_id, 0),
        ("append", branch_input.version_id, 1),
    ]
