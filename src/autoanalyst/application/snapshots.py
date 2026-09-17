"""Consistent workspace snapshot export/import for V2 backup and transfer."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .. import __version__
from ..domain.codec import utc_now
from ..domain.errors import DataError, ResourceError, SchemaError
from ..execution.lease import clear_stale_lease, owned_process
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog

SNAPSHOT_FORMAT = "autoanalyst.workspace_snapshot"
SNAPSHOT_VERSION = "1"
_MANIFEST_NAME = "manifest.json"
_CATALOG_NAME = "catalog.sqlite"


class WorkspaceSnapshotService:
    """Create and restore immutable snapshots without copying active WAL/staging state."""

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def export_snapshot(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        if target.exists() and target.is_dir():
            raise SchemaError({"reason": "snapshot_destination_is_directory"})
        target.parent.mkdir(parents=True, exist_ok=True)

        lease = clear_stale_lease(self.catalog.paths.root)
        if lease is not None and owned_process(self.catalog.paths.root, lease.run_id) is not None:
            raise ResourceError({"reason": "snapshot_worker_active", "run_id": lease.run_id})

        with tempfile.TemporaryDirectory(prefix="autoanalyst-snapshot-") as temporary:
            temp_root = Path(temporary)
            catalog_copy = temp_root / _CATALOG_NAME
            self._backup_catalog(catalog_copy)
            artifact_rows = _snapshot_artifacts(catalog_copy)
            file_entries: list[dict[str, object]] = []
            for row in artifact_rows:
                relative = _safe_relative(str(row["relative_path"]))
                source = self.artifact_store.resolve_relative_path(str(relative))
                if not source.is_file():
                    raise DataError(
                        {"reason": "snapshot_artifact_missing", "artifact_id": row["artifact_id"]}
                    )
                digest, size = _hash_file(source)
                if digest != row["sha256"] or size != int(row["byte_size"]):
                    raise DataError(
                        {"reason": "snapshot_artifact_corrupt", "artifact_id": row["artifact_id"]}
                    )
                file_entries.append(
                    {
                        "path": relative.as_posix(),
                        "sha256": digest,
                        "byte_size": size,
                        "artifact_id": row["artifact_id"],
                    }
                )

            catalog_sha, catalog_size = _hash_file(catalog_copy)
            manifest = {
                "format": SNAPSHOT_FORMAT,
                "snapshot_version": SNAPSHOT_VERSION,
                "autoanalyst_version": __version__,
                "created_at": utc_now().isoformat().replace("+00:00", "Z"),
                "catalog": {
                    "path": _CATALOG_NAME,
                    "sha256": catalog_sha,
                    "byte_size": catalog_size,
                },
                "artifacts": file_entries,
            }
            staging_target = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                with zipfile.ZipFile(
                    staging_target,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    archive.write(catalog_copy, _CATALOG_NAME)
                    for item in file_entries:
                        relative = _safe_relative(str(item["path"]))
                        archive.write(
                            self.artifact_store.resolve_relative_path(relative.as_posix()),
                            relative.as_posix(),
                        )
                    archive.writestr(
                        _MANIFEST_NAME,
                        json.dumps(
                            manifest,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                    )
                _fsync_file(staging_target)
                if target.exists():
                    raise SchemaError({"reason": "snapshot_destination_exists"})
                os.replace(staging_target, target)
            finally:
                staging_target.unlink(missing_ok=True)
        return target

    @classmethod
    def import_snapshot(cls, source: str | Path, destination_workspace: str | Path) -> Path:
        snapshot = Path(source).expanduser().resolve()
        destination = Path(destination_workspace).expanduser().resolve()
        if not snapshot.is_file():
            raise DataError({"reason": "snapshot_not_found"})
        if destination.exists() and any(destination.iterdir()):
            raise SchemaError({"reason": "snapshot_destination_not_empty"})

        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        restore_root = Path(tempfile.mkdtemp(prefix=".autoanalyst-restore-", dir=parent))
        try:
            manifest = _extract_and_verify(snapshot, restore_root)
            if destination.exists():
                destination.rmdir()
            os.replace(restore_root, destination)
            restore_root = destination
            # Opening the restored catalog validates migration checksums and makes
            # sure the package can actually resume from the snapshot.
            restored = SQLiteCatalog(destination)
            store = ArtifactStore(restored.paths.root)
            for item in manifest["artifacts"]:
                artifact = restored.get_artifact(str(item["artifact_id"]))
                if not store.verify(artifact):
                    raise DataError(
                        {
                            "reason": "snapshot_restored_artifact_invalid",
                            "artifact_id": artifact.artifact_id,
                        }
                    )
            return destination
        except Exception:
            if restore_root != destination:
                shutil.rmtree(restore_root, ignore_errors=True)
            raise

    def _backup_catalog(self, destination: Path) -> None:
        # sqlite3.Connection's context manager commits/rolls back but deliberately
        # does not close the connection. Keep an explicit target handle so Windows
        # releases the file before fsync/temporary-directory cleanup.
        target = sqlite3.connect(destination)
        try:
            with self.catalog.connection() as source:
                source.backup(target)
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                target.commit()
        finally:
            target.close()
        _fsync_file(destination)


def _snapshot_artifacts(catalog_copy: Path) -> tuple[sqlite3.Row, ...]:
    connection = sqlite3.connect(catalog_copy)
    connection.row_factory = sqlite3.Row
    try:
        return tuple(
            connection.execute(
                "SELECT artifact_id, relative_path, byte_size, sha256 FROM artifacts ORDER BY artifact_id"
            )
        )
    finally:
        connection.close()


def _extract_and_verify(snapshot: Path, destination: Path) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(snapshot, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise DataError({"reason": "snapshot_invalid_zip"}) from exc
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SchemaError({"reason": "snapshot_duplicate_entry"})
        safe_names = {_safe_relative(name).as_posix() for name in names}
        if _MANIFEST_NAME not in safe_names or _CATALOG_NAME not in safe_names:
            raise SchemaError({"reason": "snapshot_required_entry_missing"})
        try:
            manifest = json.loads(archive.read(_MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataError({"reason": "snapshot_manifest_invalid"}) from exc
        _validate_manifest(manifest)
        allowed = {_MANIFEST_NAME, _CATALOG_NAME} | {
            _safe_relative(str(item["path"])).as_posix() for item in manifest["artifacts"]
        }
        if safe_names != allowed:
            raise SchemaError({"reason": "snapshot_unlisted_entry"})
        for name in sorted(safe_names):
            if name == _MANIFEST_NAME:
                continue
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            _fsync_file(target)

    catalog_meta = manifest["catalog"]
    catalog_path = destination / _CATALOG_NAME
    catalog_hash, catalog_size = _hash_file(catalog_path)
    if catalog_hash != catalog_meta["sha256"] or catalog_size != int(catalog_meta["byte_size"]):
        raise DataError({"reason": "snapshot_catalog_checksum_mismatch"})
    for item in manifest["artifacts"]:
        path = destination.joinpath(*_safe_relative(str(item["path"])).parts)
        digest, size = _hash_file(path)
        if digest != item["sha256"] or size != int(item["byte_size"]):
            raise DataError(
                {
                    "reason": "snapshot_artifact_checksum_mismatch",
                    "artifact_id": item["artifact_id"],
                }
            )
    return manifest


def _validate_manifest(value: object) -> None:
    if not isinstance(value, dict):
        raise SchemaError({"reason": "snapshot_manifest_not_object"})
    if value.get("format") != SNAPSHOT_FORMAT or value.get("snapshot_version") != SNAPSHOT_VERSION:
        raise SchemaError({"reason": "snapshot_format_unsupported"})
    catalog = value.get("catalog")
    artifacts = value.get("artifacts")
    if not isinstance(catalog, dict) or not isinstance(artifacts, list):
        raise SchemaError({"reason": "snapshot_manifest_shape_invalid"})
    if catalog.get("path") != _CATALOG_NAME:
        raise SchemaError({"reason": "snapshot_catalog_path_invalid"})
    if not _valid_sha(catalog.get("sha256")) or not isinstance(catalog.get("byte_size"), int):
        raise SchemaError({"reason": "snapshot_catalog_metadata_invalid"})
    seen: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid"})
        path = _safe_relative(str(item.get("path", ""))).as_posix()
        if path in seen:
            raise SchemaError({"reason": "snapshot_artifact_path_duplicate", "path": path})
        seen.add(path)
        if (
            not isinstance(item.get("artifact_id"), str)
            or not _valid_sha(item.get("sha256"))
            or not isinstance(item.get("byte_size"), int)
            or int(item["byte_size"]) < 0
        ):
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid", "path": path})


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise SchemaError({"reason": "snapshot_path_unsafe", "path": value})
    return path


def _valid_sha(value: object) -> bool:
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _fsync_file(path: Path) -> None:
    # Windows' CRT rejects fsync on a read-only descriptor; r+b is portable for
    # files AutoAnalyst owns and does not mutate content by itself.
    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())
