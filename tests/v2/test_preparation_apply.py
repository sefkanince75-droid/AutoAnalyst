from __future__ import annotations

import pytest

import autoanalyst.storage.sqlite as sqlite_storage
from autoanalyst.data import CSVIngestor
from autoanalyst.domain.errors import DataError, SchemaError
from autoanalyst.preparation import PreviewStatus


def _ready_preview(workspace):
    text = workspace.column("text")
    recipe = workspace.preparation.create_recipe(
        project_id=workspace.project.project_id,
        base_version_id=workspace.imported.version.version_id,
        steps=[
            {
                "operation": "drop_missing_rows",
                "parameters": {"column_ids": [text["column_id"]]},
            }
        ],
    )
    return workspace.preparation.preview_recipe(recipe.recipe_id)


def test_apply_uses_candidate_without_recomputing_and_publishes_new_version(
    phase3_workspace, monkeypatch
) -> None:
    preview = _ready_preview(phase3_workspace)

    def forbidden_recompute(*args, **kwargs):
        raise AssertionError("apply must not execute preview again")

    monkeypatch.setattr(phase3_workspace.preparation.engine, "preview", forbidden_recompute)
    version = phase3_workspace.preparation.apply_preview(preview.preview_id)
    dataset = phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id)

    assert version.version_id != preview.base_version_id
    assert version.kind.value == "prepared"
    assert version.recipe_id == preview.recipe_id
    assert version.table_artifact_id == preview.candidate_artifact_id
    assert dataset.head_version_id == version.version_id
    assert dataset.head_revision == 2
    assert (
        phase3_workspace.catalog.list_head_events(dataset.dataset_id)[-1]["reason"]
        == "preparation_apply"
    )
    assert (
        phase3_workspace.preparation.get_preview(preview.preview_id).status is PreviewStatus.APPLIED
    )


def test_apply_is_idempotent(phase3_workspace) -> None:
    preview = _ready_preview(phase3_workspace)
    first = phase3_workspace.preparation.apply_preview(preview.preview_id)
    second = phase3_workspace.preparation.apply_preview(preview.preview_id)

    assert second == first
    assert len(phase3_workspace.datasets.version_history(phase3_workspace.dataset.dataset_id)) == 2
    assert phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id).head_revision == 2


def test_head_change_makes_preview_stale(phase3_workspace) -> None:
    preview = _ready_preview(phase3_workspace)
    CSVIngestor(phase3_workspace.catalog, phase3_workspace.store).import_csv(
        project_id=phase3_workspace.project.project_id,
        dataset_id=phase3_workspace.dataset.dataset_id,
        source=b"value\nnew\n",
        original_name="new-head.csv",
    )

    with pytest.raises(SchemaError) as stale:
        phase3_workspace.preparation.apply_preview(preview.preview_id)
    assert stale.value.context["reason"] == "preparation_preview_stale"


def test_returning_to_base_version_does_not_revive_stale_preview(phase3_workspace) -> None:
    preview = _ready_preview(phase3_workspace)
    newer = CSVIngestor(phase3_workspace.catalog, phase3_workspace.store).import_csv(
        project_id=phase3_workspace.project.project_id,
        dataset_id=phase3_workspace.dataset.dataset_id,
        source=b"value\nnew\n",
        original_name="new-head.csv",
    )
    assert newer.version.version_id != preview.base_version_id
    phase3_workspace.datasets.move_head(
        phase3_workspace.dataset.dataset_id,
        preview.base_version_id,
        reason="undo_for_stale_test",
    )

    with pytest.raises(SchemaError) as stale:
        phase3_workspace.preparation.apply_preview(preview.preview_id)
    assert stale.value.context["reason"] == "preparation_preview_stale"
    assert stale.value.context["actual_head_revision"] == 3


def test_expired_preview_is_rejected(phase3_workspace) -> None:
    preview = _ready_preview(phase3_workspace)
    with phase3_workspace.catalog.transaction() as connection:
        connection.execute(
            "UPDATE preparation_previews SET expires_at = ? WHERE preview_id = ?",
            ("2000-01-01T00:00:00Z", preview.preview_id),
        )

    with pytest.raises(SchemaError) as expired:
        phase3_workspace.preparation.apply_preview(preview.preview_id)
    assert expired.value.context["reason"] == "preparation_preview_expired"


def test_tampered_candidate_is_rejected(phase3_workspace) -> None:
    preview = _ready_preview(phase3_workspace)
    artifact = phase3_workspace.catalog.get_artifact(preview.candidate_artifact_id)
    phase3_workspace.store.resolve_relative_path(artifact.relative_path).write_bytes(b"tampered")

    with pytest.raises(DataError) as invalid:
        phase3_workspace.preparation.apply_preview(preview.preview_id)
    assert invalid.value.context["reason"] == "artifact_verification_failed"
    assert phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id).head_revision == 1


def test_failed_apply_transaction_rolls_back_version_head_and_status(
    phase3_workspace, monkeypatch
) -> None:
    preview = _ready_preview(phase3_workspace)
    before_versions = phase3_workspace.datasets.version_history(phase3_workspace.dataset.dataset_id)

    def fail_before_head(*args, **kwargs):
        raise RuntimeError("forced head failure")

    monkeypatch.setattr(sqlite_storage, "_move_head", fail_before_head)
    with pytest.raises(RuntimeError, match="forced head failure"):
        phase3_workspace.preparation.apply_preview(preview.preview_id)

    assert (
        phase3_workspace.datasets.version_history(phase3_workspace.dataset.dataset_id)
        == before_versions
    )
    assert phase3_workspace.datasets.get(phase3_workspace.dataset.dataset_id).head_revision == 1
    assert (
        phase3_workspace.preparation.get_preview(preview.preview_id).status is PreviewStatus.READY
    )
    with phase3_workspace.catalog.connection() as connection:
        assert connection.execute("SELECT count(*) FROM dataset_version_lineage").fetchone()[0] == 0
