"""Consistent workspace snapshot export/import for V2 backup and transfer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from .. import __version__
from ..domain.codec import from_json, require_uuid, utc_now
from ..domain.errors import DataError, ResourceError, SchemaError
from ..domain.plans import AnalysisSpec
from ..domain.results import AnalysisResult
from ..domain.runs import AnalysisRun
from ..execution.lease import clear_stale_lease, owned_process
from ..storage.artifacts import ArtifactStore
from ..storage.schema_compat import migration_layout
from ..storage.sqlite import SQLiteCatalog, _sql_statements

SNAPSHOT_FORMAT = "autoanalyst.workspace_snapshot"
SNAPSHOT_VERSION = "1"
_MANIFEST_NAME = "manifest.json"
_CATALOG_NAME = "catalog.sqlite"
MAX_SNAPSHOT_BYTES = 2 * 1024**3
MAX_UNPACKED_BYTES = 8 * 1024**3
MAX_CATALOG_BYTES = 256 * 1024**2
MAX_MANIFEST_BYTES = 8 * 1024**2
MAX_ENTRIES = 100_000


@dataclass(frozen=True)
class SnapshotInspection:
    source_version: str
    created_at: str
    projects: int
    datasets: int
    versions: int
    results: int
    artifacts: int
    models: int
    unpacked_bytes: int


class WorkspaceSnapshotService:
    """Create and restore immutable snapshots without copying active WAL/staging state."""

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def export_snapshot(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        if target.exists():
            raise SchemaError({"reason": "snapshot_destination_exists"})
        if target.exists() and target.is_dir():
            raise SchemaError({"reason": "snapshot_destination_is_directory"})
        target.parent.mkdir(parents=True, exist_ok=True)

        from ..execution.data_tasks import active_data_task

        if active_data_task(self.catalog.paths.root):
            raise ResourceError({"reason": "snapshot_worker_active"})
        lease = clear_stale_lease(self.catalog.paths.root)
        if lease is not None and owned_process(self.catalog.paths.root, lease.run_id) is not None:
            raise ResourceError({"reason": "snapshot_worker_active", "run_id": lease.run_id})

        with tempfile.TemporaryDirectory(prefix="autoanalyst-snapshot-") as temporary:
            temp_root = Path(temporary)
            catalog_copy = temp_root / _CATALOG_NAME
            self._backup_catalog(catalog_copy)
            _require_idle_catalog(catalog_copy)
            artifact_rows = _snapshot_artifacts(catalog_copy)
            if (
                len(artifact_rows) + 2 > MAX_ENTRIES
                or catalog_copy.stat().st_size > MAX_CATALOG_BYTES
                or catalog_copy.stat().st_size + sum(int(row["byte_size"]) for row in artifact_rows)
                > MAX_UNPACKED_BYTES
            ):
                raise ResourceError({"reason": "snapshot_size_limit"})
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
            manifest_bytes = json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if (
                len(manifest_bytes) > MAX_MANIFEST_BYTES
                or catalog_size
                + sum(int(str(item["byte_size"])) for item in file_entries)
                + len(manifest_bytes)
                > MAX_UNPACKED_BYTES
            ):
                raise ResourceError({"reason": "snapshot_size_limit"})
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
                        manifest_bytes,
                    )
                if staging_target.stat().st_size > MAX_SNAPSHOT_BYTES:
                    raise ResourceError({"reason": "snapshot_size_limit"})
                _fsync_file(staging_target)
                if target.exists():
                    raise SchemaError({"reason": "snapshot_destination_exists"})
                try:
                    if os.name == "nt":
                        os.rename(staging_target, target)
                    else:
                        # POSIX rename replaces files; link provides exclusive publication.
                        os.link(staging_target, target)
                except FileExistsError as exc:
                    raise SchemaError({"reason": "snapshot_destination_exists"}) from exc
            finally:
                staging_target.unlink(missing_ok=True)
        return target

    @classmethod
    def inspect_snapshot(cls, source: str | Path) -> SnapshotInspection:
        snapshot = Path(source).expanduser().resolve()
        with tempfile.TemporaryDirectory(prefix="autoanalyst-inspect-") as directory:
            _, summary = _prepare_restore(snapshot, Path(directory))
            return summary

    @classmethod
    def import_snapshot(
        cls, source: str | Path, destination_workspace: str | Path, *, trusted: bool = False
    ) -> Path:
        if trusted is not True:
            raise SchemaError({"reason": "snapshot_trust_required"})
        snapshot = Path(source).expanduser().resolve()
        requested = Path(destination_workspace).expanduser().absolute()
        if requested.is_symlink():
            raise SchemaError({"reason": "snapshot_destination_not_empty"})
        destination = requested.resolve()
        if not snapshot.is_file():
            raise DataError({"reason": "snapshot_not_found"})
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise SchemaError({"reason": "snapshot_destination_not_empty"})

        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)
        restore_root = Path(tempfile.mkdtemp(prefix=".autoanalyst-restore-", dir=parent))
        try:
            _prepare_restore(snapshot, restore_root)
            # Validation and any known migrations finish before publishing the
            # destination. An incompatible snapshot must leave the target empty.
            if destination.exists():
                if not destination.is_dir() or any(destination.iterdir()):
                    raise SchemaError({"reason": "snapshot_destination_not_empty"})
                destination.rmdir()
            os.rename(restore_root, destination)
            return destination
        finally:
            if restore_root.exists():
                if (
                    restore_root.resolve().parent != parent.resolve()
                    or not restore_root.name.startswith(".autoanalyst-restore-")
                ):
                    raise SchemaError({"reason": "snapshot_path_unsafe"})
                shutil.rmtree(restore_root, ignore_errors=True)

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


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def _require_idle_catalog(path: Path) -> None:
    connection = _readonly(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'analysis_runs'"
        ).fetchone()
        if (
            exists
            and connection.execute(
                "SELECT 1 FROM analysis_runs WHERE status IN ('pending','running') LIMIT 1"
            ).fetchone()
        ):
            raise ResourceError({"reason": "snapshot_worker_active"})
    finally:
        connection.close()


def _validate_catalog(path: Path) -> None:
    """Check only built-in schema/migrations, never execute uploaded SQL objects."""
    connection = _readonly(path)
    reference = sqlite3.connect(":memory:")
    try:
        rows = connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        migrations = sorted(
            (Path(__file__).parents[1] / "storage" / "migrations").glob(
                "[0-9][0-9][0-9][0-9]_*.sql"
            )
        )
        if (
            not rows
            or not migrations
            or [row["version"] for row in rows] != list(range(1, len(rows) + 1))
            or len(rows) > len(migrations)
        ):
            raise SchemaError({"reason": "snapshot_catalog_incompatible"})
        try:
            layout, _ = migration_layout(
                {row["version"]: row["checksum"] for row in rows}, migrations
            )
        except SchemaError as exc:
            raise SchemaError({"reason": "snapshot_catalog_incompatible"}) from exc
        for row in rows:
            migration = layout[row["version"]]
            sql = migration.read_text(encoding="utf-8").replace("\r\n", "\n")
            if row["checksum"] != hashlib.sha256(sql.encode()).hexdigest():
                raise SchemaError({"reason": "snapshot_catalog_incompatible"})
            for statement in _sql_statements(sql):
                reference.execute(statement)

        def schema(db):
            return {
                tuple(row)
                for row in db.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
                )
            }

        if schema(connection) != schema(reference):
            raise SchemaError({"reason": "snapshot_catalog_incompatible"})
        if (
            connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise DataError({"reason": "snapshot_catalog_invalid"})
        if connection.execute(
            "SELECT 1 FROM projects WHERE format_version != '2.0' OR default_language NOT IN ('en','tr') LIMIT 1"
        ).fetchone():
            raise SchemaError({"reason": "snapshot_catalog_incompatible"})
    except sqlite3.Error as exc:
        raise DataError({"reason": "snapshot_catalog_invalid"}) from exc
    finally:
        connection.close()
        reference.close()


def _prepare_restore(snapshot: Path, root: Path) -> tuple[dict[str, Any], SnapshotInspection]:
    if not snapshot.is_file():
        raise DataError({"reason": "snapshot_not_found"})
    if snapshot.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ResourceError({"reason": "snapshot_size_limit"})
    try:
        manifest = _extract_and_verify(snapshot, root)
    except zipfile.BadZipFile as exc:
        raise DataError({"reason": "snapshot_invalid_zip"}) from exc
    _validate_catalog(root / _CATALOG_NAME)
    _require_idle_catalog(root / _CATALOG_NAME)
    # Only our own missing, known migrations may run, after validating the old schema.
    restored = SQLiteCatalog(root)
    _validate_records(restored)
    store = ArtifactStore(root)
    rows = _snapshot_artifacts(root / _CATALOG_NAME)
    actual = {
        row["artifact_id"]: (row["relative_path"], row["sha256"], row["byte_size"]) for row in rows
    }
    declared = {
        item["artifact_id"]: (item["path"], item["sha256"], item["byte_size"])
        for item in manifest["artifacts"]
    }
    if actual != declared:
        raise DataError({"reason": "snapshot_catalog_artifacts_mismatch"})
    for item in manifest["artifacts"]:
        artifact = restored.get_artifact(item["artifact_id"])
        if _safe_relative(artifact.relative_path).parts[
            1
        ] != artifact.project_id or not store.verify(artifact):
            raise DataError({"reason": "snapshot_restored_artifact_invalid"})
    with restored.connection() as connection:
        counts = {
            name: int(connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0])
            for name in ("projects", "datasets", "dataset_versions", "analysis_results")
        }
        models = connection.execute(
            "SELECT COUNT(*) FROM artifacts WHERE kind='binary_model' OR media_type='application/x-joblib'"
        ).fetchone()[0]
    return manifest, SnapshotInspection(
        manifest["autoanalyst_version"],
        manifest["created_at"],
        counts["projects"],
        counts["datasets"],
        counts["dataset_versions"],
        counts["analysis_results"],
        len(rows),
        int(models),
        int(manifest["catalog"]["byte_size"])
        + sum(int(item["byte_size"]) for item in manifest["artifacts"]),
    )


def _validate_records(catalog: SQLiteCatalog) -> None:
    """Decode only the fixed domain registry; no models or arbitrary Python objects."""
    from ..preparation.operations import validate_step

    definitions = (
        (
            "analysis_specs",
            AnalysisSpec,
            ("spec_id", "project_id", "input_version_id", "spec_hash", "module_id"),
        ),
        (
            "analysis_runs",
            AnalysisRun,
            (
                "run_id",
                "project_id",
                "spec_id",
                "parent_run_id",
                "kind",
                "status",
                "error_code",
                "result_id",
            ),
        ),
        ("analysis_results", AnalysisResult, ("result_id", "run_id", "module_id", "outcome")),
    )
    try:
        with catalog.connection() as connection:
            for table, expected_type, fields in definitions:
                for row in connection.execute("SELECT * FROM " + table):
                    value = from_json(row["payload_json"])
                    if not isinstance(value, expected_type):
                        raise ValueError("Unexpected domain payload")
                    for field in fields:
                        item = getattr(value, field)
                        if getattr(item, "value", item) != row[field]:
                            raise ValueError("Catalog and payload disagree")
            versions = [
                row[0] for row in connection.execute("SELECT version_id FROM dataset_versions")
            ]
            recipes = [
                row[0] for row in connection.execute("SELECT recipe_id FROM preparation_recipes")
            ]
            previews = [
                row[0] for row in connection.execute("SELECT preview_id FROM preparation_previews")
            ]
        for version_id in versions:
            catalog.get_version(version_id)
        for recipe_id in recipes:
            for step in catalog.get_preparation_recipe(recipe_id).ordered_steps:
                validate_step(step)
        for preview_id in previews:
            catalog.get_preparation_preview(preview_id)
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        DataError,
        SchemaError,
        sqlite3.Error,
    ) as exc:
        raise SchemaError({"reason": "snapshot_catalog_incompatible"}) from exc


def _snapshot_artifacts(catalog_copy: Path) -> tuple[sqlite3.Row, ...]:
    connection = _readonly(catalog_copy)
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
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES or sum(item.file_size for item in infos) > MAX_UNPACKED_BYTES:
            raise ResourceError({"reason": "snapshot_size_limit"})
        for item in infos:
            mode = stat.S_IFMT(item.external_attr >> 16)
            if (
                item.is_dir()
                or mode not in (0, stat.S_IFREG)
                or item.flag_bits & 1
                or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                raise SchemaError({"reason": "snapshot_archive_unsupported"})
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise SchemaError({"reason": "snapshot_duplicate_entry"})
        safe_names = {_safe_relative(name).as_posix() for name in names}
        if len({name.casefold() for name in safe_names}) != len(safe_names):
            raise SchemaError({"reason": "snapshot_duplicate_entry"})
        if _MANIFEST_NAME not in safe_names or _CATALOG_NAME not in safe_names:
            raise SchemaError({"reason": "snapshot_required_entry_missing"})
        if archive.getinfo(_MANIFEST_NAME).file_size > MAX_MANIFEST_BYTES:
            raise ResourceError({"reason": "snapshot_size_limit"})
        try:
            manifest = json.loads(archive.read(_MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataError({"reason": "snapshot_manifest_invalid"}) from exc
        _validate_manifest(manifest)
        expected_sizes = {
            _CATALOG_NAME: manifest["catalog"]["byte_size"],
            **{item["path"]: item["byte_size"] for item in manifest["artifacts"]},
        }
        if any(
            archive.getinfo(name).file_size != size
            for name, size in expected_sizes.items()
            if name in safe_names
        ):
            raise DataError({"reason": "snapshot_entry_size_mismatch"})
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
    source_version = value.get("autoanalyst_version")
    if (
        not isinstance(source_version, str)
        or len(source_version) > 64
        or not re.fullmatch(r"\d+\.\d+\.\d+(?:[a-zA-Z0-9.]+)?", source_version)
        or source_version.split(".")[0] != __version__.split(".")[0]
    ):
        raise SchemaError({"reason": "snapshot_version_unsupported"})
    try:
        stamp = datetime.fromisoformat(value["created_at"].replace("Z", "+00:00"))
        if stamp.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise SchemaError({"reason": "snapshot_manifest_shape_invalid"}) from exc
    catalog = value.get("catalog")
    artifacts = value.get("artifacts")
    if not isinstance(catalog, dict) or not isinstance(artifacts, list):
        raise SchemaError({"reason": "snapshot_manifest_shape_invalid"})
    if catalog.get("path") != _CATALOG_NAME:
        raise SchemaError({"reason": "snapshot_catalog_path_invalid"})
    if (
        not _valid_sha(catalog.get("sha256"))
        or type(catalog.get("byte_size")) is not int
        or not 0 < catalog["byte_size"] <= MAX_CATALOG_BYTES
    ):
        raise SchemaError({"reason": "snapshot_catalog_metadata_invalid"})
    seen: set[str] = set()
    identifiers: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid"})
        path = _safe_relative(str(item.get("path", ""))).as_posix()
        if len(PurePosixPath(path).parts) < 4 or PurePosixPath(path).parts[0] != "projects":
            raise SchemaError({"reason": "snapshot_path_unsafe"})
        if path in seen:
            raise SchemaError({"reason": "snapshot_artifact_path_duplicate", "path": path})
        seen.add(path)
        try:
            identifier = require_uuid(str(item.get("artifact_id", "")), "artifact_id")
        except (ValueError, TypeError, AttributeError) as exc:
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid"}) from exc
        if identifier in identifiers:
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid"})
        identifiers.add(identifier)
        if (
            not isinstance(item.get("artifact_id"), str)
            or not _valid_sha(item.get("sha256"))
            or type(item.get("byte_size")) is not int
            or int(item["byte_size"]) < 0
        ):
            raise SchemaError({"reason": "snapshot_artifact_metadata_invalid", "path": path})


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)
    }
    if (
        not value
        or "\\" in value
        or path.as_posix() != value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(
            any(char in '<>:"|?*' or ord(char) < 32 for char in part)
            or part.endswith((".", " "))
            or part.split(".")[0].upper() in reserved
            for part in path.parts
        )
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
