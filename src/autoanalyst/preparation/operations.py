"""Closed preparation operation definitions and parameter validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from ..domain.codec import FrozenDict, decode_typed_label, freeze_json, require_uuid
from ..domain.errors import SchemaError
from ..domain.plans import LearningScope, PreparationStep


class OperationId(str, Enum):
    FILTER_ROWS = "filter_rows"
    DROP_COLUMNS = "drop_columns"
    RENAME_COLUMN = "rename_column"
    CAST_COLUMN = "cast_column"
    FILL_MISSING = "fill_missing"
    DROP_MISSING_ROWS = "drop_missing_rows"
    REMOVE_DUPLICATES = "remove_duplicates"
    REPLACE_VALUES = "replace_values"
    REPLACE_NON_FINITE = "replace_non_finite"
    APPEND_ROWS = "append_rows"


FILTER_OPERATORS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "contains_literal", "is_null", "is_not_null"}
)
CAST_TARGETS = frozenset({"numeric", "string", "categorical", "boolean", "datetime"})
FAILURE_POLICIES = frozenset({"strict", "invalid_to_null"})
FILL_STRATEGIES = frozenset({"constant", "mean", "median", "mode"})


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    operation_id: OperationId
    version: str
    parameter_schema: FrozenDict
    default_learning_scope: LearningScope
    schema_effect: str


def _definition(
    operation_id: OperationId,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    scope: LearningScope = LearningScope.NONE,
    *,
    types: dict[str, str],
    schema_effect: str,
) -> OperationDefinition:
    return OperationDefinition(
        operation_id,
        "1.0",
        FrozenDict({"required": required, "optional": optional, "types": types}),
        scope,
        schema_effect,
    )


OPERATION_REGISTRY = MappingProxyType(
    {
        OperationId.FILTER_ROWS.value: _definition(
            OperationId.FILTER_ROWS,
            ("column_id", "operator"),
            ("value",),
            types={"column_id": "uuid", "operator": "filter_operator", "value": "scalar_or_null"},
            schema_effect="preserve",
        ),
        OperationId.DROP_COLUMNS.value: _definition(
            OperationId.DROP_COLUMNS,
            ("column_ids",),
            types={"column_ids": "non_empty_uuid_list"},
            schema_effect="remove_selected_user_columns",
        ),
        OperationId.RENAME_COLUMN.value: _definition(
            OperationId.RENAME_COLUMN,
            ("column_id", "new_name"),
            types={"column_id": "uuid", "new_name": "non_empty_string"},
            schema_effect="rename_display_name_preserve_column_id",
        ),
        OperationId.CAST_COLUMN.value: _definition(
            OperationId.CAST_COLUMN,
            ("column_id", "target_type", "failure_policy"),
            types={
                "column_id": "uuid",
                "target_type": "cast_target",
                "failure_policy": "failure_policy",
            },
            schema_effect="replace_selected_physical_type_preserve_column_id",
        ),
        OperationId.FILL_MISSING.value: _definition(
            OperationId.FILL_MISSING,
            ("column_ids", "strategy"),
            ("value",),
            types={
                "column_ids": "non_empty_uuid_list",
                "strategy": "fill_strategy",
                "value": "typed_label",
            },
            schema_effect="preserve",
        ),
        OperationId.DROP_MISSING_ROWS.value: _definition(
            OperationId.DROP_MISSING_ROWS,
            ("column_ids",),
            types={"column_ids": "non_empty_uuid_list"},
            schema_effect="preserve",
        ),
        OperationId.REMOVE_DUPLICATES.value: _definition(
            OperationId.REMOVE_DUPLICATES,
            ("column_ids",),
            ("keep",),
            types={"column_ids": "non_empty_uuid_list", "keep": "first_only"},
            schema_effect="preserve",
        ),
        OperationId.REPLACE_VALUES.value: _definition(
            OperationId.REPLACE_VALUES,
            ("column_id", "mapping"),
            types={"column_id": "uuid", "mapping": "typed_exact_mapping"},
            schema_effect="preserve",
        ),
        OperationId.REPLACE_NON_FINITE.value: _definition(
            OperationId.REPLACE_NON_FINITE,
            ("column_ids",),
            types={"column_ids": "non_empty_uuid_list"},
            schema_effect="preserve",
        ),
        OperationId.APPEND_ROWS.value: _definition(
            OperationId.APPEND_ROWS,
            ("input_version_ids",),
            types={"input_version_ids": "ordered_unique_uuid_list"},
            schema_effect="require_compatible_schema",
        ),
    }
)


def operation_definition(operation: str) -> OperationDefinition:
    try:
        return OPERATION_REGISTRY[OperationId(operation).value]
    except (ValueError, KeyError) as exc:
        raise SchemaError({"reason": "unsupported_preparation_operation", "operation": operation}) from exc


def validate_parameters(operation: str, parameters: FrozenDict | dict[str, Any]) -> FrozenDict:
    definition = operation_definition(operation)
    if not isinstance(parameters, Mapping):
        raise SchemaError({"reason": "operation_parameters_must_be_mapping"})
    frozen = freeze_json(parameters)
    required = set(definition.parameter_schema["required"])
    optional = set(definition.parameter_schema["optional"])
    keys = set(frozen)
    if missing := sorted(required - keys):
        raise SchemaError({"reason": "missing_operation_parameters", "parameters": missing})
    if unknown := sorted(keys - required - optional):
        raise SchemaError({"reason": "unknown_operation_parameters", "parameters": unknown})

    operation_id = definition.operation_id
    if operation_id is OperationId.FILTER_ROWS:
        _uuid(frozen["column_id"])
        operator = str(frozen["operator"])
        if operator not in FILTER_OPERATORS:
            raise SchemaError({"reason": "unsupported_filter_operator", "operator": operator})
        has_value = "value" in frozen
        if operator in {"is_null", "is_not_null"} and has_value:
            raise SchemaError({"reason": "null_filter_does_not_accept_value"})
        if operator not in {"is_null", "is_not_null"} and not has_value:
            raise SchemaError({"reason": "filter_value_required"})
        if operator in {"gt", "gte", "lt", "lte", "contains_literal"} and frozen.get("value") is None:
            raise SchemaError({"reason": "null_only_supports_eq_ne_or_null_operators"})
    elif operation_id is OperationId.RENAME_COLUMN:
        _uuid(frozen["column_id"])
        if not str(frozen["new_name"]).strip():
            raise SchemaError({"reason": "empty_column_name"})
    elif operation_id is OperationId.CAST_COLUMN:
        _uuid(frozen["column_id"])
        if frozen["target_type"] not in CAST_TARGETS:
            raise SchemaError({"reason": "unsupported_cast_target"})
        if frozen["failure_policy"] not in FAILURE_POLICIES:
            raise SchemaError({"reason": "unsupported_failure_policy"})
    elif operation_id is OperationId.FILL_MISSING:
        _uuid_list(frozen["column_ids"], "column_ids")
        if frozen["strategy"] not in FILL_STRATEGIES:
            raise SchemaError({"reason": "unsupported_fill_strategy"})
        if frozen["strategy"] == "constant":
            if "value" not in frozen:
                raise SchemaError({"reason": "fill_constant_required"})
            _typed_value(frozen["value"])
        elif "value" in frozen:
            raise SchemaError({"reason": "learned_fill_does_not_accept_value"})
    elif operation_id in {
        OperationId.DROP_COLUMNS,
        OperationId.DROP_MISSING_ROWS,
        OperationId.REMOVE_DUPLICATES,
        OperationId.REPLACE_NON_FINITE,
    }:
        _uuid_list(frozen["column_ids"], "column_ids")
        if operation_id is OperationId.REMOVE_DUPLICATES and frozen.get("keep", "first") != "first":
            raise SchemaError({"reason": "only_keep_first_is_supported"})
    elif operation_id is OperationId.REPLACE_VALUES:
        _uuid(frozen["column_id"])
        mapping = frozen["mapping"]
        if not isinstance(mapping, tuple) or not mapping:
            raise SchemaError({"reason": "replace_mapping_required"})
        seen: set[str] = set()
        for entry in mapping:
            if not isinstance(entry, FrozenDict) or set(entry) != {"from", "to"}:
                raise SchemaError({"reason": "invalid_replace_mapping"})
            source = _typed_value(entry["from"])
            _typed_value(entry["to"])
            marker = repr((entry["from"]["type"], source))
            if marker in seen:
                raise SchemaError({"reason": "duplicate_replace_source"})
            seen.add(marker)
    elif operation_id is OperationId.APPEND_ROWS:
        version_ids = _uuid_list(frozen["input_version_ids"], "input_version_ids")
        if len(set(version_ids)) != len(version_ids):
            raise SchemaError({"reason": "duplicate_append_input"})
    return frozen


def affected_column_ids(operation: str, parameters: FrozenDict) -> tuple[str, ...]:
    operation_id = OperationId(operation)
    if "column_id" in parameters:
        return (str(parameters["column_id"]),)
    if "column_ids" in parameters:
        return tuple(str(item) for item in parameters["column_ids"])
    return ()


def effective_learning_scope(operation: str, parameters: FrozenDict) -> LearningScope:
    if OperationId(operation) is OperationId.FILL_MISSING and parameters["strategy"] in {
        "mean",
        "median",
        "mode",
    }:
        return LearningScope.DATASET
    return operation_definition(operation).default_learning_scope


def validate_step(step: PreparationStep) -> None:
    definition = operation_definition(step.operation)
    if step.operation_version != definition.version:
        raise SchemaError({"reason": "unsupported_operation_version", "operation": step.operation})
    parameters = validate_parameters(step.operation, step.parameters)
    expected = affected_column_ids(step.operation, parameters)
    if tuple(step.affected_column_ids) != expected:
        raise SchemaError({"reason": "affected_column_ids_mismatch", "operation": step.operation})
    expected_scope = effective_learning_scope(step.operation, parameters)
    if step.learning_scope not in {expected_scope, LearningScope.TRAIN_ONLY}:
        raise SchemaError({"reason": "invalid_learning_scope", "operation": step.operation})


def _uuid(value: object) -> str:
    try:
        return require_uuid(str(value), "column_id")
    except ValueError as exc:
        raise SchemaError({"reason": "invalid_entity_id"}) from exc


def _uuid_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise SchemaError({"reason": f"{field_name}_required"})
    values = tuple(_uuid(item) for item in value)
    if len(set(values)) != len(values):
        raise SchemaError({"reason": f"duplicate_{field_name}"})
    return values


def _typed_value(value: object) -> object:
    if not isinstance(value, FrozenDict):
        raise SchemaError({"reason": "typed_value_required"})
    try:
        return decode_typed_label(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaError({"reason": "invalid_typed_value"}) from exc
