"""Project, dataset, source, version, and column-role models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .codec import SCHEMA_VERSION, require_sha256, require_utc, require_uuid


class SourceFormat(str, Enum):
    CSV = "csv"
    XLSX = "xlsx"


class DatasetVersionKind(str, Enum):
    IMPORTED = "imported"
    PREPARED = "prepared"
    COMBINED = "combined"


class SemanticType(str, Enum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    IDENTIFIER = "identifier"
    TEXT = "text"


class ColumnUsage(str, Enum):
    FEATURE = "feature"
    TARGET = "target"
    GROUP = "group"
    TIME = "time"
    EXCLUDED = "excluded"
    PROVENANCE = "provenance"


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    created_at: datetime
    updated_at: datetime
    format_version: str = "2.0"
    default_language: str = "en"
    revision: int = 0
    archived_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", require_uuid(self.project_id, "project_id"))
        require_utc(self.created_at, "created_at")
        require_utc(self.updated_at, "updated_at")
        if self.archived_at is not None:
            require_utc(self.archived_at, "archived_at")
        if not self.name.strip() or self.revision < 0:
            raise ValueError("Project name is required and revision cannot be negative")


@dataclass(frozen=True, slots=True)
class Dataset:
    dataset_id: str
    project_id: str
    name: str
    head_version_id: str | None = None
    head_revision: int = 0
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", require_uuid(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "project_id", require_uuid(self.project_id, "project_id"))
        if self.head_version_id is not None:
            object.__setattr__(
                self, "head_version_id", require_uuid(self.head_version_id, "head_version_id")
            )
        if not self.name.strip() or self.head_revision < 0:
            raise ValueError("Dataset name is required and head_revision cannot be negative")


@dataclass(frozen=True, slots=True)
class DatasetSource:
    source_id: str
    project_id: str
    original_name: str
    format: SourceFormat
    raw_artifact_id: str
    raw_sha256: str
    byte_size: int
    imported_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("source_id", "project_id", "raw_artifact_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "format", SourceFormat(self.format))
        object.__setattr__(self, "raw_sha256", require_sha256(self.raw_sha256, "raw_sha256"))
        require_utc(self.imported_at, "imported_at")
        if not self.original_name or self.byte_size < 0:
            raise ValueError("Source name is required and byte_size cannot be negative")


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    version_id: str
    dataset_id: str
    kind: DatasetVersionKind
    created_at: datetime
    created_by_run_id: str
    table_artifact_id: str
    row_count: int
    column_count: int
    schema_hash: str
    content_fingerprint: str
    recipe_id: str | None = None
    parse_contract: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("version_id", "dataset_id", "created_by_run_id", "table_artifact_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        if self.recipe_id is not None:
            object.__setattr__(self, "recipe_id", require_uuid(self.recipe_id, "recipe_id"))
        object.__setattr__(self, "kind", DatasetVersionKind(self.kind))
        object.__setattr__(self, "schema_hash", require_sha256(self.schema_hash, "schema_hash"))
        object.__setattr__(
            self,
            "content_fingerprint",
            require_sha256(self.content_fingerprint, "content_fingerprint"),
        )
        require_utc(self.created_at, "created_at")
        if self.row_count < 0 or self.column_count < 0:
            raise ValueError("Dataset dimensions cannot be negative")


@dataclass(frozen=True, slots=True)
class ColumnRole:
    column_id: str
    semantic_type: SemanticType
    usages: tuple[ColumnUsage, ...]
    confirmed: bool = False
    available_at_prediction: bool | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "column_id", require_uuid(self.column_id, "column_id"))
        object.__setattr__(self, "semantic_type", SemanticType(self.semantic_type))
        usages = tuple(ColumnUsage(item) for item in self.usages)
        if not usages or len(set(usages)) != len(usages):
            raise ValueError("ColumnRole requires unique usages")
        usage_set = set(usages)
        if ColumnUsage.EXCLUDED in usage_set and len(usage_set) != 1:
            raise ValueError("excluded cannot be combined with another usage")
        if ColumnUsage.FEATURE in usage_set and usage_set.intersection(
            {ColumnUsage.TARGET, ColumnUsage.GROUP, ColumnUsage.TIME, ColumnUsage.PROVENANCE}
        ):
            raise ValueError("target, group, time, and provenance columns cannot also be features")
        object.__setattr__(self, "usages", usages)
