"""Controlled DuckDB access to catalog-owned immutable Parquet versions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from ..domain.errors import DataError, SchemaError
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .schema import INTERNAL_ROW_ORDER, DatasetColumn

MAX_TABLE_PAGE_ROWS = 500
MAX_TABLE_PAGE_COLUMNS = 50


@dataclass(frozen=True, slots=True)
class TableFilter:
    column_id: str
    operator: str
    value: object = None


@dataclass(frozen=True, slots=True)
class DistinctValues:
    values: tuple[object, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class VersionTable:
    version_id: str
    parquet_path: Path
    columns: tuple[DatasetColumn, ...]

    @property
    def by_id(self) -> dict[str, DatasetColumn]:
        return {column.column_id: column for column in self.columns}

    @property
    def physical_names(self) -> frozenset[str]:
        return frozenset(column.physical_name for column in self.columns)


class TableAccess:
    """Exposes fixed table operations without accepting SQL from callers."""

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def inspect_schema(self, version_id: str) -> tuple[DatasetColumn, ...]:
        table = self.resolve(version_id)
        with self._connection() as connection:
            described = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(table.parquet_path)]
            ).fetchall()
        actual = {row[0]: str(row[1]) for row in described}
        if set(actual) != set(table.physical_names):
            raise SchemaError(
                {"reason": "parquet_catalog_schema_mismatch", "version_id": version_id}
            )
        return table.columns

    def row_count(self, version_id: str) -> int:
        table = self.resolve(version_id)
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(table.parquet_path)]
                ).fetchone()[0]
            )

    def selected_columns(self, version_id: str, column_ids: tuple[str, ...]):
        table = self.resolve(version_id)
        selected = self._columns(table, column_ids)
        projection = ", ".join(
            self.quote_identifier(column.physical_name, table) for column in selected
        )
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            return connection.execute(
                f"SELECT {projection} FROM read_parquet(?) ORDER BY {order}",
                [str(table.parquet_path)],
            ).to_arrow_table()

    def distinct_values(
        self, version_id: str, column_id: str, *, limit: int = 200, search: str = ""
    ) -> DistinctValues:
        if (
            type(limit) is not int
            or not 1 <= limit <= 500
            or not isinstance(search, str)
            or len(search) > 200
        ):
            raise SchemaError({"reason": "invalid_table_page"})
        table = self.resolve(version_id)
        column = self._column(table, column_id)
        name = self.quote_identifier(column.physical_name, table)
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT {name} FROM read_parquet(?) WHERE {name} IS NOT NULL AND strpos(CAST({name} AS VARCHAR), ?) > 0 ORDER BY {name} LIMIT ?",
                [str(table.parquet_path), search, limit + 1],
            ).fetchall()
        return DistinctValues(tuple(row[0] for row in rows[:limit]), len(rows) > limit)

    def page(
        self,
        version_id: str,
        column_ids: tuple[str, ...],
        *,
        limit: int = 100,
        offset: int = 0,
    ):
        """Return a bounded projection in stable source order, without full Arrow materialization."""
        if (
            type(limit) is not int
            or not 1 <= limit <= MAX_TABLE_PAGE_ROWS
            or type(offset) is not int
            or not 0 <= offset < 2**63
        ):
            raise SchemaError({"reason": "invalid_table_page"})
        if len(column_ids) > MAX_TABLE_PAGE_COLUMNS:
            raise SchemaError({"reason": "table_page_column_limit"})
        table = self.resolve(version_id)
        selected = self._columns(table, column_ids)
        projection = ", ".join(
            self.quote_identifier(column.physical_name, table) for column in selected
        )
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            return connection.execute(
                f"SELECT {projection} FROM read_parquet(?) ORDER BY {order} LIMIT ? OFFSET ?",
                [str(table.parquet_path), limit, offset],
            ).to_arrow_table()

    def filtered_projection(
        self,
        version_id: str,
        column_ids: tuple[str, ...],
        filters: tuple[TableFilter, ...],
    ):
        table = self.resolve(version_id)
        selected = self._columns(table, column_ids)
        projection = ", ".join(
            self.quote_identifier(column.physical_name, table) for column in selected
        )
        clauses: list[str] = []
        parameters: list[object] = [str(table.parquet_path)]
        for item in filters:
            column = self._column(table, item.column_id)
            clause, values = _filter_clause(
                self.quote_identifier(column.physical_name, table), item.operator, item.value
            )
            clauses.append(clause)
            parameters.extend(values)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            return connection.execute(
                f"SELECT {projection} FROM read_parquet(?){where} ORDER BY {order}", parameters
            ).to_arrow_table()

    def aggregate_value(self, version_id: str, column_id: str, aggregate: str) -> object:
        table = self.resolve(version_id)
        column = self._column(table, column_id)
        identifier = self.quote_identifier(column.physical_name, table)
        expressions = {
            "count": f"count({identifier})",
            "min": f"min({identifier})",
            "max": f"max({identifier})",
            "sum": f"sum({identifier})",
            "mean": f"avg({identifier})",
            "median": f"median({identifier})",
        }
        if aggregate not in expressions:
            raise SchemaError({"reason": "unsupported_aggregate", "aggregate": aggregate})
        with self._connection() as connection:
            return connection.execute(
                f"SELECT {expressions[aggregate]} FROM read_parquet(?)", [str(table.parquet_path)]
            ).fetchone()[0]

    def duplicate_rows(self, version_id: str, subset_column_ids: tuple[str, ...]):
        table = self.resolve(version_id)
        subset = self._columns(table, subset_column_ids)
        partition = ", ".join(
            self.quote_identifier(column.physical_name, table) for column in subset
        )
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            return connection.execute(
                f"""SELECT * FROM read_parquet(?)
                    QUALIFY count(*) OVER (PARTITION BY {partition}) > 1
                    ORDER BY {order}""",
                [str(table.parquet_path)],
            ).to_arrow_table()

    def ordered_export(self, version_id: str, column_ids: tuple[str, ...] | None = None):
        table = self.resolve(version_id)
        selected = table.columns if column_ids is None else self._columns(table, column_ids)
        projection = ", ".join(
            self.quote_identifier(column.physical_name, table) for column in selected
        )
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            return connection.execute(
                f"SELECT {projection} FROM read_parquet(?) ORDER BY {order}",
                [str(table.parquet_path)],
            ).to_arrow_table()

    @contextmanager
    def export_batches(self, version_id: str):
        """Stream all user columns in source order; keep connection alive for the reader."""
        table = self.resolve(version_id)
        columns = tuple(column for column in table.columns if not column.is_system)
        if not columns:
            raise SchemaError({"reason": "export_no_columns"})
        projection = ", ".join(self.quote_identifier(c.physical_name, table) for c in columns)
        order = self.quote_identifier(INTERNAL_ROW_ORDER, table)
        with self._connection() as connection:
            reader = connection.execute(
                f"SELECT {projection} FROM read_parquet(?) ORDER BY {order}",
                [str(table.parquet_path)],
            ).to_arrow_reader(batch_size=65_536)
            try:
                yield columns, reader, pq.read_schema(table.parquet_path)
            finally:
                reader.close()

    def resolve(self, version_id: str) -> VersionTable:
        version = self.catalog.get_version(version_id)
        artifact = self.catalog.get_artifact(version.table_artifact_id)
        if artifact.media_type != "application/vnd.apache.parquet":
            raise DataError({"reason": "version_artifact_is_not_parquet", "version_id": version_id})
        if not self.artifact_store.verify(artifact):
            raise DataError(
                {"reason": "artifact_verification_failed", "artifact_id": artifact.artifact_id}
            )
        path = self.artifact_store.resolve_relative_path(artifact.relative_path)
        rows = self.catalog.list_columns(version_id)
        columns = tuple(
            DatasetColumn(
                column_id=str(row["column_id"]),
                display_name=str(row["display_name"]),
                physical_name=str(row["physical_name"]),
                physical_type=str(row["physical_type"]),
                semantic_hint=str(row["semantic_hint"])
                if row["semantic_hint"] is not None
                else None,
                ordinal=int(row["ordinal"]),
                is_system=bool(row["is_system"]),
            )
            for row in rows
        )
        return VersionTable(version_id, path, columns)

    @staticmethod
    def quote_identifier(physical_name: str, table: VersionTable) -> str:
        if physical_name not in table.physical_names:
            raise SchemaError(
                {"reason": "untrusted_physical_column", "physical_name": physical_name}
            )
        return f'"{physical_name.replace(chr(34), chr(34) * 2)}"'

    @staticmethod
    @contextmanager
    def _connection() -> Iterator[duckdb.DuckDBPyConnection]:
        connection = duckdb.connect(":memory:")
        try:
            connection.execute("SET autoinstall_known_extensions = false")
            connection.execute("SET autoload_known_extensions = false")
            connection.execute("SET enable_progress_bar = false")
            connection.execute("SET memory_limit = '512MiB'")
            connection.execute("SET threads = 1")
            connection.execute("SET max_temp_directory_size = '0B'")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _column(table: VersionTable, column_id: str) -> DatasetColumn:
        try:
            return table.by_id[column_id]
        except KeyError as exc:
            raise SchemaError({"reason": "column_not_found", "column_id": column_id}) from exc

    @classmethod
    def _columns(
        cls, table: VersionTable, column_ids: tuple[str, ...]
    ) -> tuple[DatasetColumn, ...]:
        if not column_ids or len(set(column_ids)) != len(column_ids):
            raise SchemaError({"reason": "unique_column_selection_required"})
        return tuple(cls._column(table, column_id) for column_id in column_ids)


def _filter_clause(identifier: str, operator: str, value: object) -> tuple[str, list[object]]:
    if operator == "is_null" or (operator == "eq" and value is None):
        return f"{identifier} IS NULL", []
    if operator == "is_not_null" or (operator == "ne" and value is None):
        return f"{identifier} IS NOT NULL", []
    operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    if operator in operators:
        if value is None:
            raise SchemaError({"reason": "invalid_null_comparison", "operator": operator})
        return f"{identifier} {operators[operator]} ?", [value]
    if operator == "contains_literal":
        if value is None:
            raise SchemaError({"reason": "contains_value_required"})
        return f"contains(CAST({identifier} AS VARCHAR), ?)", [str(value)]
    raise SchemaError({"reason": "unsupported_filter_operator", "operator": operator})
