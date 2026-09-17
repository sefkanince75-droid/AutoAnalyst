from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from autoanalyst.domain.errors import DataError, SchemaError
from autoanalyst.storage.artifacts import ArtifactStore


def _ids() -> tuple[str, str, str]:
    return str(uuid4()), str(uuid4()), str(uuid4())


def test_artifact_checksum_verify_and_tamper_detection(tmp_path: Path) -> None:
    project_id, owner_id, artifact_id = _ids()
    store = ArtifactStore(tmp_path)
    artifact = store.finalize(
        store.stage_bytes(
            b"private,data\n1,2\n",
            project_id=project_id,
            owner_run_id=owner_id,
            kind="raw_source",
            media_type="text/csv",
            artifact_id=artifact_id,
        )
    )

    assert store.verify(artifact) is True
    assert not Path(artifact.relative_path).is_absolute()
    store.resolve_relative_path(artifact.relative_path).write_bytes(b"tampered")
    assert store.verify(artifact) is False


def test_artifact_overwrite_is_rejected(tmp_path: Path) -> None:
    project_id, owner_id, artifact_id = _ids()
    store = ArtifactStore(tmp_path)
    first = store.stage_bytes(
        b"first",
        project_id=project_id,
        owner_run_id=owner_id,
        kind="raw_source",
        media_type="text/csv",
        artifact_id=artifact_id,
    )
    store.finalize(first)
    duplicate = store.stage_bytes(
        b"second",
        project_id=project_id,
        owner_run_id=owner_id,
        kind="raw_source",
        media_type="text/csv",
        artifact_id=artifact_id,
    )

    with pytest.raises(SchemaError) as rejected:
        store.finalize(duplicate)
    assert rejected.value.context["reason"] == "artifact_overwrite"


@pytest.mark.parametrize("path", ["../../outside", "/absolute/path", "projects\\..\\outside"])
def test_workspace_escape_paths_are_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(DataError):
        ArtifactStore(tmp_path).resolve_relative_path(path)
