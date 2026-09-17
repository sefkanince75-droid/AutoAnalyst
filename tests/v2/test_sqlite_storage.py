from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from autoanalyst.domain.errors import SchemaError
from autoanalyst.storage.sqlite import SQLiteCatalog, WorkspacePaths


MIGRATIONS = Path(__file__).parents[2] / "src" / "autoanalyst" / "storage" / "migrations"


def test_empty_workspace_applies_migration_once(tmp_path: Path) -> None:
    catalog = SQLiteCatalog(tmp_path)

    assert catalog.paths.catalog.is_file()
    assert [row["version"] for row in catalog.migration_records()] == [1]
    for reserved in (catalog.paths.projects, catalog.paths.staging, catalog.paths.logs):
        assert reserved.is_dir()

    reopened = SQLiteCatalog(tmp_path)
    assert len(reopened.migration_records()) == 1


def test_changed_migration_checksum_is_rejected(tmp_path: Path) -> None:
    migration_copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, migration_copy)
    workspace = tmp_path / "workspace"
    SQLiteCatalog(workspace, migrations_dir=migration_copy)
    migration = migration_copy / "0001_initial.sql"
    migration.write_text(migration.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")

    with pytest.raises(SchemaError) as mismatch:
        SQLiteCatalog(workspace, migrations_dir=migration_copy)
    assert mismatch.value.context["reason"] == "migration_checksum_mismatch"


def test_foreign_keys_are_enforced_on_every_connection(tmp_path: Path) -> None:
    catalog = SQLiteCatalog(tmp_path)

    with catalog.connection() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO datasets VALUES (?, ?, ?, ?, ?, ?)",
                ("dataset", "missing-project", "Invalid", None, 0, "2.0"),
            )


def test_workspace_environment_override_is_used(tmp_path: Path, monkeypatch) -> None:
    configured = tmp_path / "configured-workspace"
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(configured))

    paths = WorkspacePaths.create()

    assert paths.root == configured.resolve()
    assert paths.catalog.parent == configured.resolve()
