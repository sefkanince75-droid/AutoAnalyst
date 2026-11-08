"""Closed data-task dispatcher with a self-contained sampled resource watchdog."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import threading
import time
from pathlib import Path

import psutil

from ..domain.errors import AutoAnalystError, CancellationError, ResourceError, SchemaError
from ..storage.sqlite import SQLiteCatalog
from .data_control import CHECK, JOB_ID
from .data_tasks import DataBudget, atomic_json, read_json, task_directory, workspace_bytes
from .lease import adopt_worker, owned_process, release_worker


def violation(
    budget: DataBudget, *, elapsed: float, rss: int, growth: int, free: int
) -> str | None:
    if elapsed > budget.seconds:
        return "data_task_timeout"
    if rss > budget.memory_bytes:
        return "data_task_memory"
    if growth > budget.disk_bytes:
        return "data_task_disk_budget"
    if free < budget.free_disk_bytes:
        return "data_disk_space"
    return None


def run(workspace: Path, job_id: str) -> int:
    root = workspace.resolve()
    directory = task_directory(root, job_id)
    request = read_json(directory / "request.json")
    if not request or request["job_id"] != job_id:
        return 2
    # Parent claims the lease after Popen; do not touch catalog artifacts before that.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        owner = owned_process(root, job_id)
        if owner and (
            owner.pid == os.getpid() or owner.pid in {p.pid for p in psutil.Process().parents()}
        ):
            break
        time.sleep(0.02)
    else:
        return 2
    adopt_worker(root, job_id, os.getpid())
    atomic_json(
        directory / "worker.json", {"pid": os.getpid(), "created": psutil.Process().create_time()}
    )
    budget = DataBudget(**request["budget"])
    stop = threading.Event()
    peak = 0
    last_growth = 0
    started = time.monotonic()

    def check(publication=False):
        if (directory / "cancel.flag").exists():
            raise CancellationError({"reason": "data_task_cancelled"})
        growth = (
            max(0, workspace_bytes(root) - request["baseline_bytes"])
            if publication
            else last_growth
        )
        reason = violation(
            budget,
            elapsed=time.monotonic() - started,
            rss=psutil.Process().memory_info().rss,
            growth=growth,
            free=shutil.disk_usage(root).free,
        )
        if reason:
            raise ResourceError({"reason": reason})

    def monitor():
        nonlocal peak, last_growth
        process = psutil.Process()
        while not stop.is_set():
            try:
                peak = max(peak, process.memory_info().rss)
                elapsed = time.monotonic() - started
                growth = max(0, workspace_bytes(root) - request["baseline_bytes"])
                last_growth = growth
                telemetry = {
                    "elapsed_seconds": elapsed,
                    "peak_rss_bytes": peak,
                    "disk_growth_bytes": growth,
                }
                try:
                    atomic_json(directory / "metrics.json", telemetry)
                except OSError:
                    pass  # A concurrent Windows reader must not kill the worker.
                reason = violation(
                    budget,
                    elapsed=elapsed,
                    rss=peak,
                    growth=growth,
                    free=shutil.disk_usage(root).free,
                )
                if (directory / "cancel.flag").exists():
                    reason = "data_task_cancelled"
                if reason:
                    atomic_json(
                        directory / "outcome.json",
                        {
                            "status": "cancelled" if reason == "data_task_cancelled" else "failed",
                            "reason": reason,
                        },
                    )
                    os._exit(4 if reason == "data_task_cancelled" else 3)
            except OSError:
                os._exit(3)  # A failed monitor must never leave an unbounded worker.
            if stop.wait(0.1):
                return

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    token = JOB_ID.set(job_id)
    check_token = CHECK.set(check)
    try:
        check()
        from ..application.preparation import PreparationService
        from ..data import CSVIngestor, XLSXIngestor
        from ..storage.artifacts import ArtifactStore

        catalog = SQLiteCatalog(root)
        store = ArtifactStore(root)
        dataset = catalog.get_dataset(request["dataset_id"])
        if (
            dataset.project_id != request["project_id"]
            or dataset.head_revision != request["expected_head_revision"]
        ):
            raise SchemaError({"reason": "data_task_stale"})
        if request["operation"] == "preparation":
            PreparationService(catalog, store).preview_recipe(request["recipe_id"])
        else:
            source = directory / "source.bin"
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != request["sha256"]:
                raise SchemaError({"reason": "data_task_source"})
            options = request["options"]
            allowed = (
                {"delimiter", "encoding", "original_name"}
                if request["operation"] == "csv"
                else {"sheet_name", "original_name"}
            )
            if not isinstance(options, dict) or set(options) - allowed:
                raise SchemaError({"reason": "data_task_operation"})
            if request["operation"] == "csv":
                CSVIngestor(catalog, store).import_csv(
                    project_id=dataset.project_id,
                    dataset_id=dataset.dataset_id,
                    source=source,
                    **options,
                )
            elif request["operation"] == "xlsx":
                XLSXIngestor(catalog, store).import_xlsx(
                    project_id=dataset.project_id,
                    dataset_id=dataset.dataset_id,
                    source=source,
                    **options,
                )
            else:
                raise SchemaError({"reason": "data_task_operation"})
        atomic_json(directory / "outcome.json", {"status": "completed"})
        return 0
    except AutoAnalystError as exc:
        atomic_json(
            directory / "outcome.json",
            {
                "status": "cancelled" if isinstance(exc, CancellationError) else "failed",
                "reason": exc.context.get("reason", "data_task_failed"),
                "error_code": exc.code.value,
            },
        )
        return 2
    except Exception:
        atomic_json(directory / "outcome.json", {"status": "failed", "reason": "data_task_failed"})
        return 2
    finally:
        stop.set()
        thread.join(timeout=2)
        try:
            peak = max(peak, psutil.Process().memory_info().rss)
            atomic_json(
                directory / "metrics.json",
                {
                    "elapsed_seconds": time.monotonic() - started,
                    "peak_rss_bytes": peak,
                    "disk_growth_bytes": max(0, workspace_bytes(root) - request["baseline_bytes"]),
                },
            )
        except OSError:
            pass
        CHECK.reset(check_token)
        JOB_ID.reset(token)
        release_worker(root, job_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    return run(args.workspace, args.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
