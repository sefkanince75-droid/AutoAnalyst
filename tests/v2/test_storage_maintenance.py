from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from uuid import uuid4

import psutil
import pytest

from autoanalyst.application.storage_maintenance import MIN_AGE_SECONDS, StorageMaintenanceService
from autoanalyst.domain.errors import ResourceError
from autoanalyst.storage.activity import (
    maintenance_gate,
    require_idle_writers,
    safe_path,
    write_activity,
)


def orphan(ws, *, staged=False, old=True):
    staged_file = ws.store.stage_bytes(
        b"orphan",
        project_id=ws.project.project_id,
        owner_run_id=str(uuid4()),
        kind="test",
        media_type="application/octet-stream",
    )
    path = (
        staged_file.staging_path
        if staged
        else ws.store.resolve_relative_path(ws.store.finalize(staged_file).relative_path)
    )
    if old:
        timestamp = time.time() - MIN_AGE_SECONDS - 60
        os.utime(path, (timestamp, timestamp))
    return path


def test_scan_preserves_registered_and_unknown_files(phase3_workspace):
    ws = phase3_workspace
    generated = orphan(ws)
    staged = orphan(ws, staged=True)
    recent = orphan(ws, old=False)
    user = ws.catalog.paths.staging / "my-data.csv"
    user.write_bytes(b"user")
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    assert {i.path for i in review.candidates} == {
        p.relative_to(service.root).as_posix() for p in (generated, staged)
    }
    assert review.usage["registered"] > 0
    batch = service.quarantine(review, tuple(i.path for i in review.candidates))
    assert user.read_bytes() == b"user" and recent.exists()
    assert not generated.exists() and not staged.exists()
    ws.store.verify(ws.catalog.get_artifact(ws.imported.version.table_artifact_id))
    assert not service.scan().candidates
    assert batch in service.batches()
    assert service.restore(batch) == 2
    assert generated.read_bytes() == b"orphan" and staged.read_bytes() == b"orphan"
    assert service.restore(batch) == 0


@pytest.mark.parametrize(
    "change", ["content", "delete", "recent", "root", "selection", "registered"]
)
def test_stale_review_refused_before_any_move(phase3_workspace, change):
    ws = phase3_workspace
    first, second = orphan(ws), orphan(ws)
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    selected = tuple(i.path for i in review.candidates)
    if change == "content":
        stat = second.stat()
        second.write_bytes(b"changed")
        os.utime(second, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "delete":
        second.unlink()
    elif change == "recent":
        os.utime(second, None)
    elif change == "root":
        review = replace(review, root="other")
    elif change == "selection":
        selected += ("catalog.sqlite",)
    else:
        with ws.catalog.transaction() as connection:
            connection.execute(
                "UPDATE artifacts SET relative_path=? WHERE artifact_id=?",
                (
                    second.relative_to(service.root).as_posix(),
                    ws.imported.version.table_artifact_id,
                ),
            )
    with pytest.raises(ResourceError):
        service.quarantine(review, selected)
    assert first.exists()
    assert not service.batches()


@pytest.mark.parametrize("block", ["writer", "lease", "pending", "running"])
def test_activity_blocks_maintenance(phase3_workspace, block):
    ws = phase3_workspace
    orphan(ws)
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    if block == "lease":
        (ws.catalog.paths.staging / "worker-lease.json").write_text("invalid")
    elif block != "writer":
        with ws.catalog.transaction() as connection:
            connection.execute(
                "INSERT INTO analysis_runs (run_id,project_id,kind,request_key,input_fingerprint,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    ws.project.project_id,
                    "test",
                    "test",
                    "test",
                    block,
                    "{}",
                    "2026-01-01",
                ),
            )
    if block == "writer":
        with write_activity(service.root), pytest.raises(ResourceError):
            service.quarantine(review, tuple(i.path for i in review.candidates))
    else:
        with pytest.raises(ResourceError):
            service.quarantine(review, tuple(i.path for i in review.candidates))
    assert not service.batches()


def test_gate_prevents_new_writer_and_releases_after_exception(phase3_workspace):
    root = phase3_workspace.catalog.paths.root
    with maintenance_gate(root), pytest.raises(ResourceError), write_activity(root):
        pytest.fail("writer entered maintenance gate")
    with pytest.raises(ValueError), write_activity(root):
        raise ValueError("failed producer")
    assert not list((root / "staging").glob("write-*.json"))
    with maintenance_gate(root):
        require_idle_writers(root)


@pytest.mark.parametrize("kind", ["dead", "reused", "live", "malformed"])
def test_marker_identity_is_conservative(phase3_workspace, kind):
    root = phase3_workspace.catalog.paths.root
    marker = root / "staging" / "write-test.json"
    payload = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    if kind == "dead":
        payload["pid"] = 2**30
    elif kind == "reused":
        payload["created"] -= 100
    marker.write_text("bad" if kind == "malformed" else json.dumps(payload))
    if kind in {"live", "malformed"}:
        with pytest.raises(ResourceError):
            require_idle_writers(root)
    else:
        require_idle_writers(root)
    assert marker.exists()


@pytest.mark.parametrize(
    "relative", ["../outside", "C:/outside", "staging/file:stream", "staging/../catalog.sqlite"]
)
def test_unsafe_paths_refused(phase3_workspace, relative):
    with pytest.raises(ResourceError):
        safe_path(phase3_workspace.catalog.paths.root, relative)


@pytest.mark.parametrize("change", ["occupied", "tampered", "manifest"])
def test_restore_never_overwrites_or_accepts_corrupt_files(phase3_workspace, change):
    ws = phase3_workspace
    path = orphan(ws)
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    batch = service.quarantine(review, tuple(i.path for i in review.candidates))
    directory = service.root / "staging" / "maintenance-trash" / batch
    if change == "occupied":
        path.write_bytes(b"existing")
    elif change == "tampered":
        (directory / path.relative_to(service.root)).write_bytes(b"tampered")
    else:
        payload = json.loads((directory / "manifest.json").read_text())
        payload["files"][0]["path"] = "../outside"
        (directory / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ResourceError):
        service.restore(batch)
    if change == "occupied":
        assert path.read_bytes() == b"existing"


def test_partial_move_is_recoverable_from_write_ahead_manifest(phase3_workspace, monkeypatch):
    ws = phase3_workspace
    paths = [orphan(ws), orphan(ws)]
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    rename = os.rename
    count = 0

    def interrupted(source, target):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("simulated disk error")
        rename(source, target)

    monkeypatch.setattr(os, "rename", interrupted)
    with pytest.raises(OSError):
        service.quarantine(review, tuple(i.path for i in review.candidates))
    assert service.restore(service.batches()[0]) == 1
    assert all(p.read_bytes() == b"orphan" for p in paths)


def test_cross_process_gate_blocks_writers(phase3_workspace):
    import subprocess
    import sys

    root = phase3_workspace.catalog.paths.root
    code = """
import sys
from pathlib import Path
from autoanalyst.storage.activity import write_activity
from autoanalyst.domain.errors import ResourceError
try:
    with write_activity(Path(sys.argv[1])):
        raise SystemExit(2)
except ResourceError:
    raise SystemExit(0)
"""
    with maintenance_gate(root):
        result = subprocess.run(
            [sys.executable, "-c", code, str(root)], capture_output=True, timeout=15
        )
    assert result.returncode == 0, result.stderr.decode()


def test_junction_or_symlink_is_never_traversed(phase3_workspace, tmp_path):
    root = phase3_workspace.catalog.paths.root
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_bytes(b"keep")
    link = root / "staging" / "linked"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    service = StorageMaintenanceService(phase3_workspace.catalog)
    assert service.scan().skipped_links == 1
    with pytest.raises(ResourceError):
        safe_path(root, "staging/linked/keep.txt")
    assert marker.read_bytes() == b"keep"


def test_interrupted_restore_after_link_is_recoverable(phase3_workspace):
    ws = phase3_workspace
    original = orphan(ws)
    service = StorageMaintenanceService(ws.catalog)
    review = service.scan()
    batch = service.quarantine(review, tuple(i.path for i in review.candidates))
    quarantined = (
        service.root / "staging" / "maintenance-trash" / batch / original.relative_to(service.root)
    )
    os.link(quarantined, original)
    assert service.restore(batch) == 1
    assert original.read_bytes() == b"orphan" and not quarantined.exists()
