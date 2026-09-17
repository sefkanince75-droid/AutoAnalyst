"""Persistence for immutable analysis specs/results and mutable run lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from ..domain.codec import canonical_json, from_json, utc_now
from ..domain.errors import DataError, SchemaError
from ..domain.plans import AnalysisSpec
from ..domain.results import AnalysisResult
from ..domain.runs import AnalysisRun, RunStatus
from .sqlite import SQLiteCatalog


class RunStore:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog

    def insert_spec(self, spec: AnalysisSpec) -> AnalysisSpec:
        with self.catalog.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM analysis_specs WHERE project_id = ? AND spec_hash = ?",
                (spec.project_id, spec.spec_hash),
            ).fetchone()
            if existing is not None:
                decoded = from_json(existing["payload_json"])
                if not isinstance(decoded, AnalysisSpec):
                    raise DataError({"reason": "analysis_spec_payload_corrupt"})
                return decoded
            connection.execute(
                """INSERT INTO analysis_specs
                   (spec_id, project_id, input_version_id, module_id, spec_hash, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    spec.spec_id,
                    spec.project_id,
                    spec.input_version_id,
                    spec.module_id.value,
                    spec.spec_hash,
                    canonical_json(spec),
                    _ts(spec.created_at),
                ),
            )
        return spec

    def get_spec(self, spec_id: str) -> AnalysisSpec:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analysis_specs WHERE spec_id = ?", (spec_id,)
            ).fetchone()
        if row is None:
            raise DataError({"reason": "analysis_spec_not_found", "spec_id": spec_id})
        value = from_json(row["payload_json"])
        if not isinstance(value, AnalysisSpec):
            raise DataError({"reason": "analysis_spec_payload_corrupt", "spec_id": spec_id})
        return value

    def insert_run(self, run: AnalysisRun) -> AnalysisRun:
        with self.catalog.transaction() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM analysis_runs WHERE project_id = ? AND request_key = ?",
                (run.project_id, run.request_key),
            ).fetchone()
            if existing is not None:
                value = from_json(existing["payload_json"])
                if not isinstance(value, AnalysisRun):
                    raise DataError({"reason": "analysis_run_payload_corrupt"})
                return value
            connection.execute(
                """INSERT INTO analysis_runs
                   (run_id, project_id, spec_id, parent_run_id, kind, request_key,
                    input_fingerprint, status, payload_json, created_at, started_at,
                    completed_at, error_code, result_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _run_values(run),
            )
        return run

    def get_run(self, run_id: str) -> AnalysisRun:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analysis_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise DataError({"reason": "analysis_run_not_found", "run_id": run_id})
        value = from_json(row["payload_json"])
        if not isinstance(value, AnalysisRun):
            raise DataError({"reason": "analysis_run_payload_corrupt", "run_id": run_id})
        return value

    def list_runs(self, project_id: str) -> tuple[AnalysisRun, ...]:
        with self.catalog.connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM analysis_runs WHERE project_id = ? ORDER BY created_at, run_id",
                (project_id,),
            ).fetchall()
        values = tuple(from_json(row["payload_json"]) for row in rows)
        if not all(isinstance(item, AnalysisRun) for item in values):
            raise DataError({"reason": "analysis_run_payload_corrupt"})
        return values  # type: ignore[return-value]

    def transition(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error_code: str | None = None,
        result_id: str | None = None,
        now: datetime | None = None,
    ) -> AnalysisRun:
        current = self.get_run(run_id)
        next_status = RunStatus(status)
        _validate_transition(current.status, next_status)
        stamp = now or utc_now()
        started_at = current.started_at
        completed_at = current.completed_at
        if next_status is RunStatus.RUNNING and started_at is None:
            started_at = stamp
        if next_status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            completed_at = stamp
        updated = replace(
            current,
            status=next_status,
            started_at=started_at,
            completed_at=completed_at,
            error_code=error_code,
            result_id=result_id,
        )
        with self.catalog.transaction() as connection:
            cursor = connection.execute(
                """UPDATE analysis_runs
                   SET status = ?, payload_json = ?, started_at = ?, completed_at = ?,
                       error_code = ?, result_id = ?
                   WHERE run_id = ? AND status = ?""",
                (
                    updated.status.value,
                    canonical_json(updated),
                    _ts(updated.started_at) if updated.started_at else None,
                    _ts(updated.completed_at) if updated.completed_at else None,
                    updated.error_code,
                    updated.result_id,
                    run_id,
                    current.status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise SchemaError({"reason": "run_transition_conflict", "run_id": run_id})
        return updated

    def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        stage: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> int:
        if not event_type.strip():
            raise SchemaError({"reason": "run_event_type_required"})
        stamp = created_at or utc_now()
        with self.catalog.transaction() as connection:
            exists = connection.execute(
                "SELECT 1 FROM analysis_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists is None:
                raise DataError({"reason": "analysis_run_not_found", "run_id": run_id})
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), -1) + 1 FROM run_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """INSERT INTO run_events
                   (run_id, sequence, event_type, stage, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    sequence,
                    event_type,
                    stage,
                    canonical_json(payload or {}),
                    _ts(stamp),
                ),
            )
        return sequence

    def list_events(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self.catalog.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return tuple(
            {
                "run_id": row["run_id"],
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "stage": row["stage"],
                "payload": from_json(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def publish_result(self, run_id: str, result: AnalysisResult, *, now: datetime | None = None) -> AnalysisRun:
        current = self.get_run(run_id)
        if current.status is not RunStatus.RUNNING:
            raise SchemaError({"reason": "run_not_running", "run_id": run_id})
        if result.run_id != run_id:
            raise SchemaError({"reason": "result_run_mismatch"})
        stamp = now or utc_now()
        completed = replace(
            current,
            status=RunStatus.COMPLETED,
            completed_at=stamp,
            result_id=result.result_id,
            error_code=None,
        )
        with self.catalog.transaction() as connection:
            connection.execute(
                """INSERT INTO analysis_results
                   (result_id, run_id, module_id, outcome, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    result.result_id,
                    run_id,
                    result.module_id.value,
                    result.outcome.value,
                    canonical_json(result),
                    _ts(stamp),
                ),
            )
            cursor = connection.execute(
                """UPDATE analysis_runs
                   SET status = ?, payload_json = ?, completed_at = ?, result_id = ?, error_code = NULL
                   WHERE run_id = ? AND status = ?""",
                (
                    RunStatus.COMPLETED.value,
                    canonical_json(completed),
                    _ts(stamp),
                    result.result_id,
                    run_id,
                    RunStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise SchemaError({"reason": "run_transition_conflict", "run_id": run_id})
        return completed

    def get_result(self, result_id: str) -> AnalysisResult:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analysis_results WHERE result_id = ?", (result_id,)
            ).fetchone()
        if row is None:
            raise DataError({"reason": "analysis_result_not_found", "result_id": result_id})
        value = from_json(row["payload_json"])
        if not isinstance(value, AnalysisResult):
            raise DataError({"reason": "analysis_result_payload_corrupt", "result_id": result_id})
        return value

    def result_for_run(self, run_id: str) -> AnalysisResult | None:
        with self.catalog.connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM analysis_results WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        value = from_json(row["payload_json"])
        if not isinstance(value, AnalysisResult):
            raise DataError({"reason": "analysis_result_payload_corrupt", "run_id": run_id})
        return value


def _validate_transition(current: RunStatus, target: RunStatus) -> None:
    allowed = {
        RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
        RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED},
        RunStatus.COMPLETED: set(),
        RunStatus.FAILED: set(),
        RunStatus.CANCELLED: set(),
    }
    if target not in allowed[current]:
        raise SchemaError(
            {"reason": "invalid_run_transition", "current": current.value, "target": target.value}
        )


def _run_values(run: AnalysisRun) -> tuple[object, ...]:
    return (
        run.run_id,
        run.project_id,
        run.spec_id,
        run.parent_run_id,
        run.kind.value,
        run.request_key,
        run.input_fingerprint,
        run.status.value,
        canonical_json(run),
        _ts(run.created_at),
        _ts(run.started_at) if run.started_at else None,
        _ts(run.completed_at) if run.completed_at else None,
        run.error_code,
        run.result_id,
    )


def _ts(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
