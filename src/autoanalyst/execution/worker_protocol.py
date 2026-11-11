"""Filesystem protocol between the app-owned coordinator and disposable workers.

Workers may read immutable catalog state and write staging/object files, but they do
not publish SQLite metadata.  The app process reconciles this manifest and owns all
run/result/catalog mutations.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..analyses.contract import ResultDraft
from ..domain.codec import canonical_json, from_json, utc_now
from ..domain.results import ResultOutcome

MANIFEST_VERSION = "1"


def run_staging_dir(workspace: str | Path, run_id: str) -> Path:
    path = Path(workspace).resolve() / "staging" / f"run-{run_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def result_manifest_path(workspace: str | Path, run_id: str) -> Path:
    return run_staging_dir(workspace, run_id) / "result.json"


def progress_manifest_path(workspace: str | Path, run_id: str) -> Path:
    return run_staging_dir(workspace, run_id) / "progress.json"


def write_success_manifest(workspace: str | Path, run_id: str, draft: ResultDraft) -> None:
    _write_atomic(
        result_manifest_path(workspace, run_id),
        {
            "manifest_version": MANIFEST_VERSION,
            "run_id": run_id,
            "status": "succeeded",
            "created_at": utc_now(),
            "draft": {
                "outcome": draft.outcome.value,
                "metrics": draft.metrics,
                "findings": draft.findings,
                "tables": draft.tables,
                "charts": draft.charts,
                "artifacts": draft.artifacts,
                "methodology": draft.methodology,
                "sample_summary": draft.sample_summary,
                "provenance": draft.provenance,
            },
        },
    )


def write_failure_manifest(
    workspace: str | Path,
    run_id: str,
    *,
    error_code: str,
    context: Mapping[str, object] | None = None,
) -> None:
    _write_atomic(
        result_manifest_path(workspace, run_id),
        {
            "manifest_version": MANIFEST_VERSION,
            "run_id": run_id,
            "status": "failed",
            "created_at": utc_now(),
            "error_code": error_code,
            "context": {} if context is None else dict(context),
        },
    )


def write_cancelled_manifest(
    workspace: str | Path, run_id: str, *, context: Mapping[str, object] | None = None
) -> None:
    _write_atomic(
        result_manifest_path(workspace, run_id),
        {
            "manifest_version": MANIFEST_VERSION,
            "run_id": run_id,
            "status": "cancelled",
            "created_at": utc_now(),
            "context": {} if context is None else dict(context),
        },
    )


def write_progress_manifest(
    workspace: str | Path,
    run_id: str,
    *,
    stage: str,
    fraction: float,
    payload: Mapping[str, object] | None = None,
) -> None:
    _write_atomic(
        progress_manifest_path(workspace, run_id),
        {
            "manifest_version": MANIFEST_VERSION,
            "run_id": run_id,
            "stage": stage,
            "fraction": fraction,
            "payload": {} if payload is None else dict(payload),
            "updated_at": utc_now(),
        },
    )


def read_result_manifest(workspace: str | Path, run_id: str) -> dict[str, Any] | None:
    path = result_manifest_path(workspace, run_id)
    if not path.exists():
        return None
    try:
        decoded = from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("worker result manifest is unreadable") from exc
    if not isinstance(decoded, dict):
        raise ValueError("worker result manifest must be a mapping")
    if decoded.get("manifest_version") != MANIFEST_VERSION or decoded.get("run_id") != run_id:
        raise ValueError("worker result manifest identity mismatch")
    return decoded


def draft_from_manifest(manifest: Mapping[str, Any]) -> ResultDraft:
    if manifest.get("status") != "succeeded":
        raise ValueError("worker manifest does not contain a successful result")
    payload = manifest.get("draft")
    if not isinstance(payload, Mapping):
        raise ValueError("worker result draft is missing")
    return ResultDraft(
        outcome=ResultOutcome(str(payload["outcome"])),
        metrics=tuple(payload.get("metrics", ())),
        findings=tuple(payload.get("findings", ())),
        tables=tuple(payload.get("tables", ())),
        charts=tuple(payload.get("charts", ())),
        artifacts=tuple(payload.get("artifacts", ())),
        methodology=payload.get("methodology", {}),
        sample_summary=payload.get("sample_summary", {}),
        provenance=payload.get("provenance", {}),
    )


def _write_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    data = canonical_json(payload).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
