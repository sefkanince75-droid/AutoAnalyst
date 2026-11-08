"""Persisted, closed-operation data tasks in a single disposable process.

Requests and telemetry live in staging; committed imports/previews live in SQLite.
On interruption, the catalog wins over cancellation or an incomplete outcome file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import psutil

from ..domain.codec import require_uuid
from ..domain.errors import ResourceError, SchemaError
from ..storage.activity import maintenance_gate, require_idle_writers, safe_path
from ..storage.sqlite import SQLiteCatalog
from .lease import activate_worker, read_lease, release_worker, reserve_worker

MIB = 1024**2


@dataclass(frozen=True, slots=True)
class DataBudget:
    memory_bytes: int = 1536 * MIB
    seconds: float = 120
    disk_bytes: int = 1024 * MIB
    free_disk_bytes: int = 128 * MIB

    def __post_init__(self):
        if (
            any(
                type(v) is not int or v < 1
                for v in (self.memory_bytes, self.disk_bytes, self.free_disk_bytes)
            )
            or not 0 < self.seconds <= 3600
        ):
            raise ValueError("Invalid data budget")


DEFAULT_DATA_BUDGET = DataBudget()


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.005)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    for attempt in range(20):
        try:
            if path.is_symlink() or path.stat().st_size > MIB:
                raise ValueError("Invalid task file")
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)


def task_directory(root: Path, job_id: str) -> Path:
    identifier = require_uuid(job_id, "job_id")
    return safe_path(root, f"staging/data-tasks/{identifier}")


def active_data_task(root: Path):
    lease = read_lease(root)
    if lease is None:
        return None
    try:
        request = read_json(task_directory(root, lease.run_id) / "request.json")
        return request if isinstance(request, dict) else None
    except (ValueError, OSError, ResourceError):
        # Uncertainty must not make a registered data task appear idle.
        return {"job_id": lease.run_id, "operation": "unknown"}


def workspace_bytes(root: Path) -> int:
    size = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        directories[:] = [
            name
            for name in directories
            if not (parent / name).is_symlink() and not (parent / name).is_junction()
        ]
        for name in files:
            path = parent / name
            if path.is_symlink():
                continue
            try:
                size += path.stat().st_size
            except FileNotFoundError:
                pass
    return size


class DataTaskService:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog
        self.root = catalog.paths.root

    def start(
        self,
        *,
        operation: str,
        project_id: str,
        dataset_id: str,
        source: bytes | None = None,
        options: dict | None = None,
        recipe_id: str | None = None,
        budget: DataBudget = DEFAULT_DATA_BUDGET,
    ) -> str:
        from ..data.limits import enforce_free_disk, enforce_source_size

        if operation not in {"csv", "xlsx", "preparation"}:
            raise SchemaError({"reason": "data_task_operation"})
        dataset = self.catalog.get_dataset(dataset_id)
        if dataset.project_id != project_id or self.catalog.get_project(project_id).archived_at:
            raise SchemaError({"reason": "dataset_project_mismatch"})
        if operation == "preparation":
            if recipe_id is None:
                raise SchemaError({"reason": "preparation_preview_context_mismatch"})
            recipe = self.catalog.get_preparation_recipe(recipe_id)
            if (
                recipe.project_id != project_id
                or self.catalog.get_version(recipe.base_version_id).dataset_id != dataset_id
                or recipe.base_version_id != dataset.head_version_id
            ):
                raise SchemaError({"reason": "preparation_preview_context_mismatch"})
            needed = 128 * MIB
        else:
            if not isinstance(source, bytes):
                raise SchemaError({"reason": "data_task_source"})
            enforce_source_size(len(source), source_format=operation)
            needed = len(source) * 3 + 64 * MIB
        if needed > budget.disk_bytes:
            raise ResourceError({"reason": "data_task_disk_budget"})
        enforce_free_disk(self.root, needed + budget.free_disk_bytes)
        job_id = str(uuid4())
        directory = task_directory(self.root, job_id)
        with maintenance_gate(self.root):
            require_idle_writers(self.root)
            with self.catalog.transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM analysis_runs WHERE status IN ('pending','running') LIMIT 1"
                ).fetchone():
                    raise ResourceError({"reason": "heavy_worker_busy"})
                reserve_worker(self.root, job_id)
            process = None
            try:
                baseline = workspace_bytes(self.root)
                directory.mkdir(parents=True, exist_ok=False)
                request = {
                    "job_id": job_id,
                    "operation": operation,
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "recipe_id": recipe_id,
                    "options": options or {},
                    "created": time.time(),
                    "expected_head_revision": dataset.head_revision,
                    "budget": asdict(budget),
                    "baseline_bytes": baseline,
                    "sha256": hashlib.sha256(source).hexdigest() if source is not None else None,
                }
                atomic_json(directory / "request.json", request)
                if source is not None:
                    with (directory / "source.bin").open("xb") as stream:
                        stream.write(source)
                        stream.flush()
                        os.fsync(stream.fileno())
                atomic_json(safe_path(self.root, "staging/last-data-task.json"), {"job_id": job_id})
                command = [
                    sys.executable,
                    "-m",
                    "autoanalyst.execution.data_worker",
                    "--workspace",
                    str(self.root),
                    "--job-id",
                    job_id,
                ]
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                identity = activate_worker(self.root, job_id, process.pid)
                atomic_json(
                    directory / "identity.json",
                    {"pid": identity.pid, "created": identity.create_time},
                )
            except Exception:
                if process is not None:
                    process.terminate()
                    process.wait(timeout=10)
                release_worker(self.root, job_id)
                atomic_json(
                    directory / "outcome.json",
                    {"status": "failed", "reason": "data_task_start_failed"},
                ) if directory.exists() else None
                raise
        return job_id

    def latest(self) -> str | None:
        pointer = read_json(safe_path(self.root, "staging/last-data-task.json"))
        return require_uuid(pointer["job_id"], "job_id") if pointer else None

    def cancel(self, job_id: str) -> None:
        directory = task_directory(self.root, job_id)
        if read_json(directory / "request.json") is None:
            raise SchemaError({"reason": "data_task_missing"})
        (directory / "cancel.flag").touch(exist_ok=True)

    def poll(self, job_id: str) -> dict:
        directory = task_directory(self.root, job_id)
        request = read_json(directory / "request.json")
        if not request:
            raise SchemaError({"reason": "data_task_missing"})
        telemetry = read_json(directory / "metrics.json", {})
        identity = read_json(directory / "worker.json") or read_json(directory / "identity.json")
        alive = False
        if identity:
            try:
                process = psutil.Process(identity["pid"])
                alive = (
                    abs(process.create_time() - identity["created"]) < 0.001
                    and process.is_running()
                    and process.status() != psutil.STATUS_ZOMBIE
                )
            except psutil.NoSuchProcess:
                pass
        elif time.time() - request["created"] < 30 and not (directory / "outcome.json").exists():
            alive = True
        if alive:
            return {**request, **telemetry, "status": "running"}
        # SQLite commit is authoritative, even if a cancellation/kill won the race
        # against the worker's final outcome write.
        with self.catalog.connection() as connection:
            if request["operation"] == "preparation":
                row = connection.execute(
                    "SELECT preview_id FROM preparation_previews WHERE preview_id=?", (job_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT version_id FROM dataset_versions WHERE created_by_run_id=? AND dataset_id=?",
                    (job_id, request["dataset_id"]),
                ).fetchone()
        if row:
            outcome = {"status": "completed", "result_id": row[0]}
        else:
            outcome = read_json(directory / "outcome.json") or {
                "status": "cancelled" if (directory / "cancel.flag").exists() else "failed",
                "reason": "data_task_interrupted",
            }
            if outcome.get("status") == "completed":
                outcome = {"status": "failed", "reason": "data_task_interrupted"}
        release_worker(self.root, job_id)
        # The process is confirmed stopped. Release only this request's private
        # input and explicitly recorded spill directory; never registered objects.
        (directory / "source.bin").unlink(missing_ok=True)
        spill_record = read_json(directory / "spill.json")
        if spill_record:
            name = spill_record.get("name", "")
            if (
                not isinstance(name, str)
                or "/" in name
                or "\\" in name
                or not name.startswith(f"data-spill-{job_id}-")
            ):
                raise ResourceError({"reason": "maintenance_unsafe_path"})
            spill = safe_path(self.root, f"staging/{name}")
            if spill.exists():
                if (
                    spill.resolve().parent != self.catalog.paths.staging.resolve()
                    or spill.is_symlink()
                    or spill.is_junction()
                ):
                    raise ResourceError({"reason": "maintenance_unsafe_path"})
                shutil.rmtree(spill)
        return {**request, **telemetry, **outcome}
