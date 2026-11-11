"""Bounded, read-only worker progress snapshots; private payloads are discarded."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..domain.codec import from_json, require_utc, require_uuid
from .worker_protocol import MANIFEST_VERSION


@dataclass(frozen=True)
class WorkerProgress:
    stage: str
    fraction: float
    updated_at: datetime
    model_id: str | None = None


def read_worker_progress(workspace: str | Path, run_id: str) -> WorkerProgress | None:
    run_id = require_uuid(run_id, "run_id")
    path = Path(workspace) / "staging" / f"run-{run_id}" / "progress.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        value = from_json(raw.decode("utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("run_id") != run_id
            or value.get("manifest_version") != MANIFEST_VERSION
        ):
            return None
        stage, fraction, stamp = value.get("stage"), value.get("fraction"), value.get("updated_at")
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 80:
            return None
        if (
            isinstance(fraction, bool)
            or not isinstance(fraction, (float, int))
            or not 0 <= fraction <= 1
        ):
            return None
        if not isinstance(stamp, datetime):
            return None
        require_utc(stamp, "updated_at")
        payload = value.get("payload")
        model = payload.get("model_id") if isinstance(payload, dict) else None
        if model not in {"dummy", "logistic_regression", "random_forest"}:
            model = None
        return WorkerProgress(stage, float(fraction), stamp, model)
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
