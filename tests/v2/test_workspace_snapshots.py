from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from autoanalyst.application.snapshots import WorkspaceSnapshotService
from autoanalyst.domain.errors import SchemaError
from autoanalyst.storage import ArtifactStore, SQLiteCatalog


def test_workspace_snapshot_round_trip_preserves_catalog_and_artifacts(phase3_workspace, tmp_path: Path) -> None:
    source = phase3_workspace
    snapshot_path = tmp_path / "workspace.aasnapshot"
    service = WorkspaceSnapshotService(source.catalog, source.store)

    exported = service.export_snapshot(snapshot_path)
    restored_root = tmp_path / "restored-workspace"
    WorkspaceSnapshotService.import_snapshot(exported, restored_root)

    restored_catalog = SQLiteCatalog(restored_root)
    restored_store = ArtifactStore(restored_root)
    restored_project = restored_catalog.get_project(source.project.project_id)
    restored_dataset = restored_catalog.get_dataset(source.dataset.dataset_id)
    restored_version = restored_catalog.get_version(source.imported.version.version_id)

    assert restored_project.name == source.project.name
    assert restored_dataset.head_version_id == source.imported.version.version_id
    assert restored_version.content_fingerprint == source.imported.version.content_fingerprint
    for artifact in (source.imported.raw_artifact, source.imported.table_artifact):
        restored_artifact = restored_catalog.get_artifact(artifact.artifact_id)
        assert restored_artifact.sha256 == artifact.sha256
        assert restored_store.verify(restored_artifact)

    with zipfile.ZipFile(exported) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == "autoanalyst.workspace_snapshot"
    assert len(manifest["artifacts"]) >= 2


def test_snapshot_import_rejects_path_traversal(tmp_path: Path) -> None:
    snapshot = tmp_path / "evil.aasnapshot"
    with zipfile.ZipFile(snapshot, "w") as archive:
        archive.writestr("../escape.txt", b"nope")
        archive.writestr("catalog.sqlite", b"not-a-db")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "autoanalyst.workspace_snapshot",
                    "snapshot_version": "1",
                    "autoanalyst_version": "2.0.0rc1",
                    "created_at": "2026-09-17T00:00:00Z",
                    "catalog": {"path": "catalog.sqlite", "sha256": "0" * 64, "byte_size": 8},
                    "artifacts": [],
                }
            ),
        )

    with pytest.raises(SchemaError) as unsafe:
        WorkspaceSnapshotService.import_snapshot(snapshot, tmp_path / "restore")
    assert unsafe.value.context["reason"] == "snapshot_path_unsafe"
    assert not (tmp_path / "escape.txt").exists()
