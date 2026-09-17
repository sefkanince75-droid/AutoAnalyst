"""Serializable execution protocol shared by coordinator and worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..domain.codec import FrozenDict, freeze_json
from ..domain.errors import CancellationError


class EventKind(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    kind: EventKind
    stage: str
    fraction: float | None = None
    payload: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EventKind(self.kind))
        object.__setattr__(self, "payload", freeze_json(self.payload))
        if not self.stage.strip():
            raise ValueError("Progress stage is required")
        if self.fraction is not None and not 0.0 <= self.fraction <= 1.0:
            raise ValueError("Progress fraction must be between 0 and 1")


class EventCancellationToken:
    """Cancellation token backed by any Event-like object exposing is_set()."""

    def __init__(self, event: object) -> None:
        if not hasattr(event, "is_set"):
            raise TypeError("event must provide is_set()")
        self._event = event

    def is_cancelled(self) -> bool:
        return bool(self._event.is_set())

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise CancellationError({"reason": "execution_cancelled"})


class FileCancellationToken:
    """Cross-process cancellation token using a workspace-owned marker file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def is_set(self) -> bool:
        return self.path.is_file()

    def is_cancelled(self) -> bool:
        return self.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_set():
            raise CancellationError({"reason": "execution_cancelled"})


ProgressCallback = Callable[[ProgressEvent], None]
