"""Cross-process maintenance gate and artifact publication activity markers."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from uuid import uuid4

import psutil

from ..domain.errors import ResourceError


def safe_path(root: Path, relative: str) -> Path:
    """Reject links, junctions and paths outside this workspace before file operations."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in {"..", "."} for p in parts):
        raise ResourceError({"reason": "maintenance_unsafe_path"})
    path = root
    for part in parts:
        if ":" in part or "\\" in part:
            raise ResourceError({"reason": "maintenance_unsafe_path"})
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise ResourceError({"reason": "maintenance_unsafe_path"})
    if not path.resolve().is_relative_to(root.resolve()):
        raise ResourceError({"reason": "maintenance_unsafe_path"})
    return path


@contextmanager
def maintenance_gate(root: Path) -> Iterator[None]:
    path = safe_path(root, ".activity.guard")
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ResourceError({"reason": "maintenance_busy"}) from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def write_activity(root: Path) -> Iterator[None]:
    with maintenance_gate(root):
        from ..execution.data_control import JOB_ID
        from ..execution.data_tasks import active_data_task

        active = active_data_task(root)
        if active is not None and active.get("job_id") != JOB_ID.get():
            raise ResourceError({"reason": "heavy_worker_busy"})
        marker = safe_path(root, f"staging/write-{uuid4().hex}.json")
        with marker.open("x", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created": psutil.Process().create_time()}, stream)
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)


def require_idle_writers(root: Path) -> None:
    for marker in safe_path(root, "staging").glob("write-*.json"):
        try:
            safe_path(root, marker.relative_to(root).as_posix())
            payload = json.loads(marker.read_text(encoding="utf-8"))
            process = psutil.Process(int(payload["pid"]))
            if abs(process.create_time() - float(payload["created"])) > 0.001:
                continue
            if not process.is_running():
                continue
        except psutil.NoSuchProcess:
            continue
        except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
            raise ResourceError({"reason": "maintenance_busy"}) from exc
        raise ResourceError({"reason": "maintenance_busy"})


def artifact_activity(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with write_activity(self.catalog.paths.root):
            return method(self, *args, **kwargs)

    return wrapped
