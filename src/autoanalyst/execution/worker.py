"""Disposable heavy-analysis worker with no SQLite write authority."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from ..analyses.contract import ExecutionContext
from ..bootstrap import build_registry
from ..domain.errors import AutoAnalystError, CancellationError
from ..domain.runs import RunStatus
from ..storage.artifacts import ArtifactStore
from ..storage.read_only import ReadOnlySQLiteCatalog
from ..storage.runs import RunStore
from .budget import enforce_budget
from .lease import release_worker
from .protocol import FileCancellationToken
from .watchdog import WorkerBudgetWatchdog
from .worker_protocol import (
    write_cancelled_manifest,
    write_failure_manifest,
    write_progress_manifest,
    write_success_manifest,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoanalyst-worker")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    return parser


def _cancel_path(workspace: Path, run_id: str) -> Path:
    return workspace / "staging" / f"cancel-{run_id}.flag"


def run_worker(workspace: str | Path, run_id: str) -> int:
    """Execute one immutable analysis request and publish only a file manifest."""
    workspace_path = Path(workspace).resolve()
    catalog = ReadOnlySQLiteCatalog(workspace_path)
    artifact_store = ArtifactStore(workspace_path)
    run_store = RunStore(catalog)
    run = run_store.get_run(run_id)
    if run.status is not RunStatus.RUNNING or run.spec_id is None:
        write_failure_manifest(
            workspace_path,
            run_id,
            error_code="unexpected_execution",
            context={"reason": "worker_run_not_running", "status": run.status.value},
        )
        return 2
    spec = run_store.get_spec(run.spec_id)
    registry = build_registry(catalog, artifact_store)
    module = registry.get(spec.module_id)
    description = module.describe()
    if description.module_version != spec.module_version:
        write_failure_manifest(
            workspace_path,
            run_id,
            error_code="unexpected_execution",
            context={"reason": "module_spec_mismatch"},
        )
        return 2

    cancellation = FileCancellationToken(_cancel_path(workspace_path, run_id))
    estimate = module.estimate_resources(spec)
    try:
        issues = module.validate(spec)
        if issues:
            write_failure_manifest(
                workspace_path,
                run_id,
                error_code="schema_error",
                context={"reason": "analysis_spec_validation_failed", "codes": tuple(i.code for i in issues)},
            )
            return 2
        module.check_applicability(spec).require_runnable()
        enforce_budget(estimate, spec.resource_budget)
    except AutoAnalystError as exc:
        write_failure_manifest(
            workspace_path,
            run_id,
            error_code=exc.code.value,
            context=dict(exc.context),
        )
        return 2

    last_progress = {"when": 0.0, "stage": ""}

    def progress(stage: str, fraction: float, payload) -> None:
        now = time.monotonic()
        if stage != last_progress["stage"] or fraction >= 1.0 or now - float(last_progress["when"]) >= 0.2:
            write_progress_manifest(
                workspace_path,
                run_id,
                stage=stage,
                fraction=fraction,
                payload=dict(payload),
            )
            last_progress["when"] = now
            last_progress["stage"] = stage

    watchdog = WorkerBudgetWatchdog(
        workspace=workspace_path,
        run_id=run_id,
        max_duration_seconds=spec.resource_budget.max_duration_seconds,
        max_memory_bytes=spec.resource_budget.max_memory_bytes,
    )
    context = ExecutionContext(
        run_id=run_id,
        input_version_id=spec.input_version_id,
        seed=spec.seed,
        cancellation=cancellation,
        environment=run.environment_manifest,
        resource_budget=spec.resource_budget,
        methodology_version=spec.module_version,
        deadline_monotonic=time.monotonic() + spec.resource_budget.max_duration_seconds,
        progress=progress,
    )
    watchdog.start()
    try:
        context.emit_progress("analysis", 0.0)
        draft = module.run(spec, context)
        context.raise_if_cancelled()
        write_success_manifest(workspace_path, run_id, draft)
        return 0
    except CancellationError as exc:
        write_cancelled_manifest(workspace_path, run_id, context=dict(exc.context))
        return 4
    except AutoAnalystError as exc:
        write_failure_manifest(
            workspace_path,
            run_id,
            error_code=exc.code.value,
            context=dict(exc.context),
        )
        return 2
    except Exception as exc:
        write_failure_manifest(
            workspace_path,
            run_id,
            error_code="unexpected_execution",
            context={"reason": "analysis_module_crashed", "exception_type": type(exc).__name__},
        )
        return 2
    finally:
        watchdog.stop()
        release_worker(workspace_path, run_id)


def main() -> int:
    args = _parser().parse_args()
    return run_worker(args.workspace, args.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
