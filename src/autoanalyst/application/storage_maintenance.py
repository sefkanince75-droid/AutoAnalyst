"""Explicit inventory and reversible quarantine of old unregistered generated files.

All catalog artifacts are protected, including expired previews and archived projects.
No metadata or user files are deleted. Quarantine stays on the same volume and can be
restored after a crash using its write-ahead manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from ..domain.errors import ResourceError
from ..storage.activity import maintenance_gate, require_idle_writers, safe_path
from ..storage.sqlite import SQLiteCatalog

MIN_AGE_SECONDS = 24 * 60 * 60
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_GENERATED = re.compile(
    rf"(?:staging/{_UUID}\.[0-9a-f]{{32}}\.stage|"
    rf"projects/({_UUID})/objects/([0-9a-f]{{2}})/({_UUID})\.(?:bin|parquet))\Z"
)


def _generated(relative: str) -> bool:
    match = _GENERATED.fullmatch(relative)
    return bool(match and (match[3] is None or match[2] == match[3][:2]))


@dataclass(frozen=True)
class MaintenanceFile:
    path: str
    size: int
    modified_ns: int
    digest: str
    inode: int


@dataclass(frozen=True)
class StorageReview:
    root: str
    scanned_at: float
    usage: dict[str, int]
    candidates: tuple[MaintenanceFile, ...]
    skipped_links: int


def _record(path: Path, relative: str) -> MaintenanceFile:
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ResourceError({"reason": "maintenance_changed"})
    return MaintenanceFile(relative, after.st_size, after.st_mtime_ns, digest, after.st_ino)


class StorageMaintenanceService:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog
        self.root = catalog.paths.root

    def scan(self) -> StorageReview:
        with self.catalog.connection() as connection:
            references = {
                r[0].casefold() for r in connection.execute("SELECT relative_path FROM artifacts")
            }
        usage = dict.fromkeys(
            ("catalog", "registered", "unregistered", "staging", "logs", "quarantine", "other"), 0
        )
        candidates = []
        skipped = 0
        now = time.time()
        for directory, directories, files in os.walk(self.root, followlinks=False):
            parent = Path(directory)
            for name in list(directories):
                path = parent / name
                if path.is_symlink() or path.is_junction():
                    directories.remove(name)
                    skipped += 1
            for name in files:
                path = parent / name
                if path.is_symlink() or path.is_junction():
                    skipped += 1
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    safe_path(self.root, relative)
                    stat = path.stat()
                    if relative.casefold() in references:
                        category = "registered"
                    elif relative.startswith("staging/maintenance-trash/"):
                        category = "quarantine"
                    elif relative.startswith("staging/"):
                        category = "staging"
                    elif relative.startswith("logs/"):
                        category = "logs"
                    elif relative in {"catalog.sqlite", "catalog.sqlite-wal", "catalog.sqlite-shm"}:
                        category = "catalog"
                    elif _generated(relative):
                        category = "unregistered"
                    else:
                        category = "other"
                    usage[category] += stat.st_size
                    if (
                        relative.casefold() not in references
                        and _generated(relative)
                        and now - stat.st_mtime >= MIN_AGE_SECONDS
                    ):
                        candidates.append(_record(path, relative))
                except FileNotFoundError:
                    continue  # An active writer may finish staging during this read-only scan.
        return StorageReview(
            str(self.root), now, usage, tuple(sorted(candidates, key=lambda c: c.path)), skipped
        )

    def _idle(self, connection) -> None:
        require_idle_writers(self.root)
        if connection.execute(
            "SELECT 1 FROM analysis_runs WHERE status IN ('pending', 'running') LIMIT 1"
        ).fetchone():
            raise ResourceError({"reason": "maintenance_busy"})
        # A lease can outlive its run status; uncertainty must never authorize a move.
        if safe_path(self.root, "staging/worker-lease.json").exists():
            raise ResourceError({"reason": "maintenance_busy"})

    def quarantine(self, review: StorageReview, selected: tuple[str, ...]) -> str:
        if (
            review.root != str(self.root)
            or not selected
            or len(selected) > 1000
            or len(set(selected)) != len(selected)
        ):
            raise ResourceError({"reason": "maintenance_changed"})
        records = {item.path: item for item in review.candidates}
        with maintenance_gate(self.root), self.catalog.transaction() as connection:
            self._idle(connection)
            references = {
                r[0].casefold() for r in connection.execute("SELECT relative_path FROM artifacts")
            }
            items = []
            for relative in selected:
                path = safe_path(self.root, relative)
                if (
                    relative not in records
                    or relative.casefold() in references
                    or not _generated(relative)
                ):
                    raise ResourceError({"reason": "maintenance_changed"})
                if (
                    not path.is_file()
                    or time.time() - path.stat().st_mtime < MIN_AGE_SECONDS
                    or _record(path, relative) != records[relative]
                ):
                    raise ResourceError({"reason": "maintenance_changed"})
                items.append(records[relative])
            batch = str(uuid4())
            destination = safe_path(self.root, f"staging/maintenance-trash/{batch}")
            destination.mkdir(parents=True, exist_ok=False)
            with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
                json.dump({"root": str(self.root), "files": [asdict(i) for i in items]}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            for item in items:
                source = safe_path(self.root, item.path)
                target = safe_path(self.root, f"staging/maintenance-trash/{batch}/{item.path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, target)
            return batch

    def batches(self) -> tuple[str, ...]:
        directory = safe_path(self.root, "staging/maintenance-trash")
        if not directory.exists():
            return ()
        return tuple(
            sorted(
                p.name
                for p in directory.iterdir()
                if _is_uuid(p.name)
                and not p.is_symlink()
                and not p.is_junction()
                and p.is_dir()
                and (p / "manifest.json").is_file()
            )
        )

    def restore(self, batch: str) -> int:
        if not _is_uuid(batch):
            raise ResourceError({"reason": "maintenance_unsafe_path"})
        with maintenance_gate(self.root), self.catalog.transaction() as connection:
            self._idle(connection)
            directory = f"staging/maintenance-trash/{batch}"
            manifest = safe_path(self.root, f"{directory}/manifest.json")
            if manifest.stat().st_size > 8 * 1024**2:
                raise ResourceError({"reason": "maintenance_changed"})
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if payload["root"] != str(self.root):
                raise ResourceError({"reason": "maintenance_changed"})
            moves: list[tuple[Path, Path | None]] = []
            seen = set()
            for raw in payload["files"]:
                item = MaintenanceFile(**raw)
                if not _generated(item.path) or item.path in seen:
                    raise ResourceError({"reason": "maintenance_unsafe_path"})
                seen.add(item.path)
                source = safe_path(self.root, f"{directory}/{item.path}")
                target = safe_path(self.root, item.path)
                if not source.exists():
                    continue  # Already restored or crash before the original move.
                actual = _record(source, item.path)
                if actual != item:
                    raise ResourceError({"reason": "maintenance_changed"})
                if target.exists():
                    if os.path.samefile(source, target):
                        moves.append((source, None))
                        continue
                    raise ResourceError({"reason": "maintenance_changed"})
                moves.append((source, target))
            for source, restore_target in moves:
                if restore_target is None:
                    source.unlink()
                    continue
                restore_target.parent.mkdir(parents=True, exist_ok=True)
                # Hard-link publication is exclusive on Windows and POSIX. Both locations
                # remain recoverable if interrupted between publication and unlink.
                os.link(source, restore_target)
                source.unlink()
            return len(moves)


def _is_uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False
