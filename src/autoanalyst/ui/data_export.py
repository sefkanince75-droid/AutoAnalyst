"""Explicit full-version export, separate from the browser's current page."""

from uuid import uuid4

import streamlit as st

from ..application.data_export import DataExportService
from ..domain.codec import fingerprint
from ..localization import I18N, error_text

MAX_DOWNLOAD_BYTES = 256 * 1024**2


def render_data_export(runtime, project, dataset, version, language):
    t = I18N[language]
    service = DataExportService(runtime.catalog, runtime.artifact_store)
    root = runtime.catalog.paths.root
    with st.expander(t["data_export"]):
        st.caption(
            t["export_scope"].format(
                version=version.version_id, rows=version.row_count, columns=version.column_count
            )
        )
        fmt = st.selectbox(t["export_format"], ("csv", "parquet"), key="data_export_format")
        st.caption(t["export_csv_note"] if fmt == "csv" else t["export_parquet_note"])
        key = f"data_export_path_{version.version_id}_{fmt}"
        if key not in st.session_state:
            st.session_state[key] = str(
                root.parent / f"data-{version.version_id[:8]}-{uuid4().hex[:8]}.{fmt}"
            )
        destination = st.text_input(t["export_destination"], key=key)
        context = fingerprint(
            {
                "root": str(root),
                "project": project.project_id,
                "dataset": dataset.dataset_id,
                "version": version.version_id,
                "format": fmt,
                "destination": destination,
            }
        )
        if st.session_state.get("data_export_context") != context:
            st.session_state.pop("data_export_ready", None)
            st.session_state.data_export_context = context
        if st.button(
            t["export_create"], key="data_export_create", disabled=not destination.strip()
        ):
            st.session_state.pop("data_export_ready", None)
            try:
                st.session_state.data_export_ready = service.export(
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    version_id=version.version_id,
                    format=fmt,
                    destination=destination,
                )
                st.success(t["export_done"])
            except Exception as exc:
                st.error(error_text(exc, language))
        ready = st.session_state.get("data_export_ready")
        if ready:
            st.text(ready.path)
            st.caption(
                t["export_receipt"].format(
                    rows=ready.rows, columns=len(ready.columns), size=ready.size, sha=ready.sha256
                )
            )
            if ready.size <= MAX_DOWNLOAD_BYTES:
                try:
                    content = service.download_bytes(ready, limit=MAX_DOWNLOAD_BYTES)
                    st.download_button(
                        t["export_download"],
                        content,
                        file_name=f"data-{ready.version_id[:8]}.{ready.format}",
                        mime="text/csv"
                        if ready.format == "csv"
                        else "application/vnd.apache.parquet",
                        key="data_export_download",
                    )
                except Exception as exc:
                    st.error(error_text(exc, language))
            else:
                st.info(t["export_disk_only"])
