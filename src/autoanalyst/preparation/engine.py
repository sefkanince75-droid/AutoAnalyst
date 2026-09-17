"""Full-data, closed-operation preparation preview execution."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import os
from typing import Any

import duckdb

from ..data.schema import DatasetColumn, INTERNAL_ROW_ID, INTERNAL_ROW_ORDER, schema_fingerprint
from ..data.table_access import TableAccess, VersionTable, _filter_clause
from ..domain.codec import (
    FrozenDict,
    SCHEMA_VERSION,
    decode_typed_label,
    encode_typed_label,
    fingerprint,
    freeze_json,
    require_sha256,
    require_utc,
    require_uuid,
    utc_now,
)
from ..domain.errors import MethodNotApplicableError, ResourceError, SchemaError
from ..domain.plans import LearningScope, PreparationRecipe, PreparationStep
from ..domain.results import Artifact
from ..storage.artifacts import ArtifactStore
from .operations import OperationId, validate_step


class PreviewStatus(str, Enum):
    READY = "ready"
    APPLIED = "applied"


@dataclass(frozen=True, slots=True)
class StepExecutionResult:
    step_id: str
    affected_row_count: int
    affected_cell_count: int | None
    resolved_values: FrozenDict = field(default_factory=FrozenDict)
    warnings: tuple[str, ...] = ()
    sample_row_ids: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", require_uuid(self.step_id, "step_id"))
        object.__setattr__(self, "resolved_values", freeze_json(self.resolved_values))
        if self.affected_row_count < 0 or (
            self.affected_cell_count is not None and self.affected_cell_count < 0
        ):
            raise ValueError("Step result counts cannot be negative")


@dataclass(frozen=True, slots=True)
class PreparationPreview:
    preview_id: str
    recipe_id: str
    recipe_hash: str
    base_version_id: str
    expected_head_revision: int
    status: PreviewStatus
    candidate_artifact_id: str
    before_row_count: int
    after_row_count: int
    before_column_count: int
    after_column_count: int
    step_results: tuple[StepExecutionResult, ...]
    warnings: tuple[str, ...]
    sample_changes: tuple[FrozenDict, ...]
    candidate_columns: tuple[DatasetColumn, ...]
    append_input_version_ids: tuple[str, ...]
    content_fingerprint: str
    schema_hash: str
    created_at: datetime
    expires_at: datetime
    applied_version_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("preview_id", "recipe_id", "base_version_id", "candidate_artifact_id"):
            object.__setattr__(self, name, require_uuid(getattr(self, name), name))
        if self.applied_version_id is not None:
            object.__setattr__(
                self,
                "applied_version_id",
                require_uuid(self.applied_version_id, "applied_version_id"),
            )
        object.__setattr__(self, "status", PreviewStatus(self.status))
        object.__setattr__(self, "recipe_hash", require_sha256(self.recipe_hash, "recipe_hash"))
        object.__setattr__(
            self,
            "append_input_version_ids",
            tuple(require_uuid(item, "append_input_version_id") for item in self.append_input_version_ids),
        )
        object.__setattr__(
            self, "sample_changes", tuple(freeze_json(item) for item in self.sample_changes)
        )
        object.__setattr__(
            self, "content_fingerprint", require_sha256(self.content_fingerprint, "content_fingerprint")
        )
        object.__setattr__(self, "schema_hash", require_sha256(self.schema_hash, "schema_hash"))
        require_utc(self.created_at, "created_at")
        require_utc(self.expires_at, "expires_at")
        if self.expected_head_revision < 0:
            raise ValueError("Preview revision cannot be negative")
        if min(
            self.before_row_count,
            self.after_row_count,
            self.before_column_count,
            self.after_column_count,
        ) < 0:
            raise ValueError("Preview counts cannot be negative")
        if len(set(self.append_input_version_ids)) != len(self.append_input_version_ids):
            raise ValueError("Preview append inputs must be unique")
        if self.status is PreviewStatus.APPLIED and self.applied_version_id is None:
            raise ValueError("Applied previews require applied_version_id")
        if self.status is PreviewStatus.READY and self.applied_version_id is not None:
            raise ValueError("Ready previews cannot have applied_version_id")


@dataclass(frozen=True, slots=True)
class PreviewComputation:
    preview: PreparationPreview
    candidate_artifact: Artifact


class PreparationEngine:
    def __init__(self, table_access: TableAccess, artifact_store: ArtifactStore) -> None:
        self.table_access = table_access
        self.artifact_store = artifact_store

    def preview(
        self,
        recipe: PreparationRecipe,
        *,
        preview_id: str,
        expected_head_revision: int,
        expires_at: datetime,
    ) -> PreviewComputation:
        for step in recipe.ordered_steps:
            validate_step(step)
            if step.learning_scope is LearningScope.TRAIN_ONLY:
                raise MethodNotApplicableError(
                    {"reason": "train_only_preparation", "step_id": step.step_id}
                )
        base_version = self.table_access.catalog.get_version(recipe.base_version_id)
        base = self.table_access.resolve(recipe.base_version_id)
        created_at = utc_now()
        require_utc(expires_at, "expires_at")
        if expires_at <= created_at:
            raise SchemaError({"reason": "preview_expiry_must_be_future"})

        connection = duckdb.connect(":memory:")
        staged = None
        try:
            _configure(connection)
            connection.execute(
                "CREATE TEMP TABLE prep_0 AS SELECT * FROM read_parquet(?)",
                [str(base.parquet_path)],
            )
            current_table = "prep_0"
            columns = base.columns
            before_rows = _count(connection, current_table)
            step_results: list[StepExecutionResult] = []
            append_inputs: list[str] = []
            samples: list[FrozenDict] = []
            for index, step in enumerate(recipe.ordered_steps, start=1):
                current_table, columns, result, appended = self._execute_step(
                    connection,
                    current_table,
                    f"prep_{index}",
                    columns,
                    step,
                    recipe.base_version_id,
                )
                step_results.append(result)
                append_inputs.extend(appended)
                for row_id in result.sample_row_ids:
                    if len(samples) >= 100:
                        break
                    samples.append(
                        FrozenDict(
                            {
                                "step_position": step.position,
                                "row_id": row_id,
                                "operation": step.operation,
                            }
                        )
                    )
            after_rows = _count(connection, current_table)
            columns = _refresh_physical_types(connection, current_table, columns)
            staged = self.artifact_store.create_staging(
                project_id=recipe.project_id,
                owner_run_id=preview_id,
                kind="preparation_candidate",
                media_type="application/vnd.apache.parquet",
                format_version="1",
            )
            row_order = _identifier(INTERNAL_ROW_ORDER, columns)
            connection.execute(
                f"COPY (SELECT * FROM {_table(current_table)} ORDER BY {row_order}) "
                "TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(staged.staging_path)],
            )
            with staged.staging_path.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            candidate = self.artifact_store.finalize(staged)
            execution_descriptor = [
                {
                    "step_position": position,
                    "affected_rows": result.affected_row_count,
                    "affected_cells": result.affected_cell_count,
                    "resolved_values": result.resolved_values,
                    "warnings": result.warnings,
                }
                for position, result in enumerate(step_results)
            ]
            appended_versions = tuple(dict.fromkeys(append_inputs))
            append_fingerprints = [
                self.table_access.catalog.get_version(version_id).content_fingerprint
                for version_id in appended_versions
            ]
            content_hash = fingerprint(
                {
                    "base_content_fingerprint": base_version.content_fingerprint,
                    "recipe_hash": recipe.recipe_hash,
                    "step_results": execution_descriptor,
                    "append_input_fingerprints": append_fingerprints,
                }
            )
            warnings = tuple(warning for result in step_results for warning in result.warnings)
            preview = PreparationPreview(
                preview_id=preview_id,
                recipe_id=recipe.recipe_id,
                recipe_hash=recipe.recipe_hash,
                base_version_id=recipe.base_version_id,
                expected_head_revision=expected_head_revision,
                status=PreviewStatus.READY,
                candidate_artifact_id=candidate.artifact_id,
                before_row_count=before_rows,
                after_row_count=after_rows,
                before_column_count=sum(not column.is_system for column in base.columns),
                after_column_count=sum(not column.is_system for column in columns),
                step_results=tuple(step_results),
                warnings=warnings,
                sample_changes=tuple(samples[:100]),
                candidate_columns=columns,
                append_input_version_ids=appended_versions,
                content_fingerprint=content_hash,
                schema_hash=schema_fingerprint(columns),
                created_at=created_at,
                expires_at=expires_at,
            )
            return PreviewComputation(preview, candidate)
        except duckdb.OutOfMemoryException as exc:
            if staged is not None:
                staged.staging_path.unlink(missing_ok=True)
            raise ResourceError({"reason": "preparation_memory_exhausted"}) from exc
        except Exception:
            if staged is not None:
                staged.staging_path.unlink(missing_ok=True)
            raise
        finally:
            connection.close()

    def _execute_step(
        self,
        connection: duckdb.DuckDBPyConnection,
        source_table: str,
        target_table: str,
        columns: tuple[DatasetColumn, ...],
        step: PreparationStep,
        base_version_id: str,
    ) -> tuple[str, tuple[DatasetColumn, ...], StepExecutionResult, tuple[str, ...]]:
        operation = OperationId(step.operation)
        if operation is OperationId.FILTER_ROWS:
            return _filter(connection, source_table, target_table, columns, step)
        if operation is OperationId.DROP_COLUMNS:
            return _drop_columns(connection, source_table, target_table, columns, step)
        if operation is OperationId.RENAME_COLUMN:
            return _rename_column(connection, source_table, columns, step)
        if operation is OperationId.CAST_COLUMN:
            return _cast_column(connection, source_table, target_table, columns, step)
        if operation is OperationId.FILL_MISSING:
            return _fill_missing(connection, source_table, target_table, columns, step)
        if operation is OperationId.DROP_MISSING_ROWS:
            return _drop_missing(connection, source_table, target_table, columns, step)
        if operation is OperationId.REMOVE_DUPLICATES:
            return _remove_duplicates(connection, source_table, target_table, columns, step)
        if operation is OperationId.REPLACE_VALUES:
            return _replace_values(connection, source_table, target_table, columns, step)
        if operation is OperationId.REPLACE_NON_FINITE:
            return _replace_non_finite(connection, source_table, target_table, columns, step)
        if operation is OperationId.APPEND_ROWS:
            return self._append_rows(
                connection, source_table, target_table, columns, step, base_version_id
            )
        raise SchemaError({"reason": "unsupported_preparation_operation"})

    def _append_rows(
        self,
        connection: duckdb.DuckDBPyConnection,
        source_table: str,
        target_table: str,
        columns: tuple[DatasetColumn, ...],
        step: PreparationStep,
        base_version_id: str,
    ):
        input_ids = tuple(step.parameters["input_version_ids"])
        if base_version_id in input_ids:
            raise SchemaError({"reason": "base_version_cannot_be_appended"})
        before = _count(connection, source_table)
        statements = [f"SELECT * FROM {_table(source_table)}"]
        parameters: list[object] = []
        next_order = before
        for version_id in input_ids:
            table = self.table_access.resolve(version_id)
            base_dataset = self.table_access.catalog.get_dataset(
                self.table_access.catalog.get_version(base_version_id).dataset_id
            )
            incoming_dataset = self.table_access.catalog.get_dataset(
                self.table_access.catalog.get_version(version_id).dataset_id
            )
            if incoming_dataset.project_id != base_dataset.project_id:
                raise SchemaError({"reason": "append_project_mismatch"})
            _require_compatible_schema(columns, table.columns)
            row_id = _identifier(INTERNAL_ROW_ID, columns)
            row_order = _identifier(INTERNAL_ROW_ORDER, columns)
            selections: list[str] = []
            for column in columns:
                identifier = _identifier(column.physical_name, columns)
                if column.physical_name == INTERNAL_ROW_ID:
                    selections.append(f"md5(? || ':' || CAST({row_id} AS VARCHAR)) AS {row_id}")
                    parameters.append(version_id)
                elif column.physical_name == INTERNAL_ROW_ORDER:
                    selections.append(
                        f"(? + row_number() OVER (ORDER BY {row_order}) - 1)::BIGINT AS {row_order}"
                    )
                    parameters.append(next_order)
                else:
                    selections.append(identifier)
            statements.append(f"SELECT {', '.join(selections)} FROM read_parquet(?)")
            parameters.append(str(table.parquet_path))
            next_order += self.table_access.row_count(version_id)
        connection.execute(
            f"CREATE TEMP TABLE {_table(target_table)} AS {' UNION ALL '.join(statements)}", parameters
        )
        after = _count(connection, target_table)
        sample = _sample_row_ids(connection, target_table, columns, offset=before)
        return (
            target_table,
            columns,
            StepExecutionResult(step.step_id, after - before, None, sample_row_ids=sample),
            input_ids,
        )


def _filter(connection, source, target, columns, step):
    column = _column(columns, step.parameters["column_id"])
    clause, parameters = _filter_clause(
        _identifier(column.physical_name, columns),
        str(step.parameters["operator"]),
        step.parameters.get("value"),
    )
    before = _count(connection, source)
    removed = _sample_where(connection, source, columns, f"NOT ({clause})", parameters)
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT * FROM {_table(source)} WHERE {clause}",
        parameters,
    )
    after = _count(connection, target)
    return target, columns, StepExecutionResult(step.step_id, before - after, None, sample_row_ids=removed), ()


def _drop_columns(connection, source, target, columns, step):
    dropped = set(step.parameters["column_ids"])
    selected = tuple(column for column in columns if column.column_id not in dropped)
    if any(_column(columns, column_id).is_system for column_id in dropped):
        raise SchemaError({"reason": "system_column_cannot_be_dropped"})
    if not any(not column.is_system for column in selected):
        raise SchemaError({"reason": "at_least_one_user_column_required"})
    rows = _count(connection, source)
    sample = _sample_row_ids(connection, source, columns)
    projection = ", ".join(_identifier(column.physical_name, columns) for column in selected)
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT {projection} FROM {_table(source)}"
    )
    selected = tuple(replace(column, ordinal=index) for index, column in enumerate(selected))
    return target, selected, StepExecutionResult(step.step_id, rows, rows * len(dropped), sample_row_ids=sample), ()


def _rename_column(connection, source, columns, step):
    column = _column(columns, step.parameters["column_id"])
    if column.is_system:
        raise SchemaError({"reason": "system_column_cannot_be_renamed"})
    name = str(step.parameters["new_name"]).strip()
    if any(item.column_id != column.column_id and item.display_name == name for item in columns if not item.is_system):
        raise SchemaError({"reason": "duplicate_column_names", "columns": (name,)})
    updated = tuple(replace(item, display_name=name) if item.column_id == column.column_id else item for item in columns)
    rows = _count(connection, source)
    return source, updated, StepExecutionResult(step.step_id, rows, 0, sample_row_ids=_sample_row_ids(connection, source, columns)), ()


def _cast_column(connection, source, target, columns, step):
    column = _user_column(columns, step.parameters["column_id"])
    target_type = str(step.parameters["target_type"])
    sql_type, semantic = _cast_type(target_type)
    identifier = _identifier(column.physical_name, columns)
    function = "CAST" if step.parameters["failure_policy"] == "strict" else "TRY_CAST"
    expression = f"{function}({identifier} AS {sql_type})"
    invalid = int(
        connection.execute(
            f"SELECT count(*) FROM {_table(source)} WHERE {identifier} IS NOT NULL "
            f"AND TRY_CAST({identifier} AS {sql_type}) IS NULL"
        ).fetchone()[0]
    )
    projection = _replace_expression(columns, column.column_id, expression)
    try:
        connection.execute(
            f"CREATE TEMP TABLE {_table(target)} AS SELECT {projection} FROM {_table(source)}"
        )
    except duckdb.Error as exc:
        raise SchemaError({"reason": "cast_failure", "column_id": column.column_id}) from exc
    updated = tuple(
        replace(item, physical_type=sql_type, semantic_hint=semantic)
        if item.column_id == column.column_id
        else item
        for item in columns
    )
    return target, updated, StepExecutionResult(step.step_id, invalid, invalid, sample_row_ids=_sample_invalid_cast(connection, source, columns, identifier, sql_type)), ()


def _fill_missing(connection, source, target, columns, step):
    selected_ids = set(step.parameters["column_ids"])
    for column_id in selected_ids:
        _user_column(columns, column_id)
    selected = tuple(column for column in columns if column.column_id in selected_ids)
    strategy = str(step.parameters["strategy"])
    expressions: dict[str, str] = {}
    parameters: list[object] = []
    resolved: dict[str, object] = {}
    affected_cells = 0
    warnings: list[str] = []
    sample_ids: list[str] = []
    for column in selected:
        identifier = _identifier(column.physical_name, columns)
        missing = int(
            connection.execute(
                f"SELECT count(*) FROM {_table(source)} WHERE {identifier} IS NULL"
            ).fetchone()[0]
        )
        affected_cells += missing
        sample_ids.extend(_sample_where(connection, source, columns, f"{identifier} IS NULL", []))
        if strategy == "constant":
            value = decode_typed_label(step.parameters["value"])
            _require_value_compatible(column, value)
        elif strategy in {"mean", "median"}:
            if not _is_numeric(column):
                raise SchemaError({"reason": "numeric_fill_required", "column_id": column.column_id})
            aggregate = "avg" if strategy == "mean" else "median"
            value = connection.execute(
                f"SELECT {aggregate}({identifier}) FROM {_table(source)}"
            ).fetchone()[0]
        else:
            value_row = connection.execute(
                f"""SELECT {identifier}, count(*) AS frequency FROM {_table(source)}
                    WHERE {identifier} IS NOT NULL GROUP BY {identifier}
                    ORDER BY frequency DESC, CAST({identifier} AS VARCHAR) ASC LIMIT 1"""
            ).fetchone()
            value = value_row[0] if value_row else None
        resolved[column.column_id] = encode_typed_label(value)
        if value is None:
            warnings.append("fill_value_not_defined")
            expressions[column.column_id] = identifier
        else:
            expressions[column.column_id] = f"COALESCE({identifier}, ?)"
            parameters.append(value)
    projection = _replace_expressions(columns, expressions)
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT {projection} FROM {_table(source)}", parameters
    )
    affected_rows = _unique_count(sample_ids)
    return target, columns, StepExecutionResult(step.step_id, affected_rows, affected_cells, FrozenDict(resolved), tuple(warnings), tuple(dict.fromkeys(sample_ids))[:100]), ()


def _drop_missing(connection, source, target, columns, step):
    selected = tuple(_user_column(columns, column_id) for column_id in step.parameters["column_ids"])
    clause = " AND ".join(f"{_identifier(column.physical_name, columns)} IS NOT NULL" for column in selected)
    before = _count(connection, source)
    sample = _sample_where(connection, source, columns, f"NOT ({clause})", [])
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT * FROM {_table(source)} WHERE {clause}"
    )
    after = _count(connection, target)
    return target, columns, StepExecutionResult(step.step_id, before - after, None, sample_row_ids=sample), ()


def _remove_duplicates(connection, source, target, columns, step):
    selected = tuple(_user_column(columns, column_id) for column_id in step.parameters["column_ids"])
    partition = ", ".join(_identifier(column.physical_name, columns) for column in selected)
    order = _identifier(INTERNAL_ROW_ORDER, columns)
    before = _count(connection, source)
    sample = tuple(
        str(row[0])
        for row in connection.execute(
            f"""SELECT {_identifier(INTERNAL_ROW_ID, columns)} FROM {_table(source)}
                QUALIFY row_number() OVER (PARTITION BY {partition} ORDER BY {order}) > 1
                ORDER BY {order} LIMIT 100"""
        ).fetchall()
    )
    connection.execute(
        f"""CREATE TEMP TABLE {_table(target)} AS SELECT * FROM {_table(source)}
            QUALIFY row_number() OVER (PARTITION BY {partition} ORDER BY {order}) = 1"""
    )
    after = _count(connection, target)
    return target, columns, StepExecutionResult(step.step_id, before - after, None, sample_row_ids=sample), ()


def _replace_values(connection, source, target, columns, step):
    column = _user_column(columns, step.parameters["column_id"])
    identifier = _identifier(column.physical_name, columns)
    cases: list[str] = []
    parameters: list[object] = []
    predicates: list[str] = []
    for entry in step.parameters["mapping"]:
        old = decode_typed_label(entry["from"])
        new = decode_typed_label(entry["to"])
        _require_value_compatible(column, old)
        _require_value_compatible(column, new)
        if old is None:
            cases.append(f"WHEN {identifier} IS NULL THEN ?")
            predicates.append(f"{identifier} IS NULL")
            parameters.append(new)
        else:
            cases.append(f"WHEN {identifier} = ? THEN ?")
            predicates.append(f"{identifier} = ?")
            parameters.extend((old, new))
    expression = f"CASE {' '.join(cases)} ELSE {identifier} END"
    predicate_parameters = [
        decode_typed_label(entry["from"])
        for entry in step.parameters["mapping"]
        if decode_typed_label(entry["from"]) is not None
    ]
    predicate = " OR ".join(predicates)
    affected = int(
        connection.execute(
            f"SELECT count(*) FROM {_table(source)} WHERE {predicate}", predicate_parameters
        ).fetchone()[0]
    )
    sample = _sample_where(connection, source, columns, predicate, predicate_parameters)
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT {_replace_expression(columns, column.column_id, expression)} FROM {_table(source)}",
        parameters,
    )
    return target, columns, StepExecutionResult(step.step_id, affected, affected, sample_row_ids=sample), ()


def _replace_non_finite(connection, source, target, columns, step):
    selected = tuple(_user_column(columns, column_id) for column_id in step.parameters["column_ids"])
    expressions: dict[str, str] = {}
    predicates: list[str] = []
    for column in selected:
        if not _is_numeric(column):
            raise SchemaError({"reason": "numeric_non_finite_replacement_required"})
        identifier = _identifier(column.physical_name, columns)
        predicate = f"isinf(CAST({identifier} AS DOUBLE))"
        predicates.append(predicate)
        expressions[column.column_id] = f"CASE WHEN {predicate} THEN NULL ELSE {identifier} END"
    affected_cells = sum(
        int(connection.execute(f"SELECT count(*) FROM {_table(source)} WHERE {item}").fetchone()[0])
        for item in predicates
    )
    combined = " OR ".join(predicates)
    affected_rows = int(
        connection.execute(f"SELECT count(*) FROM {_table(source)} WHERE {combined}").fetchone()[0]
    )
    sample = _sample_where(connection, source, columns, combined, [])
    connection.execute(
        f"CREATE TEMP TABLE {_table(target)} AS SELECT {_replace_expressions(columns, expressions)} FROM {_table(source)}"
    )
    return target, columns, StepExecutionResult(step.step_id, affected_rows, affected_cells, sample_row_ids=sample), ()


def _configure(connection):
    connection.execute("SET autoinstall_known_extensions = false")
    connection.execute("SET autoload_known_extensions = false")
    connection.execute("SET enable_progress_bar = false")


def _table(name: str) -> str:
    if not name.startswith("prep_") or not name[5:].isdigit():
        raise SchemaError({"reason": "invalid_internal_table_name"})
    return f'"{name}"'


def _identifier(physical_name: str, columns: tuple[DatasetColumn, ...]) -> str:
    allowed = {column.physical_name for column in columns}
    if physical_name not in allowed:
        raise SchemaError({"reason": "untrusted_physical_column"})
    return f'"{physical_name.replace(chr(34), chr(34) * 2)}"'


def _column(columns, column_id):
    try:
        return next(column for column in columns if column.column_id == column_id)
    except StopIteration as exc:
        raise SchemaError({"reason": "column_not_found", "column_id": column_id}) from exc


def _user_column(columns, column_id):
    column = _column(columns, column_id)
    if column.is_system:
        raise SchemaError({"reason": "system_column_operation_forbidden"})
    return column


def _count(connection, table):
    return int(connection.execute(f"SELECT count(*) FROM {_table(table)}").fetchone()[0])


def _replace_expression(columns, target_id, expression):
    return ", ".join(
        f"{expression} AS {_identifier(column.physical_name, columns)}"
        if column.column_id == target_id
        else _identifier(column.physical_name, columns)
        for column in columns
    )


def _replace_expressions(columns, expressions):
    return ", ".join(
        f"{expressions[column.column_id]} AS {_identifier(column.physical_name, columns)}"
        if column.column_id in expressions
        else _identifier(column.physical_name, columns)
        for column in columns
    )


def _sample_where(connection, table, columns, predicate, parameters, limit=100):
    row_id = _identifier(INTERNAL_ROW_ID, columns)
    row_order = _identifier(INTERNAL_ROW_ORDER, columns)
    return tuple(
        str(row[0])
        for row in connection.execute(
            f"SELECT {row_id} FROM {_table(table)} WHERE {predicate} ORDER BY {row_order} LIMIT {int(limit)}",
            parameters,
        ).fetchall()
    )


def _sample_row_ids(connection, table, columns, offset=0):
    row_id = _identifier(INTERNAL_ROW_ID, columns)
    row_order = _identifier(INTERNAL_ROW_ORDER, columns)
    return tuple(
        str(row[0])
        for row in connection.execute(
            f"SELECT {row_id} FROM {_table(table)} WHERE {row_order} >= ? ORDER BY {row_order} LIMIT 100",
            [offset],
        ).fetchall()
    )


def _sample_invalid_cast(connection, table, columns, identifier, sql_type):
    return _sample_where(
        connection,
        table,
        columns,
        f"{identifier} IS NOT NULL AND TRY_CAST({identifier} AS {sql_type}) IS NULL",
        [],
    )


def _unique_count(values):
    return len(set(values))


def _cast_type(target):
    return {
        "numeric": ("DOUBLE", "numeric"),
        "string": ("VARCHAR", "categorical"),
        "categorical": ("VARCHAR", "categorical"),
        "boolean": ("BOOLEAN", "boolean"),
        "datetime": ("TIMESTAMP", "datetime"),
    }[target]


def _is_numeric(column):
    value = column.physical_type.lower()
    return column.semantic_hint == "numeric" or any(
        token in value for token in ("int", "float", "double", "decimal", "hugeint")
    )


def _require_value_compatible(column, value):
    if value is None:
        return
    if _is_numeric(column):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaError({"reason": "typed_value_schema_mismatch", "column_id": column.column_id})
    elif column.semantic_hint == "boolean" or "bool" in column.physical_type.lower():
        if not isinstance(value, bool):
            raise SchemaError({"reason": "typed_value_schema_mismatch", "column_id": column.column_id})
    elif not isinstance(value, str):
        raise SchemaError({"reason": "typed_value_schema_mismatch", "column_id": column.column_id})


def _require_compatible_schema(base, incoming):
    base_user = tuple(column for column in base if not column.is_system)
    incoming_user = tuple(column for column in incoming if not column.is_system)
    base_signature = tuple(
        (column.column_id, column.physical_name, _normalized_type(column)) for column in base_user
    )
    incoming_signature = tuple(
        (column.column_id, column.physical_name, _normalized_type(column)) for column in incoming_user
    )
    if base_signature != incoming_signature:
        raise SchemaError({"reason": "append_schema_incompatible"})


def _normalized_type(column):
    if _is_numeric(column):
        return "numeric"
    lowered = column.physical_type.lower()
    if column.semantic_hint == "boolean" or "bool" in lowered:
        return "boolean"
    if column.semantic_hint == "datetime" or "date" in lowered or "time" in lowered:
        return "datetime"
    return "string"


def _refresh_physical_types(connection, table, columns):
    types = {
        str(row[0]): str(row[1])
        for row in connection.execute(f"DESCRIBE SELECT * FROM {_table(table)}").fetchall()
    }
    return tuple(replace(column, physical_type=types[column.physical_name]) for column in columns)
