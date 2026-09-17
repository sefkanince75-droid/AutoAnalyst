"""SQLite catalog, migrations, and concrete V2 metadata operations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from ..domain.codec import utc_now
from ..domain.datasets import Dataset, DatasetSource, DatasetVersion, DatasetVersionKind, SourceFormat
from ..domain.errors import DataError, SchemaError
from ..domain.results import Artifact


WORKSPACE_ENV = "AUTOANALYST_WORKSPACE"


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    root: Path
    catalog: Path
    projects: Path
    staging: Path
    logs: Path

    @classmethod
    def create(cls, override: str | Path | None = None) -> "WorkspacePaths":
        if override is not None:
            root = Path(override).expanduser()
        elif os.environ.get(WORKSPACE_ENV):
            root = Path(os.environ[WORKSPACE_ENV]).expanduser()
        else:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                root = Path(local_app_data) / "AutoAnalyst" / "workspace"
            else:
                root = Path.home() / "AppData" / "Local" / "AutoAnalyst" / "workspace"
        root = root.resolve()
        paths = cls(
            root=root,
            catalog=root / "catalog.sqlite",
            projects=root / "projects",
            staging=root / "staging",
            logs=root / "logs",
        )
        for directory in (paths.root, paths.projects, paths.staging, paths.logs):
            directory.mkdir(parents=True, exist_ok=True)
        return paths


class SQLiteCatalog:
    def __init__(
        self,
        workspace: str | Path | WorkspacePaths | None = None,
        *,
        migrations_dir: Path | None = None,
    ) -> None:
        self.paths = workspace if isinstance(workspace, WorkspacePaths) else WorkspacePaths.create(workspace)
        self.migrations_dir = migrations_dir or Path(__file__).with_name("migrations")
        self._apply_migrations()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.paths.catalog, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def migration_records(self) -> tuple[sqlite3.Row, ...]:
        with self.connection() as connection:
            return tuple(connection.execute("SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version"))

    def insert_project(self, project) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project.project_id,
                    project.name,
                    _timestamp(project.created_at),
                    _timestamp(project.updated_at),
                    project.format_version,
                    project.default_language,
                    project.revision,
                    _timestamp(project.archived_at) if project.archived_at else None,
                    project.schema_version,
                ),
            )

    def get_project(self, project_id: str):
        from ..domain.datasets import Project

        with self.connection() as connection:
            row = connection.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "project_not_found", "project_id": project_id})
        return Project(
            project_id=row["project_id"],
            name=row["name"],
            created_at=_datetime(row["created_at"]),
            updated_at=_datetime(row["updated_at"]),
            format_version=row["format_version"],
            default_language=row["default_language"],
            revision=row["revision"],
            archived_at=_datetime(row["archived_at"]) if row["archived_at"] else None,
            schema_version=row["schema_version"],
        )

    def list_projects(self):
        with self.connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT project_id FROM projects ORDER BY created_at, project_id")]
        return tuple(self.get_project(project_id) for project_id in ids)

    def rename_project(self, project_id: str, name: str, updated_at: datetime) -> object:
        current = self.get_project(project_id)
        next_revision = current.revision + 1
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE projects SET name = ?, updated_at = ?, revision = ?
                   WHERE project_id = ? AND revision = ?""",
                (name, _timestamp(updated_at), next_revision, project_id, current.revision),
            )
            if cursor.rowcount != 1:
                raise SchemaError({"reason": "project_revision_conflict", "project_id": project_id})
        return self.get_project(project_id)

    def insert_dataset(self, dataset: Dataset) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                (
                    dataset.dataset_id,
                    dataset.project_id,
                    dataset.name,
                    dataset.head_version_id,
                    dataset.head_revision,
                    dataset.schema_version,
                ),
            )

    def get_dataset(self, dataset_id: str) -> Dataset:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "dataset_not_found", "dataset_id": dataset_id})
        return _dataset(row)

    def get_version(self, version_id: str) -> DatasetVersion:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM dataset_versions WHERE version_id = ?", (version_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "dataset_version_not_found", "version_id": version_id})
        return _version(row)

    def list_versions(self, dataset_id: str) -> tuple[DatasetVersion, ...]:
        with self.connection() as connection:
            rows = tuple(
                connection.execute(
                    """SELECT * FROM dataset_versions WHERE dataset_id = ?
                       ORDER BY created_at, version_id""",
                    (dataset_id,),
                )
            )
        return tuple(_version(row) for row in rows)

    def get_artifact(self, artifact_id: str) -> Artifact:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "artifact_not_found", "artifact_id": artifact_id})
        return _artifact(row)

    def get_source(self, source_id: str) -> DatasetSource:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM dataset_sources WHERE source_id = ?", (source_id,)).fetchone()
        if row is None:
            raise DataError({"reason": "dataset_source_not_found", "source_id": source_id})
        return _source(row)

    def list_columns(self, version_id: str) -> tuple[dict[str, object], ...]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM dataset_columns WHERE version_id = ? ORDER BY ordinal",
                (version_id,),
            )
            return tuple(dict(row) for row in rows)

    def list_head_events(self, dataset_id: str) -> tuple[dict[str, object], ...]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM dataset_head_events WHERE dataset_id = ? ORDER BY to_revision",
                (dataset_id,),
            )
            return tuple(dict(row) for row in rows)

    def publish_dataset_import(
        self,
        *,
        artifacts: Sequence[Artifact],
        source: DatasetSource,
        version: DatasetVersion,
        columns: Sequence[object],
        reason: str = "csv_import",
    ) -> Dataset:
        with self.transaction() as connection:
            dataset_row = connection.execute(
                "SELECT * FROM datasets WHERE dataset_id = ?", (version.dataset_id,)
            ).fetchone()
            if dataset_row is None:
                raise DataError({"reason": "dataset_not_found", "dataset_id": version.dataset_id})
            dataset = _dataset(dataset_row)
            if dataset.project_id != source.project_id:
                raise SchemaError({"reason": "dataset_project_mismatch"})
            for artifact in artifacts:
                _insert_artifact(connection, artifact)
            _insert_source(connection, source)
            _insert_version(connection, version)
            connection.execute(
                "INSERT INTO version_inputs VALUES (?, ?, ?)",
                (version.version_id, source.source_id, 0),
            )
            for column in columns:
                connection.execute(
                    """INSERT INTO dataset_columns
                       (version_id, column_id, display_name, physical_name, physical_type,
                        semantic_hint, ordinal, is_system)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        version.version_id,
                        column.column_id,
                        column.display_name,
                        column.physical_name,
                        column.physical_type,
                        column.semantic_hint,
                        column.ordinal,
                        int(column.is_system),
                    ),
                )
            _move_head(connection, dataset, version.version_id, reason, utc_now())
        return self.get_dataset(version.dataset_id)

    def move_head(self, dataset_id: str, version_id: str, *, reason: str) -> Dataset:
        with self.transaction() as connection:
            dataset_row = connection.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
            version_row = connection.execute(
                "SELECT dataset_id FROM dataset_versions WHERE version_id = ?", (version_id,)
            ).fetchone()
            if dataset_row is None or version_row is None:
                raise DataError({"reason": "dataset_or_version_not_found"})
            if version_row["dataset_id"] != dataset_id:
                raise SchemaError({"reason": "version_dataset_mismatch"})
            _move_head(connection, _dataset(dataset_row), version_id, reason, utc_now())
        return self.get_dataset(dataset_id)

    def _apply_migrations(self) -> None:
        migration_files = sorted(self.migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not migration_files:
            raise SchemaError({"reason": "migration_files_missing"})
        with self.connection() as connection:
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            applied = (
                {row["version"]: row["checksum"] for row in connection.execute("SELECT version, checksum FROM schema_migrations")}
                if has_table
                else {}
            )
            for migration_path in migration_files:
                version = int(migration_path.name.split("_", 1)[0])
                content = migration_path.read_bytes()
                normalized_content = content.decode("utf-8").replace("\r\n", "\n")
                checksum = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise SchemaError({"reason": "migration_checksum_mismatch", "version": version})
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _sql_statements(normalized_content):
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES (?, ?, ?)",
                        (version, checksum, _timestamp(utc_now())),
                    )
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()


def _sql_statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                yield statement
    if buffer.strip():
        raise SchemaError({"reason": "incomplete_migration_statement"})


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("SQLite timestamps must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _dataset(row: Mapping[str, object]) -> Dataset:
    return Dataset(
        dataset_id=str(row["dataset_id"]),
        project_id=str(row["project_id"]),
        name=str(row["name"]),
        head_version_id=str(row["head_version_id"]) if row["head_version_id"] else None,
        head_revision=int(row["head_revision"]),
        schema_version=str(row["schema_version"]),
    )


def _artifact(row: Mapping[str, object]) -> Artifact:
    return Artifact(
        artifact_id=str(row["artifact_id"]),
        project_id=str(row["project_id"]),
        owner_run_id=str(row["owner_run_id"]),
        kind=str(row["kind"]),
        relative_path=str(row["relative_path"]),
        media_type=str(row["media_type"]),
        byte_size=int(row["byte_size"]),
        sha256=str(row["sha256"]),
        format_version=str(row["format_version"]),
        created_at=_datetime(str(row["created_at"])),
        schema_version=str(row["schema_version"]),
    )


def _source(row: Mapping[str, object]) -> DatasetSource:
    return DatasetSource(
        source_id=str(row["source_id"]),
        project_id=str(row["project_id"]),
        original_name=str(row["original_name"]),
        format=SourceFormat(str(row["format"])),
        raw_artifact_id=str(row["raw_artifact_id"]),
        raw_sha256=str(row["raw_sha256"]),
        byte_size=int(row["byte_size"]),
        imported_at=_datetime(str(row["imported_at"])),
        schema_version=str(row["schema_version"]),
    )


def _version(row: Mapping[str, object]) -> DatasetVersion:
    return DatasetVersion(
        version_id=str(row["version_id"]),
        dataset_id=str(row["dataset_id"]),
        kind=DatasetVersionKind(str(row["kind"])),
        created_at=_datetime(str(row["created_at"])),
        created_by_run_id=str(row["created_by_run_id"]),
        table_artifact_id=str(row["table_artifact_id"]),
        row_count=int(row["row_count"]),
        column_count=int(row["column_count"]),
        schema_hash=str(row["schema_hash"]),
        content_fingerprint=str(row["content_fingerprint"]),
        recipe_id=str(row["recipe_id"]) if row["recipe_id"] else None,
        parse_contract=str(row["parse_contract"]) if row["parse_contract"] else None,
        schema_version=str(row["schema_version"]),
    )


def _insert_artifact(connection: sqlite3.Connection, artifact: Artifact) -> None:
    connection.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            artifact.artifact_id,
            artifact.project_id,
            artifact.owner_run_id,
            artifact.kind,
            artifact.relative_path,
            artifact.media_type,
            artifact.byte_size,
            artifact.sha256,
            artifact.format_version,
            _timestamp(artifact.created_at),
            artifact.schema_version,
        ),
    )


def _insert_source(connection: sqlite3.Connection, source: DatasetSource) -> None:
    connection.execute(
        "INSERT INTO dataset_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source.source_id,
            source.project_id,
            source.original_name,
            source.format.value,
            source.raw_artifact_id,
            source.raw_sha256,
            source.byte_size,
            _timestamp(source.imported_at),
            source.schema_version,
        ),
    )


def _insert_version(connection: sqlite3.Connection, version: DatasetVersion) -> None:
    connection.execute(
        "INSERT INTO dataset_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            version.version_id,
            version.dataset_id,
            version.kind.value,
            _timestamp(version.created_at),
            version.created_by_run_id,
            version.table_artifact_id,
            version.row_count,
            version.column_count,
            version.schema_hash,
            version.content_fingerprint,
            version.recipe_id,
            version.parse_contract,
            version.schema_version,
        ),
    )


def _move_head(
    connection: sqlite3.Connection,
    dataset: Dataset,
    version_id: str,
    reason: str,
    changed_at: datetime,
) -> None:
    next_revision = dataset.head_revision + 1
    cursor = connection.execute(
        """UPDATE datasets SET head_version_id = ?, head_revision = ?
           WHERE dataset_id = ? AND head_revision = ?""",
        (version_id, next_revision, dataset.dataset_id, dataset.head_revision),
    )
    if cursor.rowcount != 1:
        raise SchemaError({"reason": "dataset_head_conflict", "dataset_id": dataset.dataset_id})
    connection.execute(
        "INSERT INTO dataset_head_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid4()),
            dataset.dataset_id,
            dataset.head_version_id,
            version_id,
            dataset.head_revision,
            next_revision,
            reason,
            _timestamp(changed_at),
        ),
    )
