from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from autoanalyst.application.snapshots import WorkspaceSnapshotService, _safe_relative
from autoanalyst.domain.errors import DataError, ResourceError, SchemaError


def _export(ws, tmp_path):
    return WorkspaceSnapshotService(ws.catalog, ws.store).export_snapshot(
        tmp_path / "original.aasnapshot"
    )


def _rewrite(source, target, change):
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    change(entries, manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return target


def _catalog_change(entries, manifest, tmp_path, sql):
    path = tmp_path / "edited.sqlite"
    path.write_bytes(entries["catalog.sqlite"])
    connection = sqlite3.connect(path)
    try:
        sql(connection) if callable(sql) else connection.execute(sql)
        connection.commit()
    finally:
        connection.close()
    raw = path.read_bytes()
    entries["catalog.sqlite"] = raw
    manifest["catalog"].update(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))


def test_restore_requires_trust_even_for_a_valid_snapshot(phase3_workspace, tmp_path):
    source = _export(phase3_workspace, tmp_path)
    with pytest.raises(SchemaError) as missing:
        WorkspaceSnapshotService.import_snapshot(source, tmp_path / "restore")
    assert missing.value.context["reason"] == "snapshot_trust_required"
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize(
    "case",
    [
        "future_version",
        "future_schema",
        "checksum",
        "trigger",
        "missing_artifact",
        "catalog_mapping",
    ],
)
def test_invalid_or_incompatible_snapshot_never_publishes_destination(
    phase3_workspace, tmp_path, case
):
    source = _export(phase3_workspace, tmp_path)

    def corrupt(entries, manifest):
        if case == "future_version":
            manifest["autoanalyst_version"] = "999.0.0"
        elif case == "future_schema":
            _catalog_change(
                entries,
                manifest,
                tmp_path,
                "INSERT INTO schema_migrations VALUES(999,'"
                + "0" * 64
                + "','2026-10-24T00:00:00Z')",
            )
        elif case == "trigger":
            _catalog_change(
                entries,
                manifest,
                tmp_path,
                "CREATE TRIGGER unexpected AFTER INSERT ON projects BEGIN SELECT 1; END",
            )
        elif case == "checksum":
            path = manifest["artifacts"][0]["path"]
            raw = entries[path]
            entries[path] = bytes([raw[0] ^ 1]) + raw[1:]
        elif case == "missing_artifact":
            item = manifest["artifacts"].pop()
            del entries[item["path"]]
        else:
            _catalog_change(
                entries, manifest, tmp_path, "UPDATE artifacts SET sha256='" + "0" * 64 + "'"
            )

    damaged = _rewrite(source, tmp_path / "damaged.aasnapshot", corrupt)
    destination = tmp_path / "restore"
    destination.mkdir()
    with pytest.raises((SchemaError, DataError)):
        WorkspaceSnapshotService.import_snapshot(damaged, destination, trusted=True)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".autoanalyst-restore-*"))


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "projects/x/../escape",
        "projects\\x\\file",
        "projects/x/file:stream",
        "projects/x/CON.txt",
        "projects//x/file",
        "projects/x/file.",
    ],
)
def test_snapshot_paths_reject_windows_aliases_and_traversal(name):
    with pytest.raises(SchemaError):
        _safe_relative(name)


def test_symlink_archive_entry_is_rejected_before_extraction(phase3_workspace, tmp_path):
    source = _export(phase3_workspace, tmp_path)
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    bad = tmp_path / "link.aasnapshot"
    with zipfile.ZipFile(bad, "w") as archive:
        for name, raw in entries.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, raw)
    with pytest.raises(SchemaError) as unsafe:
        WorkspaceSnapshotService.inspect_snapshot(bad)
    assert unsafe.value.context["reason"] == "snapshot_archive_unsupported"


def test_target_that_becomes_nonempty_during_validation_is_preserved(
    phase3_workspace, tmp_path, monkeypatch
):
    import autoanalyst.application.snapshots as snapshots

    source = _export(phase3_workspace, tmp_path)
    target = tmp_path / "restore"
    original = snapshots._prepare_restore

    def concurrent_writer(snapshot, root):
        value = original(snapshot, root)
        target.mkdir()
        (target / "keep.txt").write_text("do not overwrite")
        return value

    monkeypatch.setattr(snapshots, "_prepare_restore", concurrent_writer)
    with pytest.raises(SchemaError):
        WorkspaceSnapshotService.import_snapshot(source, target, trusted=True)
    assert (target / "keep.txt").read_text() == "do not overwrite"


def test_existing_backup_file_is_never_overwritten(phase3_workspace, tmp_path):
    source = _export(phase3_workspace, tmp_path)
    previous = source.read_bytes()
    with pytest.raises(SchemaError):
        WorkspaceSnapshotService(phase3_workspace.catalog, phase3_workspace.store).export_snapshot(
            source
        )
    assert source.read_bytes() == previous


def test_pending_work_blocks_backup_and_imported_active_catalog(phase3_workspace, tmp_path):
    from autoanalyst.domain.plans import AnalysisModuleId
    from autoanalyst.execution.coordinator import ExecutionCoordinator
    from autoanalyst.storage.runs import RunStore
    from autoanalyst.ui.app import _spec

    ws = phase3_workspace
    source = _export(ws, tmp_path)
    coordinator = ExecutionCoordinator(RunStore(ws.catalog))
    pending = coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.imported.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="backup-pending",
    )
    with pytest.raises(ResourceError):
        WorkspaceSnapshotService(ws.catalog, ws.store).export_snapshot(tmp_path / "busy.aasnapshot")

    # An incoming archive also cannot smuggle an unresolved active run into a restore.
    def add_pending(entries, manifest):
        with ws.catalog.connection() as connection:
            spec = connection.execute(
                "SELECT * FROM analysis_specs WHERE spec_id=?", (pending.spec_id,)
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM analysis_runs WHERE run_id=?", (pending.run_id,)
            ).fetchone()

        def inject(db):
            db.execute(
                "INSERT INTO analysis_specs VALUES(" + ",".join("?" for _ in spec) + ")",
                tuple(spec),
            )
            db.execute(
                "INSERT INTO analysis_runs VALUES(" + ",".join("?" for _ in run) + ")", tuple(run)
            )

        _catalog_change(entries, manifest, tmp_path, inject)

    bad = _rewrite(source, tmp_path / "active.aasnapshot", add_pending)
    with pytest.raises(ResourceError):
        WorkspaceSnapshotService.inspect_snapshot(bad)


def test_inspection_and_restore_never_deserialize_model_files(
    phase3_workspace, tmp_path, monkeypatch
):
    import joblib

    from autoanalyst.storage.sqlite import _insert_artifact

    ws = phase3_workspace
    staged = ws.store.stage_bytes(
        b"not a pickle",
        project_id=ws.project.project_id,
        owner_run_id=str(uuid4()),
        kind="binary_model",
        media_type="application/x-joblib",
        format_version="2.0",
    )
    artifact = ws.store.finalize(staged)
    with ws.catalog.transaction() as connection:
        _insert_artifact(connection, artifact)
    source = _export(ws, tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot inspection must not load models")

    monkeypatch.setattr(joblib, "load", forbidden)
    summary = WorkspaceSnapshotService.inspect_snapshot(source)
    assert summary.models == 1 and summary.projects == 1 and summary.datasets == 1
    WorkspaceSnapshotService.import_snapshot(source, tmp_path / "restore", trusted=True)


def test_known_older_catalog_is_migrated_before_publication(tmp_path):
    import autoanalyst.storage.sqlite as sqlite_module
    from autoanalyst.application import ProjectService
    from autoanalyst.storage import ArtifactStore, SQLiteCatalog

    migrations = tmp_path / "old-migrations"
    migrations.mkdir()
    for path in sorted(Path(sqlite_module.__file__).with_name("migrations").glob("*.sql"))[:3]:
        shutil.copyfile(path, migrations / path.name)
    old = SQLiteCatalog(tmp_path / "old", migrations_dir=migrations)
    project = ProjectService(old).create("Older schema")
    source = WorkspaceSnapshotService(old, ArtifactStore(old.paths.root)).export_snapshot(
        tmp_path / "old.aasnapshot"
    )
    restored = WorkspaceSnapshotService.import_snapshot(source, tmp_path / "restore", trusted=True)
    current = SQLiteCatalog(restored)
    assert len(current.migration_records()) == 8
    assert current.get_project(project.project_id).name == "Older schema"


@pytest.mark.parametrize(
    "limit", ["MAX_SNAPSHOT_BYTES", "MAX_UNPACKED_BYTES", "MAX_MANIFEST_BYTES", "MAX_ENTRIES"]
)
def test_archive_limits_are_checked_before_publication(
    phase3_workspace, tmp_path, monkeypatch, limit
):
    import autoanalyst.application.snapshots as snapshots

    source = _export(phase3_workspace, tmp_path)
    monkeypatch.setattr(snapshots, limit, 1)
    with pytest.raises(ResourceError):
        WorkspaceSnapshotService.import_snapshot(source, tmp_path / "restore", trusted=True)
    assert not (tmp_path / "restore").exists()


def test_incompatible_result_payload_is_rejected_before_publication(phase3_workspace, tmp_path):
    from threading import Event

    from autoanalyst.bootstrap import build_runtime
    from autoanalyst.domain.plans import AnalysisModuleId
    from autoanalyst.ui.app import _spec

    ws = phase3_workspace
    runtime = build_runtime(ws.catalog.paths.root)
    run = runtime.coordinator.create_run(
        _spec(
            ws.project.project_id,
            ws.imported.version.version_id,
            AnalysisModuleId.PROFILING,
            "profile",
            {},
            (),
        ),
        request_key="snapshot-result",
    )
    runtime.coordinator.execute_inline(
        run.run_id, runtime.registry.get(AnalysisModuleId.PROFILING), cancellation_event=Event()
    )
    source = _export(ws, tmp_path)

    def corrupt(entries, manifest):
        _catalog_change(
            entries, manifest, tmp_path, "UPDATE analysis_results SET payload_json='{}'"
        )

    bad = _rewrite(source, tmp_path / "bad-result.aasnapshot", corrupt)
    with pytest.raises(SchemaError) as invalid:
        WorkspaceSnapshotService.import_snapshot(bad, tmp_path / "restore", trusted=True)
    assert invalid.value.context["reason"] == "snapshot_catalog_incompatible"
    assert not (tmp_path / "restore").exists()


def test_backup_publication_does_not_overwrite_a_concurrent_destination(
    phase3_workspace, tmp_path, monkeypatch
):
    import autoanalyst.application.snapshots as snapshots

    target = tmp_path / "concurrent.aasnapshot"
    operation = "rename" if snapshots.os.name == "nt" else "link"
    original = getattr(snapshots.os, operation)

    def concurrent_file(source, destination, *args, **kwargs):
        if Path(destination) == target:
            target.write_bytes(b"concurrent user file")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(snapshots.os, operation, concurrent_file)
    with pytest.raises(SchemaError) as exists:
        WorkspaceSnapshotService(phase3_workspace.catalog, phase3_workspace.store).export_snapshot(
            target
        )
    assert exists.value.context["reason"] == "snapshot_destination_exists"
    assert target.read_bytes() == b"concurrent user file"
