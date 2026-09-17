"""Preparation and accepted analysis-plan models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .codec import (
    SCHEMA_VERSION,
    FrozenDict,
    freeze_json,
    require_sha256,
    require_utc,
    require_uuid,
)
from .datasets import ColumnRole, ColumnUsage


class LearningScope(str, Enum):
    NONE = "none"
    DATASET = "dataset"
    TRAIN_ONLY = "train_only"
    FULL_DATASET = "dataset"
    TRAINING_ONLY = "train_only"


class AnalysisModuleId(str, Enum):
    PROFILING = "profiling"
    PREPARATION = "preparation"
    COMPARISON = "comparison"
    BINARY_CLASSIFICATION = "binary_classification"


@dataclass(frozen=True, slots=True)
class PreparationStep:
    step_id: str
    position: int
    operation: str
    operation_version: str
    parameters: FrozenDict = field(default_factory=FrozenDict)
    affected_column_ids: tuple[str, ...] = ()
    learning_scope: LearningScope = LearningScope.NONE
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", require_uuid(self.step_id, "step_id"))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))
        object.__setattr__(
            self,
            "affected_column_ids",
            tuple(require_uuid(item, "affected_column_id") for item in self.affected_column_ids),
        )
        object.__setattr__(self, "learning_scope", LearningScope(self.learning_scope))
        if self.position < 0 or not self.operation.strip() or not self.operation_version.strip():
            raise ValueError(
                "PreparationStep requires a position, operation, and operation_version"
            )


@dataclass(frozen=True, slots=True)
class PreparationRecipe:
    recipe_id: str
    project_id: str
    base_version_id: str
    ordered_steps: tuple[PreparationStep, ...]
    recipe_hash: str
    created_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("recipe_id", "project_id", "base_version_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "recipe_hash", require_sha256(self.recipe_hash, "recipe_hash"))
        require_utc(self.created_at, "created_at")
        positions = tuple(step.position for step in self.ordered_steps)
        if positions != tuple(range(len(self.ordered_steps))):
            raise ValueError(
                "PreparationRecipe steps must have contiguous ordered positions starting at zero"
            )


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_memory_bytes: int
    max_disk_bytes: int
    max_duration_seconds: int
    max_parallelism: int = 1
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            min(
                self.max_memory_bytes,
                self.max_disk_bytes,
                self.max_duration_seconds,
                self.max_parallelism,
            )
            <= 0
        ):
            raise ValueError("ResourceBudget values must be positive")


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    spec_id: str
    project_id: str
    module_id: AnalysisModuleId
    module_version: str
    operation: str
    input_version_id: str
    column_roles: tuple[ColumnRole, ...]
    parameters: FrozenDict
    seed: int
    resource_budget: ResourceBudget
    spec_hash: str
    created_at: datetime
    accepted_warnings: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("spec_id", "project_id", "input_version_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "module_id", AnalysisModuleId(self.module_id))
        object.__setattr__(self, "parameters", freeze_json(self.parameters))
        object.__setattr__(self, "spec_hash", require_sha256(self.spec_hash, "spec_hash"))
        require_utc(self.created_at, "created_at")
        if self.seed < 0 or not self.module_version.strip() or not self.operation.strip():
            raise ValueError(
                "AnalysisSpec requires non-negative seed, module_version, and operation"
            )
        column_ids = tuple(role.column_id for role in self.column_roles)
        if len(set(column_ids)) != len(column_ids):
            raise ValueError("AnalysisSpec cannot assign multiple roles to one column")
        if self.module_id is AnalysisModuleId.BINARY_CLASSIFICATION:
            targets = [role for role in self.column_roles if ColumnUsage.TARGET in role.usages]
            if self.operation == "train_validate" and len(targets) != 1:
                raise ValueError("binary train_validate requires exactly one target column")
            if self.operation != "train_validate" and len(targets) > 1:
                raise ValueError("binary operations allow at most one target column")
