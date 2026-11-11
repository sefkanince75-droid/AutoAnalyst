"""Analysis-run and immutable split-manifest models."""

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


class RunKind(str, Enum):
    ANALYSIS = "analysis"
    PREPARATION = "preparation"
    REPORT = "report"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    run_id: str
    project_id: str
    kind: RunKind
    request_key: str
    input_fingerprint: str
    status: RunStatus
    created_at: datetime
    spec_id: str | None = None
    parent_run_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    environment_manifest: FrozenDict = field(default_factory=FrozenDict)
    error_code: str | None = None
    result_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("run_id", "project_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        for field_name in ("spec_id", "parent_run_id", "result_id"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, require_uuid(value, field_name))
        object.__setattr__(self, "kind", RunKind(self.kind))
        object.__setattr__(self, "status", RunStatus(self.status))
        object.__setattr__(
            self, "input_fingerprint", require_sha256(self.input_fingerprint, "input_fingerprint")
        )
        object.__setattr__(self, "environment_manifest", freeze_json(self.environment_manifest))
        for field_name in ("created_at", "started_at", "completed_at"):
            value = getattr(self, field_name)
            if value is not None:
                require_utc(value, field_name)
        if not self.request_key:
            raise ValueError("request_key is required")
        if self.status is RunStatus.COMPLETED and self.result_id is None:
            raise ValueError("Completed analysis runs require result_id")
        if self.status is RunStatus.FAILED and not self.error_code:
            raise ValueError("Failed analysis runs require error_code")


@dataclass(frozen=True, slots=True)
class SplitManifest:
    split_id: str
    input_version_id: str
    strategy: str
    parameters: FrozenDict
    seed: int
    membership_artifact_id: str
    counts: FrozenDict
    class_counts: FrozenDict
    membership_hash: str
    created_at: datetime
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("split_id", "input_version_id", "membership_artifact_id"):
            object.__setattr__(
                self, field_name, require_uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "parameters", freeze_json(self.parameters))
        object.__setattr__(self, "counts", freeze_json(self.counts))
        object.__setattr__(self, "class_counts", freeze_json(self.class_counts))
        object.__setattr__(
            self, "membership_hash", require_sha256(self.membership_hash, "membership_hash")
        )
        require_utc(self.created_at, "created_at")
        if not self.strategy.strip() or self.seed < 0:
            raise ValueError("SplitManifest requires strategy and non-negative seed")
