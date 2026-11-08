from uuid import uuid4

import pytest

from autoanalyst.application import DatasetService, PreparationService, ProjectService
from autoanalyst.bootstrap import build_runtime
from autoanalyst.data import CSVIngestor, TableAccess
from autoanalyst.domain.errors import ResourceError, SchemaError


def setup_join(
    tmp_path, left=b"id,value\n1,10\n2,20\n3,30\n", right=b"id,value\n1,100\n2,200\n4,400\n"
):
    runtime = build_runtime(tmp_path)
    project = ProjectService(runtime.catalog).create("Join")
    datasets = DatasetService(runtime.catalog)
    a, b = datasets.create(project.project_id, "Left"), datasets.create(project.project_id, "Right")
    ingestor = CSVIngestor(runtime.catalog, runtime.artifact_store)
    av = ingestor.import_csv(project_id=project.project_id, dataset_id=a.dataset_id, source=left)
    bv = ingestor.import_csv(project_id=project.project_id, dataset_id=b.dataset_id, source=right)
    params = {
        "right_version_id": bv.version.version_id,
        "left_key_ids": [next(c.column_id for c in av.columns if c.display_name == "id")],
        "right_key_ids": [next(c.column_id for c in bv.columns if c.display_name == "id")],
        "how": "left",
        "relationship": "one_to_one",
        "max_output_rows": 100,
        "max_expansion": 2.0,
    }
    return runtime, project, av, bv, params


def test_join_preview_apply_preserves_column_identity_lineage_and_order(tmp_path):
    runtime, project, left, right, params = setup_join(tmp_path)
    service = PreparationService(runtime.catalog, runtime.artifact_store)
    recipe = service.create_recipe(
        project_id=project.project_id,
        base_version_id=left.version.version_id,
        steps=[{"operation": "join_rows", "parameters": params}],
    )
    preview = service.preview_recipe(recipe.recipe_id)
    assert preview.after_row_count == 3 and preview.after_column_count == 3
    assert preview.step_results[0].resolved_values["unmatched_left"] == 1
    assert preview.step_results[0].resolved_values["unmatched_right"] == 1
    version = service.apply_preview(preview.preview_id)
    cols = [c for c in runtime.catalog.list_columns(version.version_id) if not c["is_system"]]
    assert [c["display_name"] for c in cols] == ["id", "value", "value_right"]
    assert cols[-1]["column_id"] == next(
        c.column_id for c in right.columns if c.display_name == "value"
    )
    table = TableAccess(runtime.catalog, runtime.artifact_store).selected_columns(
        version.version_id, tuple(c["column_id"] for c in cols)
    )
    assert table.column(0).to_pylist() == [1, 2, 3]
    assert table.column(2).to_pylist() == [100, 200, None]
    assert [e["role"] for e in runtime.catalog.get_version_lineage(version.version_id)] == [
        "base",
        "join",
    ]
    from autoanalyst.application.snapshots import WorkspaceSnapshotService

    backup = tmp_path.parent / f"{uuid4()}.aasnapshot"
    try:
        WorkspaceSnapshotService(runtime.catalog, runtime.artifact_store).export_snapshot(backup)
        assert WorkspaceSnapshotService.inspect_snapshot(backup).versions == 3
    finally:
        backup.unlink(missing_ok=True)


@pytest.mark.parametrize("failure", ["cardinality", "expansion", "type", "project"])
def test_join_rejects_unsafe_match_before_publication(tmp_path, failure):
    right = (
        b"id,value\n1,100\n1,101\n1,102\n1,103\n"
        if failure in {"cardinality", "expansion"}
        else b"id,value\nx,1\ny,2\n"
        if failure == "type"
        else b"id,value\n1,1\n"
    )
    runtime, project, left, _, params = setup_join(tmp_path, right=right)
    if failure == "expansion":
        params.update(relationship="one_to_many", max_output_rows=1)
    if failure == "project":
        other = ProjectService(runtime.catalog).create("Other")
        dataset = DatasetService(runtime.catalog).create(other.project_id, "Other")
        incoming = CSVIngestor(runtime.catalog, runtime.artifact_store).import_csv(
            project_id=other.project_id, dataset_id=dataset.dataset_id, source=b"id,value\n1,1\n"
        )
        params["right_version_id"] = incoming.version.version_id
        params["right_key_ids"] = [
            next(c.column_id for c in incoming.columns if c.display_name == "id")
        ]
    service = PreparationService(runtime.catalog, runtime.artifact_store)
    recipe = service.create_recipe(
        project_id=project.project_id,
        base_version_id=left.version.version_id,
        steps=[{"operation": "join_rows", "parameters": params}],
    )
    with pytest.raises((ResourceError, SchemaError)):
        service.preview_recipe(recipe.recipe_id)
    assert DatasetService(runtime.catalog).current_version(left.version.dataset_id) == left.version


def test_many_to_many_unique_output_rows_and_null_keys_do_not_match(tmp_path):
    runtime, project, left, _, params = setup_join(
        tmp_path, left=b"id,value\n1,10\n1,11\n,12\n", right=b"id,value\n1,100\n1,101\n,102\n"
    )
    params.update(relationship="many_to_many", max_expansion=2.0)
    service = PreparationService(runtime.catalog, runtime.artifact_store)
    recipe = service.create_recipe(
        project_id=project.project_id,
        base_version_id=left.version.version_id,
        steps=[{"operation": "join_rows", "parameters": params}],
    )
    preview = service.preview_recipe(recipe.recipe_id)
    assert (
        preview.after_row_count == 5
        and preview.step_results[0].resolved_values["matched_pairs"] == 4
    )
    version = service.apply_preview(preview.preview_id)
    system = next(
        c
        for c in runtime.catalog.list_columns(version.version_id)
        if c["physical_name"] == "__aa_internal_row_id__"
    )
    ids = (
        TableAccess(runtime.catalog, runtime.artifact_store)
        .selected_columns(version.version_id, (system["column_id"],))
        .column(0)
        .to_pylist()
    )
    assert len(set(ids)) == 5
