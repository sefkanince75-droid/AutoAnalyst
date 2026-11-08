"""Strict compatibility for two previously shipped migration-number layouts."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..domain.codec import utc_now
from ..domain.errors import SchemaError

TRUST_CHECKSUM = "5b56b61707af899d47f82b39e2468af8d1422e4b67762a458d73fb14ac8e3f1f"
SOURCE_CHECKSUM = "f2a9295223296b23f1da6eed58ca54a0de9b5c983e5b751e36001e6793932223"


def checksum(path: Path) -> str:
    return hashlib.sha256(
        path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
    ).hexdigest()


def migration_layout(
    applied: Mapping[int, str], paths: Sequence[Path]
) -> tuple[dict[int, Path], bool]:
    current = {int(path.name.split("_", 1)[0]): path for path in paths}
    legacy = applied.get(6) == TRUST_CHECKSUM
    if legacy:
        if (
            not {7, 8}.issubset(current)
            or checksum(current[7]) != TRUST_CHECKSUM
            or checksum(current[8]) != SOURCE_CHECKSUM
        ):
            raise SchemaError({"reason": "migration_checksum_mismatch", "version": 6})
        layout = {**{k: v for k, v in current.items() if k <= 5}, 6: current[7], 7: current[8]}
    else:
        layout = current
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise SchemaError({"reason": "migration_history_invalid"})
    for version, digest in applied.items():
        if version not in layout:
            raise SchemaError({"reason": "migration_from_newer_application", "version": version})
        if checksum(layout[version]) != digest:
            raise SchemaError({"reason": "migration_checksum_mismatch", "version": version})
    return layout, legacy


def schema_objects(connection):
    return {
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
        )
    }


def bridge_known_history(connection: sqlite3.Connection, paths: Sequence[Path]) -> None:
    """Atomically renumber only verified historical records; preserve all rows.

    Old: 6=quarantine, 7=source locks. Canonical: 6=join, 7=quarantine,
    8=source locks. Unknown hashes or SQL objects are never accepted.
    """
    from .sqlite import _sql_statements

    rows = connection.execute(
        "SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    applied = {int(row[0]): str(row[1]) for row in rows}
    _, legacy = migration_layout(applied, paths)
    if not legacy:
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = connection.execute(
            "SELECT version,checksum,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        applied = {int(row[0]): str(row[1]) for row in rows}
        layout, legacy = migration_layout(applied, paths)
        if not legacy:
            connection.rollback()
            return
        reference = sqlite3.connect(":memory:")
        try:
            for version in sorted(applied):
                for statement in _sql_statements(layout[version].read_text(encoding="utf-8")):
                    reference.execute(statement)
            if schema_objects(connection) != schema_objects(reference):
                raise SchemaError({"reason": "migration_legacy_schema_mismatch"})
        finally:
            reference.close()
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaError({"reason": "migration_legacy_schema_mismatch"})
        old = {int(row[0]): (row[1], row[2]) for row in rows if row[0] >= 6}
        connection.execute("DELETE FROM schema_migrations WHERE version IN (6,7)")
        connection.executemany(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            ((version + 1, *values) for version, values in old.items()),
        )
        join_path = next(path for path in paths if path.name.startswith("0006_"))
        for statement in _sql_statements(join_path.read_text(encoding="utf-8")):
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(6,?,?)",
            (checksum(join_path), utc_now().isoformat().replace("+00:00", "Z")),
        )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SchemaError({"reason": "migration_legacy_schema_mismatch"})
        connection.commit()
    except Exception:
        connection.rollback()
        raise
