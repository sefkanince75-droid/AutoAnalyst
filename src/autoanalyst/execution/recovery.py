"""App-owned reconciliation of disposable worker manifests into SQLite state."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from ..domain.codec import canonical_json, utc_now
from ..domain.plans import AnalysisModuleId
from ..domain.results import AnalysisResult
from ..domain.runs import RunStatus
from ..storage.artifacts import ArtifactStore
from ..storage.runs import RunStore
from .lease import clear_stale_lease, owned_process, release_worker
from .protocol import EventKind
from .worker_protocol import draft_from_manifest, read_result_manifest


def reconcile_worker_run(store: RunStore, workspace: str | Path, run_id: str) -> bool:
    """Publish one completed worker manifest, or fail a disappeared worker."""
    workspace_path = Path(workspace).resolve()
    current = store.get_run(run_id)
    if current.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        return False

    try:
        manifest = read_result_manifest(workspace_path, run_id)
    except ValueError:
        _fail_run(store, run_id, "unexpected_execution", "worker_manifest_corrupt")
        release_worker(workspace_path, run_id)
        return True

    if manifest is not None:
        status = str(manifest.get("status", ""))
        if status == "succeeded":
            _publish_success(store, workspace_path, run_id, manifest)
        elif status == "cancelled":
            if current.status in {RunStatus.PENDING, RunStatus.RUNNING}:
                store.transition(run_id, status=RunStatus.CANCELLED)
                store.append_event(run_id, EventKind.CANCELLED.value, stage="worker")
        elif status == "failed":
            error_code = str(manifest.get("error_code") or "unexpected_execution")
            reason = "worker_reported_failure"
            context = manifest.get("context")
            if isinstance(context, dict) and context.get("reason"):
                reason = str(context["reason"])
            _fail_run(store, run_id, error_code, reason)
        else:
            _fail_run(store, run_id, "unexpected_execution", "worker_manifest_status_invalid")
        release_worker(workspace_path, run_id)
        return True

    if owned_process(workspace_path, run_id) is not None:
        return False
    _fail_run(store, run_id, "unexpected_execution", "worker_interrupted")
    release_worker(workspace_path, run_id)
    return True


def reconcile_interrupted_runs(store: RunStore, workspace: str | Path) -> tuple[str, ...]:
    """Reconcile finished manifests and repair orphaned RUNNING rows at startup."""
    workspace_path = Path(workspace).resolve()
    clear_stale_lease(workspace_path)
    with store.catalog.connection() as connection:
        rows = connection.execute(
            "SELECT run_id FROM analysis_runs WHERE status = ? ORDER BY created_at, run_id",
            (RunStatus.RUNNING.value,),
        ).fetchall()
    recovered: list[str] = []
    for row in rows:
        run_id = str(row["run_id"])
        if reconcile_worker_run(store, workspace_path, run_id):
            recovered.append(run_id)
    return tuple(recovered)


def _publish_success(store: RunStore, workspace: Path, run_id: str, manifest) -> None:
    current = store.get_run(run_id)
    if current.status is not RunStatus.RUNNING or current.spec_id is None:
        return
    draft = draft_from_manifest(manifest)
    artifacts = draft.artifacts
    artifact_store = ArtifactStore(workspace)
    for artifact in artifacts:
        if artifact.owner_run_id != run_id or not artifact_store.verify(artifact):
            _fail_run(store, run_id, "data_error", "worker_artifact_verification_failed")
            return

    spec = store.get_spec(current.spec_id)
    result = AnalysisResult(
        result_id=str(uuid4()),
        run_id=run_id,
        module_id=spec.module_id,
        outcome=draft.outcome,
        metrics=draft.metrics,
        findings=draft.findings,
        tables=draft.tables,
        charts=draft.charts,
        methodology=draft.methodology,
        sample_summary=draft.sample_summary,
        provenance=draft.provenance,
    )
    stamp = utc_now()
    completed = replace(
        current,
        status=RunStatus.COMPLETED,
        completed_at=stamp,
        result_id=result.result_id,
        error_code=None,
    )
    holdout_training_run_id = None
    if (
        spec.module_id is AnalysisModuleId.BINARY_CLASSIFICATION
        and spec.operation == "final_evaluate"
    ):
        holdout_training_run_id = str(spec.parameters["training_run_id"])

    with store.catalog.transaction() as connection:
        for artifact in artifacts:
            connection.execute(
                """INSERT INTO artifacts
                   (artifact_id, project_id, owner_run_id, kind, relative_path, media_type,
                    byte_size, sha256, format_version, created_at, schema_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact.artifact_id,
                    artifact.project_id,
                    artifact.owner_run_id,
                    artifact.kind,
                    artifact.relative_path,
                    artifact.media_type,
                    artifact.byte_size,
                    artifact.sha256,
                    artifact.format_version,
                    artifact.created_at.isoformat().replace("+00:00", "Z"),
                    artifact.schema_version,
                ),
            )
        if holdout_training_run_id is not None:
            lock = connection.execute(
                "SELECT * FROM holdout_locks WHERE training_run_id = ?",
                (holdout_training_run_id,),
            ).fetchone()
            if (
                lock is None
                or lock["final_run_id"] != run_id
                or lock["status"] != "access_started"
            ):
                raise RuntimeError("final holdout lock is not publishable")
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
                stamp.isoformat().replace("+00:00", "Z"),
            ),
        )
        cursor = connection.execute(
            """UPDATE analysis_runs
               SET status = ?, payload_json = ?, completed_at = ?, result_id = ?, error_code = NULL
               WHERE run_id = ? AND status = ?""",
            (
                RunStatus.COMPLETED.value,
                canonical_json(completed),
                stamp.isoformat().replace("+00:00", "Z"),
                result.result_id,
                run_id,
                RunStatus.RUNNING.value,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("worker result publication conflict")
        if holdout_training_run_id is not None:
            cursor = connection.execute(
                """UPDATE holdout_locks SET status = 'reported'
                   WHERE training_run_id = ? AND final_run_id = ? AND status = 'access_started'""",
                (holdout_training_run_id, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("holdout publication conflict")
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
                EventKind.COMPLETED.value,
                "worker",
                canonical_json({}),
                stamp.isoformat().replace("+00:00", "Z"),
            ),
        )


def _fail_run(store: RunStore, run_id: str, error_code: str, reason: str) -> None:
    current = store.get_run(run_id)
    if current.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
        return
    store.transition(run_id, status=RunStatus.FAILED, error_code=error_code)
    store.append_event(
        run_id,
        EventKind.FAILED.value,
        stage="recovery",
        payload={"error_code": error_code, "reason": reason},
    )
