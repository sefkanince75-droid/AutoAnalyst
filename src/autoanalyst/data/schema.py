"""Canonical column metadata and schema fingerprint rules."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4, uuid5

from ..domain.codec import fingerprint, require_uuid
from ..domain.errors import SchemaError


INTERNAL_ROW_ID = "__aa_internal_row_id__"
INTERNAL_ROW_ORDER = "__aa_internal_row_order__"
INTERNAL_NAMES = frozenset({INTERNAL_ROW_ID, INTERNAL_ROW_ORDER})


@dataclass(frozen=True, slots=True)
class DatasetColumn:
    column_id: str
    display_name: str
    physical_name: str
    physical_type: str
    semantic_hint: str | None
    ordinal: int
    is_system: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "column_id", require_uuid(self.column_id, "column_id"))
        if not self.display_name or not self.physical_name or not self.physical_type:
            raise ValueError("Dataset columns require display, physical, and type names")
        if self.ordinal < 0:
            raise ValueError("Dataset column ordinal cannot be negative")


def validate_display_names(names: list[str]) -> tuple[str, ...]:
    normalized = tuple(str(name) for name in names)
    if not normalized or any(not name for name in normalized):
        raise SchemaError({"reason": "empty_column_name"})
    duplicates = sorted({name for name in normalized if normalized.count(name) > 1})
    if duplicates:
        raise SchemaError({"reason": "duplicate_column_names", "columns": duplicates})
    return normalized


def build_columns(
    version_id: str,
    display_names: list[str],
    physical_types: list[str],
) -> tuple[DatasetColumn, ...]:
    names = validate_display_names(display_names)
    if len(names) != len(physical_types):
        raise SchemaError({"reason": "column_type_count_mismatch"})
    namespace = UUID(require_uuid(version_id, "version_id"))
    system = (
        DatasetColumn(
            str(uuid5(namespace, INTERNAL_ROW_ID)),
            "row_id",
            INTERNAL_ROW_ID,
            "string",
            "identifier",
            0,
            True,
        ),
        DatasetColumn(
            str(uuid5(namespace, INTERNAL_ROW_ORDER)),
            "row_order",
            INTERNAL_ROW_ORDER,
            "int64",
            "ordinal",
            1,
            True,
        ),
    )
    user_columns = tuple(
        DatasetColumn(
            column_id=(column_id := str(uuid4())),
            display_name=name,
            physical_name=f"__aa_col_{UUID(column_id).hex}__",
            physical_type=physical_type,
            semantic_hint=_semantic_hint(physical_type),
            ordinal=index + len(system),
        )
        for index, (name, physical_type) in enumerate(zip(names, physical_types, strict=True))
    )
    columns = system + user_columns
    physical_names = tuple(column.physical_name for column in columns)
    if len(set(physical_names)) != len(physical_names) or not INTERNAL_NAMES.issubset(physical_names):
        raise SchemaError({"reason": "internal_column_collision"})
    return columns


def schema_fingerprint(columns: tuple[DatasetColumn, ...]) -> str:
    stable_schema = [
        {
            "display_name": column.display_name,
            "physical_type": column.physical_type,
            "semantic_hint": column.semantic_hint,
            "ordinal": column.ordinal,
            "is_system": column.is_system,
        }
        for column in columns
    ]
    return fingerprint({"columns": stable_schema})


def _semantic_hint(physical_type: str) -> str:
    lowered = physical_type.lower()
    if "bool" in lowered:
        return "boolean"
    if any(token in lowered for token in ("int", "float", "decimal")):
        return "numeric"
    if "date" in lowered or "time" in lowered:
        return "datetime"
    return "unknown"
