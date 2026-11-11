"""Read-only SQLite catalog view for disposable analysis workers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .sqlite import SQLiteCatalog, WorkspacePaths


class ReadOnlySQLiteCatalog(SQLiteCatalog):
    """SQLiteCatalog-compatible reader that cannot open write transactions.

    Workers use this view so a coding mistake cannot mutate catalog metadata.  The
    app-owned coordinator remains the sole SQLite publisher.
    """

    def __init__(self, workspace: str | Path | WorkspacePaths) -> None:
        self.paths = (
            workspace if isinstance(workspace, WorkspacePaths) else WorkspacePaths.create(workspace)
        )
        self.migrations_dir = Path(__file__).with_name("migrations")
        if not self.paths.catalog.is_file():
            raise FileNotFoundError(f"catalog does not exist: {self.paths.catalog}")

    def connect(self) -> sqlite3.Connection:
        uri = self.paths.catalog.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        raise RuntimeError("read-only worker catalog cannot open write transactions")
        yield  # pragma: no cover
