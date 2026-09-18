from __future__ import annotations

from uuid import uuid4

import pytest

from autoanalyst.domain.codec import encode_typed_label
from autoanalyst.domain.errors import SchemaError
from autoanalyst.domain.plans import LearningScope, PreparationStep
from autoanalyst.preparation.operations import (
    operation_definition,
    validate_parameters,
    validate_step,
)


def _id() -> str:
    return str(uuid4())


@pytest.mark.parametrize(
    ("operation", "parameters", "reason"),
    (
        ("filter_rows", [], "operation_parameters_must_be_mapping"),
        ("rename_column", {}, "missing_operation_parameters"),
        (
            "rename_column",
            {"column_id": _id(), "new_name": "x", "extra": 1},
            "unknown_operation_parameters",
        ),
        (
            "filter_rows",
            {"column_id": _id(), "operator": "regex", "value": "x"},
            "unsupported_filter_operator",
        ),
        (
            "filter_rows",
            {"column_id": _id(), "operator": "is_null", "value": None},
            "null_filter_does_not_accept_value",
        ),
        (
            "filter_rows",
            {"column_id": _id(), "operator": "eq"},
            "filter_value_required",
        ),
        (
            "filter_rows",
            {"column_id": _id(), "operator": "gt", "value": None},
            "null_only_supports_eq_ne_or_null_operators",
        ),
        (
            "rename_column",
            {"column_id": _id(), "new_name": "   "},
            "empty_column_name",
        ),
        (
            "cast_column",
            {
                "column_id": _id(),
                "target_type": "object",
                "failure_policy": "strict",
            },
            "unsupported_cast_target",
        ),
        (
            "cast_column",
            {
                "column_id": _id(),
                "target_type": "numeric",
                "failure_policy": "coerce",
            },
            "unsupported_failure_policy",
        ),
        (
            "fill_missing",
            {"column_ids": [_id()], "strategy": "random"},
            "unsupported_fill_strategy",
        ),
        (
            "fill_missing",
            {"column_ids": [_id()], "strategy": "constant"},
            "fill_constant_required",
        ),
        (
            "fill_missing",
            {
                "column_ids": [_id()],
                "strategy": "median",
                "value": encode_typed_label(0),
            },
            "learned_fill_does_not_accept_value",
        ),
        (
            "remove_duplicates",
            {"column_ids": [_id()], "keep": "last"},
            "only_keep_first_is_supported",
        ),
        (
            "replace_values",
            {"column_id": _id(), "mapping": []},
            "replace_mapping_required",
        ),
        (
            "replace_values",
            {
                "column_id": _id(),
                "mapping": [{"from": encode_typed_label("a")}],
            },
            "invalid_replace_mapping",
        ),
        (
            "replace_values",
            {
                "column_id": _id(),
                "mapping": [
                    {
                        "from": encode_typed_label("a"),
                        "to": encode_typed_label("b"),
                    },
                    {
                        "from": encode_typed_label("a"),
                        "to": encode_typed_label("c"),
                    },
                ],
            },
            "duplicate_replace_source",
        ),
        (
            "replace_values",
            {
                "column_id": _id(),
                "mapping": [{"from": "raw", "to": encode_typed_label("b")}],
            },
            "typed_value_required",
        ),
        (
            "replace_values",
            {
                "column_id": _id(),
                "mapping": [
                    {
                        "from": {"type": "unknown", "value": "a"},
                        "to": encode_typed_label("b"),
                    }
                ],
            },
            "invalid_typed_value",
        ),
        (
            "drop_columns",
            {"column_ids": []},
            "column_ids_required",
        ),
        (
            "rename_column",
            {"column_id": "not-a-uuid", "new_name": "x"},
            "invalid_entity_id",
        ),
    ),
)
def test_preparation_parameter_validation_rejects_invalid_contracts(
    operation,
    parameters,
    reason,
) -> None:
    with pytest.raises(SchemaError) as exc_info:
        validate_parameters(operation, parameters)
    assert exc_info.value.context["reason"] == reason


def test_unknown_preparation_operation_is_rejected() -> None:
    with pytest.raises(SchemaError) as exc_info:
        operation_definition("execute_python")
    assert exc_info.value.context["reason"] == "unsupported_preparation_operation"


@pytest.mark.parametrize(
    ("step", "reason"),
    (
        (
            PreparationStep(
                step_id=_id(),
                position=0,
                operation="rename_column",
                operation_version="999",
                parameters={
                    "column_id": _id(),
                    "new_name": "renamed",
                },
                affected_column_ids=(),
                learning_scope=LearningScope.NONE,
            ),
            "unsupported_operation_version",
        ),
        (
            PreparationStep(
                step_id=_id(),
                position=0,
                operation="rename_column",
                operation_version="1.0",
                parameters={
                    "column_id": _id(),
                    "new_name": "renamed",
                },
                affected_column_ids=(),
                learning_scope=LearningScope.NONE,
            ),
            "affected_column_ids_mismatch",
        ),
        (
            PreparationStep(
                step_id=_id(),
                position=0,
                operation="fill_missing",
                operation_version="1.0",
                parameters={
                    "column_ids": [_id()],
                    "strategy": "median",
                },
                affected_column_ids=(),
                learning_scope=LearningScope.NONE,
            ),
            "affected_column_ids_mismatch",
        ),
    ),
)
def test_preparation_step_contract_rejects_invalid_metadata(step, reason) -> None:
    if reason == "affected_column_ids_mismatch" and step.operation == "fill_missing":
        parameters = validate_parameters(step.operation, step.parameters)
        step = PreparationStep(
            step_id=step.step_id,
            position=step.position,
            operation=step.operation,
            operation_version=step.operation_version,
            parameters=parameters,
            affected_column_ids=tuple(parameters["column_ids"]),
            learning_scope=LearningScope.NONE,
        )
        reason = "invalid_learning_scope"

    with pytest.raises(SchemaError) as exc_info:
        validate_step(step)
    assert exc_info.value.context["reason"] == reason
