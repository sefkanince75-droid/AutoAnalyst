"""Select parsing options, inspect a bounded sample, then explicitly publish an import."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

import pandas as pd
import streamlit as st

from ..data import CSVIngestor, XLSXIngestor
from ..data.preview import IMPORT_PREVIEW_ROWS
from ..data.table_access import MAX_TABLE_PAGE_COLUMNS
from ..domain.codec import fingerprint
from ..execution.data_tasks import DataTaskService, active_data_task
from .import_feedback import show_import_error


def render_import_panel(
    runtime, project, dataset, language: str, labels: Mapping[str, str]
) -> None:
    st.subheader(labels["import"])
    uploaded = st.file_uploader(
        "CSV / XLSX", type=("csv", "xlsx"), key=f"upload_{dataset.dataset_id}"
    )
    if uploaded is None:
        _clear_import_sample()
        st.session_state.pop("import_sheet_names", None)
        return
    try:
        raw = uploaded.getvalue()
        source_context = fingerprint(
            {
                "dataset_id": dataset.dataset_id,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "format": "xlsx" if uploaded.name.lower().endswith(".xlsx") else "csv",
            }
        )
        st.caption(labels["import_settings"])
        is_xlsx = uploaded.name.lower().endswith(".xlsx")
        settings: dict[str, str] = {}
        if is_xlsx:
            ingestor = XLSXIngestor(runtime.catalog, runtime.artifact_store)
            cached_sheets = st.session_state.get("import_sheet_names")
            if cached_sheets is None or cached_sheets["context"] != source_context:
                cached_sheets = {"context": source_context, "names": ingestor.sheet_names(raw)}
                st.session_state.import_sheet_names = cached_sheets
            settings["sheet_name"] = st.selectbox(
                labels["worksheet"], cached_sheets["names"], key=f"import_sheet_{source_context}"
            )
        else:
            ingestor = CSVIngestor(runtime.catalog, runtime.artifact_store)
            delimiter_labels = {
                ",": labels["comma"],
                ";": labels["semicolon"],
                "\t": labels["tab_character"],
                "|": labels["pipe"],
            }
            settings["delimiter"] = st.selectbox(
                labels["csv_delimiter"],
                tuple(delimiter_labels),
                format_func=delimiter_labels.get,
                key=f"csv_delimiter_{source_context}",
            )
            settings["encoding"] = st.selectbox(
                labels["csv_encoding"],
                ("utf-8-sig", "utf-8", "cp1254", "iso8859-9", "utf-16"),
                key=f"csv_encoding_{source_context}",
            )
            st.caption(labels["encoding_hint"])
        context = fingerprint({"source": source_context, "settings": settings})
        cached = st.session_state.get("import_sample")
        if cached is not None and cached["context"] != context:
            _clear_import_sample()
            cached = None
        if st.button(labels["preview_import"], key="preview_import"):
            _clear_import_sample()
            sample = (
                ingestor.preview_xlsx(raw, **settings)
                if is_xlsx
                else ingestor.preview_csv(raw, **settings)
            )
            cached = {"context": context, "sample": sample}
            st.session_state.import_sample = cached
        if cached is not None:
            sample = cached["sample"]
            frame = sample.frame
            st.caption(
                labels["import_sample_summary"].format(
                    rows=len(frame), columns=len(frame.columns), limit=IMPORT_PREVIEW_ROWS
                )
            )
            st.dataframe(
                frame.iloc[:, :MAX_TABLE_PAGE_COLUMNS], hide_index=True, use_container_width=True
            )
            if len(frame.columns) > MAX_TABLE_PAGE_COLUMNS:
                st.caption(labels["sample_columns_limited"].format(limit=MAX_TABLE_PAGE_COLUMNS))
            st.caption(labels["import_sample_advisory"])
            if not is_xlsx and len(frame.columns) == 1:
                st.warning(labels["single_csv_column_hint"])
            if sample.has_more_rows:
                st.info(labels["import_sample_more"])
            with st.expander(labels["sample_schema"]):
                st.dataframe(
                    pd.DataFrame(
                        {
                            labels["column_name"]: list(frame.columns),
                            labels["sample_type"]: [str(dtype) for dtype in frame.dtypes],
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
        else:
            st.info(labels["preview_before_import"])
        large = len(raw) >= (1024**2 if is_xlsx else 4 * 1024**2)
        isolated = (
            st.checkbox(
                labels["data_task_isolate"],
                value=large,
                disabled=large,
                key=f"isolate_import_{source_context}",
            )
            or large
        )
        st.caption(labels["data_limits_hint"])
        committed = False
        if st.button(
            labels["import"] + (" XLSX" if is_xlsx else " CSV"),
            key="import_xlsx" if is_xlsx else "import_csv",
            disabled=cached is None or bool(active_data_task(runtime.catalog.paths.root)),
        ):
            arguments = {
                "project_id": project.project_id,
                "dataset_id": dataset.dataset_id,
                "source": raw,
                "original_name": uploaded.name,
                **settings,
            }
            if isolated:
                job_id = DataTaskService(runtime.catalog).start(
                    operation="xlsx" if is_xlsx else "csv",
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    source=raw,
                    options={"original_name": uploaded.name, **settings},
                )
                st.session_state.data_task_id = job_id
                st.session_state.data_task_context = context
                st.rerun()
            elif is_xlsx:
                ingestor.import_xlsx(**arguments)
            else:
                ingestor.import_csv(**arguments)
            committed = True
    except Exception as error:
        _clear_import_sample()
        show_import_error(error, language, runtime.catalog.paths.logs)
        return
    if committed:
        _clear_import_sample()
        st.rerun()


def _clear_import_sample() -> None:
    st.session_state.pop("import_sample", None)
