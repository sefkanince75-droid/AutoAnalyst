"""Immutable, workspace-relative artifact staging and publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..domain.codec import require_uuid, utc_now
from ..domain.errors import DataError, SchemaError
from ..domain.results import Artifact


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    artifact_id: str
    project_id: str
    owner_run_id: str
    kind: str
    media_type: str
    format_version: str
    staging_path: Path
    created_at: datetime


class ArtifactStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.staging_root = (self.workspace_root / "staging").resolve()
        self.projects_root = (self.workspace_root / "projects").resolve()
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def stage_bytes(
        self,
        content: bytes,
        *,
        project_id: str,
        owner_run_id: str,
        kind: str,
        media_type: str,
        format_version: str = "1",
        artifact_id: str | None = None,
    ) -> StagedArtifact:
        staged = self.create_staging(
            project_id=project_id,
            owner_run_id=owner_run_id,
            kind=kind,
            media_type=media_type,
            format_version=format_version,
            artifact_id=artifact_id,
        )
        try:
            with staged.staging_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            staged.staging_path.unlink(missing_ok=True)
            raise
        return staged

    def create_staging(
        self,
        *,
        project_id: str,
        owner_run_id: str,
        kind: str,
        media_type: str,
        format_version: str = "1",
        artifact_id: str | None = None,
    ) -> StagedArtifact:
        normalized_artifact_id = require_uuid(artifact_id or str(uuid4()), "artifact_id")
        normalized_project_id = require_uuid(project_id, "project_id")
        normalized_owner_id = require_uuid(owner_run_id, "owner_run_id")
        if not kind or not media_type or not format_version:
            raise ValueError("Artifact staging metadata cannot be empty")
        staging_path = self.staging_root / f"{normalized_artifact_id}.{uuid4().hex}.stage"
        self._ensure_within(staging_path, self.staging_root)
        return StagedArtifact(
            normalized_artifact_id,
            normalized_project_id,
            normalized_owner_id,
            kind,
            media_type,
            format_version,
            staging_path,
            utc_now(),
        )

    def finalize(self, staged: StagedArtifact) -> Artifact:
        staging_path = staged.staging_path.resolve(strict=True)
        self._ensure_within(staging_path, self.staging_root)
        if staging_path.is_symlink() or not staging_path.is_file():
            raise DataError({"reason": "invalid_staged_artifact"})
        byte_size, digest = _measure(staging_path)
        suffix = ".parquet" if staged.media_type == "application/vnd.apache.parquet" else ".bin"
        relative_path = PurePosixPath(
            "projects",
            staged.project_id,
            "objects",
            staged.artifact_id[:2],
            f"{staged.artifact_id}{suffix}",
        ).as_posix()
        artifact = Artifact(
            artifact_id=staged.artifact_id,
            project_id=staged.project_id,
            owner_run_id=staged.owner_run_id,
            kind=staged.kind,
            relative_path=relative_path,
            media_type=staged.media_type,
            byte_size=byte_size,
            sha256=digest,
            format_version=staged.format_version,
            created_at=staged.created_at,
        )
        final_path = self.resolve_relative_path(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_within(final_path.parent, self.projects_root)
        if final_path.exists():
            raise SchemaError({"reason": "artifact_overwrite", "artifact_id": staged.artifact_id})
        os.replace(staging_path, final_path)
        return artifact

    def verify(self, artifact: Artifact) -> bool:
        try:
            path = self.resolve_relative_path(artifact.relative_path)
            if not path.is_file() or path.is_symlink():
                return False
            byte_size, digest = _measure(path)
        except (OSError, ValueError, DataError):
            return False
        return byte_size == artifact.byte_size and digest == artifact.sha256

    def resolve_relative_path(self, relative_path: str) -> Path:
        if "\\" in relative_path:
            raise DataError({"reason": "invalid_artifact_path"})
        logical = PurePosixPath(relative_path)
        if logical.is_absolute() or not logical.parts or ".." in logical.parts:
            raise DataError({"reason": "artifact_path_escape"})
        candidate = (self.workspace_root / Path(*logical.parts)).resolve(strict=False)
        self._ensure_within(candidate, self.workspace_root)
        return candidate

    @staticmethod
    def _ensure_within(path: Path, boundary: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(boundary.resolve(strict=False))
        except ValueError as exc:
            raise DataError({"reason": "workspace_boundary_violation"}) from exc


def _measure(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            byte_size += len(block)
            digest.update(block)
    return byte_size, digest.hexdigest()
