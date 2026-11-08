"""Inspect trusted workspace backups and restore to a separate empty folder."""

import hashlib
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from ..application.snapshots import WorkspaceSnapshotService
from ..bootstrap import build_runtime
from ..domain.codec import fingerprint
from ..domain.errors import SchemaError
from ..execution.data_tasks import active_data_task
from ..localization import I18N, error_text

MAX_UI_SNAPSHOT_BYTES = 256 * 1024**2


def _open_workspace(runtime, path, language):
    try:
        if runtime.run_store.active_analysis_runs() or active_data_task(runtime.catalog.paths.root):
            raise SchemaError({"reason": "snapshot_worker_active"})
        destination = Path(path).expanduser().resolve()
        if not (destination / "catalog.sqlite").is_file():
            raise SchemaError({"reason": "workspace_catalog_missing"})
        build_runtime(destination)
        origin = st.session_state.get("original_workspace", str(runtime.catalog.paths.root))
        language_override = st.session_state.get("language_override", False)
        for key in list(st.session_state):
            del st.session_state[key]
        st.session_state.active_workspace = str(destination)
        st.session_state.original_workspace = origin
        if language_override:
            st.session_state.language_override = True
            st.session_state.language = language
    except Exception as exc:
        st.session_state.workspace_open_error = error_text(exc, language)


def _destination(root, text):
    destination = Path(text).expanduser().absolute()
    if not text.strip() or destination.resolve().is_relative_to(root):
        raise SchemaError({"reason": "snapshot_destination_current"})
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SchemaError({"reason": "snapshot_destination_not_empty"})
    return destination


def render_workspace_backups(runtime, language):
    t = I18N[language]
    root = runtime.catalog.paths.root
    service = WorkspaceSnapshotService(runtime.catalog, runtime.artifact_store)
    active = bool(runtime.run_store.active_analysis_runs() or active_data_task(root))
    with st.sidebar.expander(t["workspace_backups"]):
        st.caption(t["current_workspace"])
        st.text(str(root))
        if message := st.session_state.pop("workspace_open_error", None):
            st.error(message)
        if active:
            st.info(t["snapshot_idle_hint"])
        st.caption(t["snapshot_export_scope"])
        if "snapshot_export_destination" not in st.session_state:
            st.session_state.snapshot_export_destination = str(
                root.parent / f"{root.name}-backup-{uuid4().hex[:8]}.aasnapshot"
            )
        target = st.text_input(t["snapshot_export_destination"], key="snapshot_export_destination")
        if st.button(
            t["create_snapshot"], key="create_snapshot", disabled=active or not target.strip()
        ):
            st.session_state.pop("snapshot_export_ready", None)
            try:
                exported = service.export_snapshot(target)
                st.session_state.snapshot_export_ready = {
                    "path": str(exported),
                    "root": str(root),
                    "sha256": _digest(exported),
                }
                st.success(t["snapshot_created"])
            except Exception as exc:
                st.error(error_text(exc, language))
        ready = st.session_state.get("snapshot_export_ready")
        if ready and ready["root"] == str(root):
            path = Path(ready["path"])
            st.text(str(path))
            if path.is_file() and path.stat().st_size <= MAX_UI_SNAPSHOT_BYTES:
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() == ready["sha256"]:
                    st.download_button(
                        t["download_snapshot"],
                        content,
                        file_name=path.name,
                        mime="application/zip",
                        key="download_snapshot",
                    )
                else:
                    st.error(t["snapshot_changed"])
            elif path.is_file():
                st.info(t["snapshot_disk_only"])
        st.divider()
        st.caption(t["snapshot_restore_hint"])
        uploaded = st.file_uploader(
            t["snapshot_upload"],
            type=["aasnapshot", "zip"],
            max_upload_size=256,
            key="snapshot_upload",
        )
        if "snapshot_restore_destination" not in st.session_state:
            st.session_state.snapshot_restore_destination = str(
                root.parent / f"{root.name}-restored-{uuid4().hex[:8]}"
            )
        restore_target = st.text_input(
            t["snapshot_restore_destination"], key="snapshot_restore_destination"
        )
        if uploaded is None:
            st.session_state.pop("snapshot_inspection", None)
        else:
            raw = uploaded.getvalue()
            context = fingerprint(
                {
                    "root": str(root),
                    "source": hashlib.sha256(raw).hexdigest(),
                    "target": restore_target,
                }
            )
            if st.session_state.get("snapshot_inspection", {}).get("context") != context:
                st.session_state.pop("snapshot_inspection", None)
            if len(raw) > MAX_UI_SNAPSHOT_BYTES:
                st.error(t["snapshot_ui_size_limit"])
            elif st.button(t["inspect_snapshot"], key="inspect_snapshot", disabled=active):
                st.session_state.pop("snapshot_inspection", None)
                try:
                    _destination(root, restore_target)
                    with tempfile.TemporaryDirectory(prefix="autoanalyst-upload-") as directory:
                        source = Path(directory) / "upload.aasnapshot"
                        source.write_bytes(raw)
                        summary = service.inspect_snapshot(source)
                    st.session_state.snapshot_inspection = {"context": context, "summary": summary}
                except Exception as exc:
                    st.error(error_text(exc, language))
            review = st.session_state.get("snapshot_inspection")
            if review and review["context"] == context:
                st.success(t["snapshot_compatible"])
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                t["snapshot_property"]: t.get("snapshot_field." + key, key),
                                t["value"]: str(value),
                            }
                            for key, value in asdict(review["summary"]).items()
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                )
                st.warning(t["snapshot_trust_hint"])
                trusted = st.checkbox(t["snapshot_trust"], key="snapshot_trust_" + context[:16])
                if st.button(
                    t["restore_snapshot"], key="restore_snapshot", disabled=active or not trusted
                ):
                    try:
                        destination = _destination(root, restore_target)
                        with tempfile.TemporaryDirectory(prefix="autoanalyst-upload-") as directory:
                            source = Path(directory) / "upload.aasnapshot"
                            source.write_bytes(raw)
                            restored = service.import_snapshot(source, destination, trusted=True)
                        st.session_state.snapshot_restored_path = str(restored)
                        st.session_state.pop("snapshot_inspection", None)
                        st.success(t["snapshot_restored"])
                    except Exception as exc:
                        st.error(error_text(exc, language))
        if path := st.session_state.get("snapshot_restored_path"):
            st.text(path)
            st.button(
                t["open_restored_workspace"],
                key="open_restored_workspace",
                disabled=active,
                on_click=_open_workspace,
                args=(runtime, path, language),
            )
        st.divider()
        st.caption(t["workspace_open_hint"])
        existing = st.text_input(t["workspace_open_path"], key="workspace_open_path")
        acknowledge = st.checkbox(
            t["workspace_open_trust"], key="workspace_open_trust_" + fingerprint(existing)[:16]
        )
        st.button(
            t["open_workspace"],
            key="open_workspace",
            disabled=active or not existing.strip() or not acknowledge,
            on_click=_open_workspace,
            args=(runtime, existing, language),
        )
        origin = st.session_state.get("original_workspace")
        if origin and origin != str(root):
            st.button(
                t["return_workspace"],
                key="return_workspace",
                disabled=active,
                on_click=_open_workspace,
                args=(runtime, origin, language),
            )


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
