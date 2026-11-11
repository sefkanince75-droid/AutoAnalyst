"""Version-scoped browsing with bounded row and column projections."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import streamlit as st

from ..data import TableAccess
from ..data.table_access import MAX_TABLE_PAGE_COLUMNS


def render_data_browser(runtime, version, columns, labels: Mapping[str, str]) -> None:
    st.subheader(labels["browse_data"])
    by_id = {str(column["column_id"]): column for column in columns}
    selected = st.multiselect(
        labels["visible_columns"],
        tuple(by_id),
        default=list(by_id)[:20],
        format_func=lambda column_id: str(by_id[column_id]["display_name"]),
        max_selections=MAX_TABLE_PAGE_COLUMNS,
        key=f"data_columns_{version.version_id}",
    )
    st.caption(labels["browser_limits"].format(limit=MAX_TABLE_PAGE_COLUMNS))
    if not selected:
        st.info(labels["select_preview_columns"])
        return
    left, right = st.columns(2)
    page_size = left.selectbox(
        labels["rows_per_page"],
        (25, 50, 100, 250, 500),
        index=2,
        key=f"data_page_size_{version.version_id}",
    )
    total_pages = max(1, (version.row_count + page_size - 1) // page_size)
    page = int(
        right.number_input(
            labels["page_number"],
            min_value=1,
            max_value=total_pages,
            value=1,
            step=1,
            key=f"data_page_{version.version_id}_{page_size}",
        )
    )
    offset = (page - 1) * page_size
    try:
        table = TableAccess(runtime.catalog, runtime.artifact_store).page(
            version.version_id, tuple(selected), limit=page_size, offset=offset
        )
        names = {
            str(by_id[column_id]["physical_name"]): str(by_id[column_id]["display_name"])
            for column_id in selected
        }
        frame = table.to_pandas().rename(columns=names)
        frame.index = pd.RangeIndex(offset + 1, offset + len(frame) + 1)
        st.caption(
            labels["page_summary"].format(
                first=offset + 1 if len(frame) else 0,
                last=offset + len(frame),
                total=version.row_count,
                page=page,
                pages=total_pages,
            )
        )
        st.dataframe(frame, use_container_width=True)
    except Exception:
        st.error(labels["data_preview_error"])
