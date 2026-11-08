from __future__ import annotations

from datetime import timedelta

import pytest

from autoanalyst.data import CSVIngestor
from autoanalyst.data.schema import DatasetColumn
from autoanalyst.domain.codec import encode_typed_label
from autoanalyst.domain.errors import DataError, SchemaError


def _preview(ws, steps, **kwargs):
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.imported.version.version_id,
        steps=steps,
    )
    return ws.preparation.preview_recipe(recipe.recipe_id, **kwargs)


def _branch(ws):
    preview = _preview(
        ws,
        [
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": ws.column("num")["column_id"],
                    "operator": "gte",
                    "value": 2,
                },
            }
        ],
    )
    branch = ws.preparation.apply_preview(preview.preview_id)
    ws.datasets.move_head(ws.dataset.dataset_id, ws.imported.version.version_id)
    return branch


def test_before_after_samples_are_bounded_and_show_final_schema(phase3_workspace):
    ws = phase3_workspace
    preview = _preview(
        ws,
        [
            {
                "operation": "rename_column",
                "parameters": {
                    "column_id": ws.column("text")["column_id"],
                    "new_name": "description",
                },
            },
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": ws.column("num")["column_id"],
                    "operator": "gte",
                    "value": 2,
                },
            },
        ],
    )
    before, after = ws.preparation.preview_samples(preview.preview_id, limit=2)
    assert before.num_rows == after.num_rows == 2
    assert "text" in before.column_names and "description" in after.column_names
    assert all(not name.startswith("__aa_") for name in after.column_names)
    assert before.column("num").to_pylist() == [1, 2]
    assert after.column("num").to_pylist() == [2, 4]
    assert ws.datasets.get(ws.dataset.dataset_id).head_revision == 1


def test_empty_after_sample_keeps_schema_and_no_versions_are_created(phase3_workspace):
    ws = phase3_workspace
    preview = _preview(
        ws,
        [
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": ws.column("group")["column_id"],
                    "operator": "eq",
                    "value": "absent",
                },
            }
        ],
    )
    before, after = ws.preparation.preview_samples(preview.preview_id)
    assert before.num_rows == 5 and after.num_rows == 0
    assert after.column_names == before.column_names
    assert len(ws.datasets.version_history(ws.dataset.dataset_id)) == 1


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_preview_sample_invalid_limits_are_rejected(phase3_workspace, limit):
    with pytest.raises(SchemaError):
        phase3_workspace.preparation.preview_samples("not-needed", limit=limit)


def test_sample_integrity_and_expiration_are_checked(phase3_workspace):
    ws = phase3_workspace
    preview = _preview(
        ws,
        [
            {
                "operation": "rename_column",
                "parameters": {
                    "column_id": ws.column("text")["column_id"],
                    "new_name": "description",
                },
            }
        ],
    )
    artifact = ws.catalog.get_artifact(preview.candidate_artifact_id)
    ws.store.resolve_relative_path(artifact.relative_path).write_bytes(b"corrupt test candidate")
    with pytest.raises(DataError):
        ws.preparation.preview_samples(preview.preview_id)
    # Even an expired candidate must never be presented as a current preview.
    other = _preview(
        ws,
        [
            {
                "operation": "drop_missing_rows",
                "parameters": {"column_ids": [ws.column("num")["column_id"]]},
            }
        ],
        ttl=timedelta(hours=1),
    )
    from autoanalyst.domain.codec import utc_now

    with ws.catalog.transaction() as connection:
        connection.execute(
            "UPDATE preparation_previews SET expires_at = ? WHERE preview_id = ?",
            ((utc_now() - timedelta(seconds=1)).isoformat(), other.preview_id),
        )
    with pytest.raises(SchemaError) as expired:
        ws.preparation.preview_samples(other.preview_id)
    assert expired.value.context["reason"] == "preparation_preview_expired"


def test_append_candidates_require_original_identity_not_just_equal_headers(phase3_workspace):
    ws = phase3_workspace
    branch = _branch(ws)
    other = ws.datasets.create(ws.project.project_id, "Same headers")
    unrelated = (
        CSVIngestor(ws.catalog, ws.store)
        .import_csv(
            project_id=ws.project.project_id,
            dataset_id=other.dataset_id,
            source=b"num,text,flag,group\n1,x,true,a\n",
        )
        .version
    )
    columns = tuple(
        DatasetColumn(**{key: c[key] for key in DatasetColumn.__dataclass_fields__})
        for c in ws.catalog.list_columns(ws.imported.version.version_id)
    )
    choices = ws.preparation.append_candidates(ws.imported.version.version_id, columns)
    assert branch in choices and unrelated not in choices and ws.imported.version not in choices


def test_repeated_append_input_across_steps_is_rejected_without_changing_head(phase3_workspace):
    ws = phase3_workspace
    branch = _branch(ws)
    head = ws.datasets.get(ws.dataset.dataset_id)
    with pytest.raises(SchemaError) as duplicate:
        _preview(
            ws,
            [{"operation": "append_rows", "parameters": {"input_version_ids": [branch.version_id]}}]
            * 2,
        )
    assert duplicate.value.context["reason"] == "duplicate_append_input"
    assert duplicate.value.context["step_position"] == 1
    assert ws.datasets.get(ws.dataset.dataset_id) == head


def test_replacement_pairs_are_simultaneous_and_support_null(phase3_workspace):
    ws = phase3_workspace
    preview = _preview(
        ws,
        [
            {
                "operation": "replace_values",
                "parameters": {
                    "column_id": ws.column("text")["column_id"],
                    "mapping": [
                        {"from": encode_typed_label("1"), "to": encode_typed_label("x")},
                        {"from": encode_typed_label("x"), "to": encode_typed_label("done")},
                        {"from": encode_typed_label(None), "to": encode_typed_label("missing")},
                    ],
                },
            }
        ],
    )
    before, after = ws.preparation.preview_samples(preview.preview_id)
    assert after.column("text").to_pylist() == ["x", "missing", "done", "done", "z"]
    assert before.column("text").to_pylist() == ["1", None, "x", "x", "z"]


def test_step_failure_reports_its_position_without_partial_publication(phase3_workspace):
    ws = phase3_workspace
    with pytest.raises(SchemaError) as failed:
        _preview(
            ws,
            [
                {
                    "operation": "rename_column",
                    "parameters": {
                        "column_id": ws.column("text")["column_id"],
                        "new_name": "description",
                    },
                },
                {
                    "operation": "cast_column",
                    "parameters": {
                        "column_id": ws.column("text")["column_id"],
                        "target_type": "numeric",
                        "failure_policy": "strict",
                    },
                },
            ],
        )
    assert failed.value.context["reason"] == "cast_failure"
    assert failed.value.context["step_position"] == 1
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version


def test_append_after_filter_uses_fresh_order_numbers_and_only_samples_new_rows(phase3_workspace):
    from autoanalyst.data import TableAccess

    ws = phase3_workspace
    branch = _branch(ws)
    preview = _preview(
        ws,
        [
            {
                "operation": "filter_rows",
                "parameters": {
                    "column_id": ws.column("num")["column_id"],
                    "operator": "gte",
                    "value": 4,
                },
            },
            {"operation": "append_rows", "parameters": {"input_version_ids": [branch.version_id]}},
        ],
    )
    version = ws.preparation.apply_preview(preview.preview_id)
    columns = ws.catalog.list_columns(version.version_id)
    row_order = next(
        c["column_id"] for c in columns if c["physical_name"] == "__aa_internal_row_order__"
    )
    row_id = next(c["column_id"] for c in columns if c["physical_name"] == "__aa_internal_row_id__")
    table = TableAccess(ws.catalog, ws.store).page(version.version_id, (row_order, row_id))
    assert table.column(0).to_pylist() == [3, 4, 5, 6, 7]
    existing = set(table.column(1).to_pylist()[:2])
    assert not existing.intersection(preview.step_results[1].sample_row_ids)


def test_same_source_appended_in_a_later_recipe_gets_new_row_identities(phase3_workspace):
    from autoanalyst.data import TableAccess

    ws = phase3_workspace
    branch = _branch(ws)
    first = _preview(
        ws, [{"operation": "append_rows", "parameters": {"input_version_ids": [branch.version_id]}}]
    )
    first_version = ws.preparation.apply_preview(first.preview_id)
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=first_version.version_id,
        steps=[
            {"operation": "append_rows", "parameters": {"input_version_ids": [branch.version_id]}}
        ],
    )
    second = ws.preparation.preview_recipe(recipe.recipe_id)
    version = ws.preparation.apply_preview(second.preview_id)
    row_id = next(
        c["column_id"]
        for c in ws.catalog.list_columns(version.version_id)
        if c["physical_name"] == "__aa_internal_row_id__"
    )
    ids = (
        TableAccess(ws.catalog, ws.store).page(version.version_id, (row_id,)).column(0).to_pylist()
    )
    assert len(ids) == len(set(ids)) == 11
