"""Run lifecycle coordinator independent from Streamlit and presentation."""

from __future__ import annotations

from multiprocessing import get_context
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from ..analyses.contract import AnalysisModule, ExecutionContext, ResultDraft
from ..domain.codec import fingerprint, utc_now
from ..domain.errors import AutoAnalystError, CancellationError, UnexpectedExecutionError
from ..domain.plans import AnalysisSpec
from ..domain.results import AnalysisResult, Artifact
from ..domain.runs import AnalysisRun, RunKind, RunStatus
from ..storage.runs import RunStore
from .budget import enforce_budget
from .protocol import EventCancellationToken, EventKind, ProgressCallback, ProgressEvent


class ExecutionCoordinator:
    """Owns run state transitions; modules own only deterministic computation."""

    def __init__(self, store: RunStore) -> None:
        self.store = store

    def create_run(
        self,
        spec: AnalysisSpec,
        *,
        request_key: str,
        parent_run_id: str | None = None,
        environment: dict[str, object] | None = None,
    ) -> AnalysisRun:
        persisted_spec = self.store.insert_spec(spec)
        input_fingerprint = fingerprint(
            {
                "input_version_id": persisted_spec.input_version_id,
                "spec_hash": persisted_spec.spec_hash,
                "module_version": persisted_spec.module_version,
            }
        )
        run = AnalysisRun(
            run_id=str(uuid4()),
            project_id=persisted_spec.project_id,
            kind=RunKind.ANALYSIS,
            request_key=request_key,
            input_fingerprint=input_fingerprint,
            status=RunStatus.PENDING,
            created_at=utc_now(),
            spec_id=persisted_spec.spec_id,
            parent_run_id=parent_run_id,
            environment_manifest=environment or {},
        )
        return self.store.insert_run(run)

    def execute_inline(
        self,
        run_id: str,
        module: AnalysisModule,
        *,
        cancellation_event: object,
        progress: ProgressCallback | None = None,
    ) -> AnalysisRun:
        run = self.store.get_run(run_id)
        if run.status is RunStatus.COMPLETED:
            return run
        if run.status is not RunStatus.PENDING:
            raise UnexpectedExecutionError({"reason": "run_not_pending", "run_id": run_id, "status": run.status.value})
        if run.spec_id is None:
            raise UnexpectedExecutionError({"reason": "analysis_run_missing_spec", "run_id": run_id})
        spec = self.store.get_spec(run.spec_id)
        description = module.describe()
        if description.module_id != spec.module_id or description.module_version != spec.module_version:
            raise UnexpectedExecutionError({"reason": "module_spec_mismatch", "run_id": run_id})
        issues = module.validate(spec)
        if issues:
            raise UnexpectedExecutionError({"reason": "analysis_spec_validation_failed", "codes": tuple(item.code for item in issues)})
        module.check_applicability(spec).require_runnable()
        enforce_budget(module.estimate_resources(spec), spec.resource_budget)

        running = self.store.transition(run_id, status=RunStatus.RUNNING)
        self.store.append_event(run_id, EventKind.STARTED.value, stage="run")
        token = EventCancellationToken(cancellation_event)

        def emit(event: ProgressEvent) -> None:
            token.raise_if_cancelled()
            self.store.append_event(run_id, event.kind.value, stage=event.stage, payload={"fraction": event.fraction, **dict(event.payload)})
            if progress is not None:
                progress(event)

        context = ExecutionContext(run_id=running.run_id, input_version_id=spec.input_version_id, seed=spec.seed, cancellation=token, environment=running.environment_manifest)
        try:
            emit(ProgressEvent(EventKind.PROGRESS, "analysis", 0.0))
            draft = module.run(spec, context)
            token.raise_if_cancelled()
            if draft.artifacts:
                self._register_artifacts(draft.artifacts, run_id)
            result = _result_from_draft(run_id, spec, draft)
            completed = self.store.publish_result(run_id, result)
            self.store.append_event(run_id, EventKind.COMPLETED.value, stage="run")
            if progress is not None:
                progress(ProgressEvent(EventKind.COMPLETED, "run", 1.0))
            return completed
        except CancellationError:
            self.store.transition(run_id, status=RunStatus.CANCELLED)
            self.store.append_event(run_id, EventKind.CANCELLED.value, stage="run")
            raise
        except AutoAnalystError as exc:
            self.store.transition(run_id, status=RunStatus.FAILED, error_code=exc.code.value)
            self.store.append_event(run_id, EventKind.FAILED.value, stage="run", payload={"error_code": exc.code.value})
            raise
        except Exception as exc:
            self.store.transition(run_id, status=RunStatus.FAILED, error_code="unexpected_execution")
            self.store.append_event(run_id, EventKind.FAILED.value, stage="run", payload={"error_code": "unexpected_execution", "exception_type": type(exc).__name__})
            raise UnexpectedExecutionError({"reason": "analysis_module_crashed", "exception_type": type(exc).__name__}) from exc

    def _register_artifacts(self, artifacts: tuple[Artifact, ...], run_id: str) -> None:
        with self.store.catalog.transaction() as connection:
            for artifact in artifacts:
                if artifact.owner_run_id != run_id:
                    raise UnexpectedExecutionError({"reason": "artifact_run_mismatch", "artifact_id": artifact.artifact_id})
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

    def start_worker_process(self, run_id: str, workspace: str | Path) -> subprocess.Popen[bytes]:
        """Launch the repository worker entry point; it reconstructs runtime from workspace."""
        run = self.store.get_run(run_id)
        if run.status is not RunStatus.PENDING:
            raise UnexpectedExecutionError({"reason": "run_not_pending", "run_id": run_id, "status": run.status.value})
        command = [sys.executable, "-m", "autoanalyst.execution.worker", "--workspace", str(Path(workspace)), "--run-id", run_id]
        env = os.environ.copy()
        src_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = src_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def new_cancellation_event():
    return get_context("spawn").Event()


def _result_from_draft(run_id: str, spec: AnalysisSpec, draft: ResultDraft) -> AnalysisResult:
    return AnalysisResult(
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
