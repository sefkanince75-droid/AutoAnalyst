from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autoanalyst.application.snapshots import _validate_catalog
from autoanalyst.domain.errors import DataError, SchemaError
from autoanalyst.storage.schema_compat import checksum
from autoanalyst.storage.sqlite import SQLiteCatalog

MIGRATIONS = Path(__file__).parents[2] / "src/autoanalyst/storage/migrations"


def legacy_workspace(root: Path, *, source_locks=True):
    root.mkdir(exist_ok=True)
    paths = sorted(MIGRATIONS.glob("*.sql"))
    selected = [*paths[:5], paths[6], *([paths[7]] if source_locks else [])]
    with sqlite3.connect(root / "catalog.sqlite") as connection:
        for version, path in enumerate(selected, 1):
            connection.executescript(path.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations VALUES(?,?,?)",
                (version, checksum(path), "2026-01-01"),
            )
            connection.commit()
        connection.execute(
            "INSERT INTO projects VALUES('p','Preserved','now','now','2.0','tr',0,NULL,'2.0')"
        )
        connection.execute(
            "INSERT INTO artifacts VALUES('a','p','r','binary_model','projects/p/model.joblib','application/x-joblib',1,'hash','2.0','now','2.0')"
        )
        connection.execute("INSERT INTO artifact_trust VALUES('a','quarantined','snapshot','now')")


@pytest.mark.parametrize("source_locks", [False, True])
def test_known_old_history_preserves_rows_and_security_state(tmp_path, source_locks):
    legacy_workspace(tmp_path, source_locks=source_locks)
    _validate_catalog(tmp_path / "catalog.sqlite")
    catalog = SQLiteCatalog(tmp_path)
    assert len(catalog.migration_records()) == 8
    assert catalog.artifact_is_deserialization_trusted("a") is False
    with catalog.connection() as connection:
        assert connection.execute("SELECT name FROM projects").fetchone()[0] == "Preserved"
        assert (
            connection.execute(
                "SELECT applied_at FROM schema_migrations WHERE version=7"
            ).fetchone()[0]
            == "2026-01-01"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            "'join'"
            in connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='dataset_version_lineage'"
            ).fetchone()[0]
        )
    assert len(SQLiteCatalog(tmp_path).migration_records()) == 8


@pytest.mark.parametrize("tamper", ["hash", "extra_table", "newer"])
def test_unknown_history_rejected_without_changing_existing_rows(tmp_path, tamper):
    legacy_workspace(tmp_path)
    with sqlite3.connect(tmp_path / "catalog.sqlite") as connection:
        if tamper == "hash":
            connection.execute("UPDATE schema_migrations SET checksum='unknown' WHERE version=7")
        elif tamper == "extra_table":
            connection.execute("CREATE TABLE unexpected(value TEXT)")
        else:
            connection.execute("INSERT INTO schema_migrations VALUES(8,'unknown','now')")
    with sqlite3.connect(tmp_path / "catalog.sqlite") as connection:
        before = list(connection.iterdump())
    with pytest.raises(SchemaError):
        SQLiteCatalog(tmp_path)
    with sqlite3.connect(tmp_path / "catalog.sqlite") as connection:
        assert list(connection.iterdump()) == before


def test_upgraded_quarantined_model_is_rejected_before_deserialization(tmp_path, monkeypatch):
    from autoanalyst.analyses.binary.module import BinaryClassificationModule
    from autoanalyst.storage.artifacts import ArtifactStore

    legacy_workspace(tmp_path)
    module = BinaryClassificationModule(SQLiteCatalog(tmp_path), ArtifactStore(tmp_path))
    monkeypatch.setattr(
        "joblib.load", lambda path: pytest.fail("Quarantined model was deserialized")
    )
    with pytest.raises(DataError) as error:
        module._load_model({"artifact_id": "a"})
    assert error.value.context["reason"] == "binary_model_artifact_untrusted"
