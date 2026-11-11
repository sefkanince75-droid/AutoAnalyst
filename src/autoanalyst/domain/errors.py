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


def _context(value: FrozenDict | dict[str, object] | None) -> FrozenDict:
    return freeze_json({} if value is None else value)


class DataError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.DATA_ERROR, _context(context))


class SchemaError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.SCHEMA_ERROR, _context(context))


class MethodNotApplicableError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.METHOD_NOT_APPLICABLE, _context(context))


class ResourceError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.RESOURCE_ERROR, _context(context))


class CancellationError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.CANCELLATION, _context(context))


class DependencyError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.DEPENDENCY_ERROR, _context(context))


class UnexpectedExecutionError(AutoAnalystError):
    def __init__(self, context: FrozenDict | dict[str, object] | None = None) -> None:
        super().__init__(ErrorCode.UNEXPECTED_EXECUTION, _context(context))
