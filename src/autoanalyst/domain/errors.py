"""Stable domain error categories without localized user-facing messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .codec import FrozenDict, freeze_json


class ErrorCode(str, Enum):
    DATA_ERROR = "data_error"
    SCHEMA_ERROR = "schema_error"
    METHOD_NOT_APPLICABLE = "method_not_applicable"
    RESOURCE_ERROR = "resource_error"
    CANCELLATION = "cancellation"
    DEPENDENCY_ERROR = "dependency_error"
    UNEXPECTED_EXECUTION = "unexpected_execution"


@dataclass(slots=True)
class AutoAnalystError(Exception):
    code: ErrorCode
    context: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        self.code = ErrorCode(self.code)
        self.context = freeze_json(self.context)
        Exception.__init__(self, self.code.value)


class DataError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.DATA_ERROR, freeze_json(context))


class SchemaError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.SCHEMA_ERROR, freeze_json(context))


class MethodNotApplicableError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.METHOD_NOT_APPLICABLE, freeze_json(context))


class ResourceError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.RESOURCE_ERROR, freeze_json(context))


class CancellationError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.CANCELLATION, freeze_json(context))


class DependencyError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.DEPENDENCY_ERROR, freeze_json(context))


class UnexpectedExecutionError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] = FrozenDict()) -> None:
        super().__init__(ErrorCode.UNEXPECTED_EXECUTION, freeze_json(context))
