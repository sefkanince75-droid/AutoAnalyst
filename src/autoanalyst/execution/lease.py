"""Workspace-wide ownership record for the single heavy analysis worker."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time

import psutil

from ..domain.errors import ResourceError


LEASE_NAME = "worker-lease.json"
_PENDING_STALE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class WorkerLease:
    run_id: str
    pid: int | None
    create_time: float | None


def lease_path(workspace: str | Path) -> Path:
    return Path(workspace).resolve() / "staging" / LEASE_NAME


def reserve_worker(workspace: str | Path, run_id: str) -> WorkerLease:
    """Atomically reserve the only heavy-worker slot before spawning a process."""
    path = lease_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = read_lease(workspace)
            if existing is not None and _lease_is_active(existing):
                raise ResourceError({"reason": "heavy_worker_busy", "run_id": existing.run_id})
            if existing is not None and existing.pid is None:
                try:
                    age = max(0.0, time.time() - path.stat().st_mtime)
                except OSError:
                    age = 0.0
                if age < _PENDING_STALE_SECONDS:
                    raise ResourceError({"reason": "heavy_worker_starting", "run_id": existing.run_id})
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            payload = json.dumps({"run_id": run_id, "pid": None, "create_time": None}, sort_keys=True)
            os.write(descriptor, payload.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return WorkerLease(run_id, None, None)
    raise ResourceError({"reason": "heavy_worker_slot_unavailable"})


def activate_worker(workspace: str | Path, run_id: str, pid: int) -> WorkerLease:
    current = read_lease(workspace)
    if current is None or current.run_id != run_id or current.pid is not None:
        raise ResourceError({"reason": "worker_lease_lost", "run_id": run_id})
    process = psutil.Process(pid)
    lease = WorkerLease(run_id, pid, float(process.create_time()))
    _replace_lease(workspace, lease)
    return lease


def read_lease(workspace: str | Path) -> WorkerLease | None:
    path = lease_path(workspace)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    run_id = raw.get("run_id")
    pid = raw.get("pid")
    create_time = raw.get("create_time")
    if not isinstance(run_id, str) or not run_id:
        return None
    if pid is not None and (not isinstance(pid, int) or pid <= 0):
        return None
    if create_time is not None and not isinstance(create_time, (int, float)):
        return None
    return WorkerLease(run_id, pid, float(create_time) if create_time is not None else None)


def owned_process(workspace: str | Path, run_id: str) -> psutil.Process | None:
    lease = read_lease(workspace)
    if lease is None or lease.run_id != run_id or lease.pid is None or lease.create_time is None:
        return None
    try:
        process = psutil.Process(lease.pid)
        if abs(float(process.create_time()) - lease.create_time) > 1e-3:
            return None
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def release_worker(workspace: str | Path, run_id: str, *, pid: int | None = None) -> bool:
    path = lease_path(workspace)
    lease = read_lease(workspace)
    if lease is None or lease.run_id != run_id:
        return False
    if pid is not None and lease.pid is not None and lease.pid != pid:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def clear_stale_lease(workspace: str | Path) -> WorkerLease | None:
    lease = read_lease(workspace)
    if lease is None:
        return None
    if _lease_is_active(lease):
        return lease
    if lease.pid is None:
        path = lease_path(workspace)
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            age = 0.0
        if age < _PENDING_STALE_SECONDS:
            return lease
    release_worker(workspace, lease.run_id)
    return None


def _lease_is_active(lease: WorkerLease) -> bool:
    if lease.pid is None or lease.create_time is None:
        return False
    try:
        process = psutil.Process(lease.pid)
        return (
            abs(float(process.create_time()) - lease.create_time) <= 1e-3
            and process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _replace_lease(workspace: str | Path, lease: WorkerLease) -> None:
    path = lease_path(workspace)
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(
        {"run_id": lease.run_id, "pid": lease.pid, "create_time": lease.create_time},
        sort_keys=True,
        separators=(",", ":"),
    )
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
