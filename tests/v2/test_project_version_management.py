from __future__ import annotations

import pytest

from autoanalyst.domain.errors import DataError, SchemaError
from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage.runs import RunStore
from autoanalyst.ui.app import _spec


def _second(ws):
    from autoanalyst.data import CSVIngestor

    return (
        CSVIngestor(ws.catalog, ws.store)
        .import_csv(
            project_id=ws.project.project_id,
            dataset_id=ws.dataset.dataset_id,
            source=b"num,text,flag,group\n10,x,true,a\n20,y,false,b\n",
        )
        .version
    )


def _restore(ws, target, head):
    return ws.datasets.restore_version(
        ws.dataset.dataset_id,
        target.version_id,
        expected_project_id=ws.project.project_id,
        expected_head_version_id=head.head_version_id,
        expected_head_revision=head.head_revision,
    )


def test_project_archive_restore_preserves_versions_artifacts_and_metadata(phase3_workspace):
    ws = phase3_workspace
    first = ws.project
    artifacts = tuple(ws.catalog.list_columns(ws.imported.version.version_id))
    archived = ws.projects.set_archived(first.project_id, True, expected_revision=first.revision)
    assert archived.archived_at is not None and archived.revision == first.revision + 1
    assert ws.projects.list() == ()
    assert ws.projects.list(include_archived=True) == (archived,)
    assert ws.datasets.current_version(ws.dataset.dataset_id) == ws.imported.version
    assert tuple(ws.catalog.list_columns(ws.imported.version.version_id)) == artifacts
    restored = ws.projects.set_archived(
        first.project_id, False, expected_revision=archived.revision
    )
    assert restored.revision == 2 and restored.archived_at is None
    assert (
        restored.created_at == first.created_at
        and restored.default_language == first.default_language
    )
    assert ws.projects.list() == (restored,)
    # Repeating the same archive state does not manufacture another revision.
    assert ws.projects.set_archived(first.project_id, False, expected_revision=2) == restored


@pytest.mark.parametrize("action", ["rename", "archive", "unarchive"])
def test_stale_project_metadata_action_cannot_overwrite_new_revision(phase3_workspace, action):
    ws = phase3_workspace
    updated = ws.projects.rename(ws.project.project_id, "Updated elsewhere", expected_revision=0)
    with pytest.raises(SchemaError) as conflict:
        if action == "rename":
            ws.projects.rename(ws.project.project_id, "Stale name", expected_revision=0)
        else:
            ws.projects.set_archived(
                ws.project.project_id, action == "archive", expected_revision=0
            )
    assert conflict.value.context["reason"] == "project_revision_conflict"
    assert ws.projects.get(ws.project.project_id) == updated


def test_active_project_cannot_be_archived_but_other_project_can(phase3_workspace):
    ws = phase3_workspace
    coordinator = ExecutionCoordinator(RunStore(ws.catalog))
    coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.imported.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="archive-active",
    )
    with pytest.raises(SchemaError) as blocked:
        ws.projects.set_archived(ws.project.project_id, True, expected_revision=0)
    assert blocked.value.context["reason"] == "project_has_active_runs"
    other = ws.projects.create("Other project")
    assert ws.projects.set_archived(other.project_id, True, expected_revision=0).archived_at
    assert ws.projects.get(ws.project.project_id).revision == 0


def test_version_restore_and_redo_preserve_data_and_advance_only_head_history(phase3_workspace):
    ws = phase3_workspace
    second = _second(ws)
    history = ws.datasets.version_history(ws.dataset.dataset_id)
    first_artifact = ws.catalog.get_artifact(ws.imported.version.table_artifact_id)
    second_artifact = ws.catalog.get_artifact(second.table_artifact_id)
    before = {
        a.artifact_id: ws.store.resolve_relative_path(a.relative_path).read_bytes()
        for a in (first_artifact, second_artifact)
    }
    moved = _restore(ws, ws.imported.version, ws.datasets.get(ws.dataset.dataset_id))
    assert moved.head_version_id == ws.imported.version.version_id and moved.head_revision == 3
    redone = _restore(ws, second, moved)
    assert redone.head_version_id == second.version_id and redone.head_revision == 4
    assert ws.datasets.version_history(ws.dataset.dataset_id) == history
    for artifact in (first_artifact, second_artifact):
        assert (
            ws.store.resolve_relative_path(artifact.relative_path).read_bytes()
            == before[artifact.artifact_id]
        )
    events = ws.catalog.list_head_events(ws.dataset.dataset_id)
    assert [event["reason"] for event in events[-2:]] == ["restore_version", "restore_version"]
    assert [(event["from_revision"], event["to_revision"]) for event in events] == [
        (0, 1),
        (1, 2),
        (2, 3),
        (3, 4),
    ]


@pytest.mark.parametrize("mismatch", ["project", "dataset", "head", "revision", "current"])
def test_invalid_restore_context_cannot_move_head(phase3_workspace, mismatch):
    ws = phase3_workspace
    second = _second(ws)
    head = ws.datasets.get(ws.dataset.dataset_id)
    params = dict(
        expected_project_id=ws.project.project_id,
        expected_head_version_id=head.head_version_id,
        expected_head_revision=head.head_revision,
    )
    target = ws.imported.version.version_id
    if mismatch == "project":
        params["expected_project_id"] = ws.projects.create("Other").project_id
    elif mismatch == "dataset":
        other = ws.datasets.create(ws.project.project_id, "Other data")
        from autoanalyst.data import CSVIngestor

        target = (
            CSVIngestor(ws.catalog, ws.store)
            .import_csv(
                project_id=ws.project.project_id, dataset_id=other.dataset_id, source=b"a\n1\n"
            )
            .version.version_id
        )
    elif mismatch == "head":
        params["expected_head_version_id"] = target
    elif mismatch == "revision":
        params["expected_head_revision"] -= 1
    else:
        target = second.version_id
    before = ws.catalog.list_head_events(ws.dataset.dataset_id)
    with pytest.raises(SchemaError):
        ws.datasets.restore_version(ws.dataset.dataset_id, target, **params)
    assert ws.datasets.get(ws.dataset.dataset_id) == head
    assert ws.catalog.list_head_events(ws.dataset.dataset_id) == before


def test_restore_rechecks_head_inside_transaction_even_after_aba_change(
    phase3_workspace, monkeypatch
):
    ws = phase3_workspace
    second = _second(ws)
    head = ws.datasets.get(ws.dataset.dataset_id)
    original = ws.catalog.move_head

    def concurrent_head_move(dataset_id, version_id, **kwargs):
        original(dataset_id, ws.imported.version.version_id, reason="other-session")
        original(dataset_id, second.version_id, reason="other-session")
        return original(dataset_id, version_id, **kwargs)

    monkeypatch.setattr(ws.catalog, "move_head", concurrent_head_move)
    with pytest.raises(SchemaError) as stale:
        _restore(ws, ws.imported.version, head)
    assert stale.value.context["reason"] == "restore_head_changed"
    assert ws.datasets.get(ws.dataset.dataset_id).head_version_id == second.version_id
    assert ws.datasets.get(ws.dataset.dataset_id).head_revision == 4


def test_missing_or_corrupt_saved_artifact_does_not_change_head(phase3_workspace):
    ws = phase3_workspace
    _second(ws)
    head = ws.datasets.get(ws.dataset.dataset_id)
    artifact = ws.catalog.get_artifact(ws.imported.version.table_artifact_id)
    ws.store.resolve_relative_path(artifact.relative_path).write_bytes(b"corrupt fixture")
    with pytest.raises(DataError) as invalid:
        _restore(ws, ws.imported.version, head)
    assert invalid.value.context["reason"] == "restore_artifact_invalid"
    assert ws.datasets.get(ws.dataset.dataset_id) == head


def test_restoring_prepared_version_preserves_lineage_and_rejects_old_preview(phase3_workspace):
    ws = phase3_workspace
    recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=ws.imported.version.version_id,
        steps=(
            {
                "operation": "drop_missing_rows",
                "parameters": {"column_ids": [ws.column("text")["column_id"]]},
            },
        ),
    )
    preview = ws.preparation.preview_recipe(recipe.recipe_id)
    prepared = ws.preparation.apply_preview(preview.preview_id)
    lineage = ws.catalog.get_version_lineage(prepared.version_id)
    pending_recipe = ws.preparation.create_recipe(
        project_id=ws.project.project_id,
        base_version_id=prepared.version_id,
        steps=(
            {
                "operation": "rename_column",
                "parameters": {"column_id": ws.column("text")["column_id"], "new_name": "renamed"},
            },
        ),
    )
    pending = ws.preparation.preview_recipe(pending_recipe.recipe_id)
    base = _restore(ws, ws.imported.version, ws.datasets.get(ws.dataset.dataset_id))
    with pytest.raises(SchemaError):
        ws.preparation.apply_preview(pending.preview_id)
    _restore(ws, prepared, base)
    with pytest.raises(SchemaError):
        ws.preparation.apply_preview(pending.preview_id)
    assert ws.catalog.get_version_lineage(prepared.version_id) == lineage
    assert ws.datasets.current_version(ws.dataset.dataset_id) == prepared
