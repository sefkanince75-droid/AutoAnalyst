"""Canonical V2 JSON encoding, typed labels, and deterministic fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

SCHEMA_VERSION = "2.0"


class FrozenDict(Mapping[str, Any]):
    """Small immutable mapping used for JSON-shaped domain parameters."""

    __slots__ = ("_items", "_lookup")

    def __init__(self, value: Mapping[str, Any] | tuple[tuple[str, Any], ...] = ()) -> None:
        items = value.items() if isinstance(value, Mapping) else value
        normalized = tuple(
            sorted(((str(key), freeze_json(item)) for key, item in items), key=lambda pair: pair[0])
        )
        if len({key for key, _ in normalized}) != len(normalized):
            raise ValueError("FrozenDict keys must be unique")
        object.__setattr__(self, "_items", normalized)
        object.__setattr__(self, "_lookup", dict(normalized))

    def __getitem__(self, key: str) -> Any:
        return self._lookup[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f"FrozenDict({dict(self._items)!r})"


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value


def require_uuid(value: str, field_name: str) -> str:
    from uuid import UUID

    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc
    return str(parsed)


def require_sha256(value: str, field_name: str) -> str:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return normalized


def freeze_json(value: Any) -> Any:
    """Deep-freeze JSON-compatible values and reject non-finite numbers."""

    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity are not valid domain JSON values")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


class LabelType(str, Enum):
    NULL = "null"
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"


def encode_typed_label(value: None | str | int | float | bool) -> dict[str, Any]:
    if value is None:
        label_type = LabelType.NULL
    elif isinstance(value, bool):
        label_type = LabelType.BOOLEAN
    elif isinstance(value, int):
        label_type = LabelType.INTEGER
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Typed labels cannot contain NaN or Infinity")
        label_type = LabelType.FLOAT
    elif isinstance(value, str):
        label_type = LabelType.STRING
    else:
        raise TypeError(f"Unsupported category label: {type(value).__name__}")
    return {"type": label_type.value, "value": value}


def decode_typed_label(payload: Mapping[str, Any]) -> None | str | int | float | bool:
    label_type = LabelType(payload["type"])
    value = payload.get("value")
    validators = {
        LabelType.NULL: lambda item: item is None,
        LabelType.STRING: lambda item: isinstance(item, str),
        LabelType.INTEGER: lambda item: isinstance(item, int) and not isinstance(item, bool),
        LabelType.FLOAT: lambda item: isinstance(item, float) and math.isfinite(item),
        LabelType.BOOLEAN: lambda item: isinstance(item, bool),
    }
    if not validators[label_type](value):
        raise ValueError(f"Value does not match typed label kind {label_type.value}")
    return value


def _to_primitive(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        payload = {field.name: _to_primitive(getattr(value, field.name)) for field in fields(value)}
        payload["__type__"] = type(value).__name__
        return payload
    if isinstance(value, datetime):
        require_utc(value, "datetime")
        return {"__datetime__": value.isoformat().replace("+00:00", "Z")}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_primitive(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity must use an explicit domain value state")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def to_primitive(value: Any) -> Any:
    return _to_primitive(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        _to_primitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _type_registry() -> dict[str, type[Any]]:
    from .datasets import ColumnRole, Dataset, DatasetSource, DatasetVersion, Project
    from .plans import AnalysisSpec, PreparationRecipe, PreparationStep, ResourceBudget
    from .results import AnalysisResult, Artifact, ChartSpec, Finding, Metric, Report, ResultTable
    from .runs import AnalysisRun, SplitManifest

    classes = (
        Project,
        Dataset,
        DatasetSource,
        DatasetVersion,
        ColumnRole,
        PreparationStep,
        PreparationRecipe,
        ResourceBudget,
        AnalysisSpec,
        AnalysisRun,
        SplitManifest,
        Finding,
        Metric,
        ResultTable,
        ChartSpec,
        Artifact,
        Report,
        AnalysisResult,
    )
    return {item.__name__: item for item in classes}


def from_primitive(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(from_primitive(item) for item in value)
    if isinstance(value, dict):
        if set(value) == {"__datetime__"}:
            parsed = datetime.fromisoformat(value["__datetime__"].replace("Z", "+00:00"))
            return require_utc(parsed, "datetime")
        type_name = value.get("__type__")
        decoded = {key: from_primitive(item) for key, item in value.items() if key != "__type__"}
        if type_name is None:
            return decoded
        model = _type_registry().get(type_name)
        if model is None:
            raise ValueError(f"Unknown domain type: {type_name}")
        return model(**decoded)
    return value


def from_json(payload: str) -> Any:
    return from_primitive(json.loads(payload))
