"""User-reviewed storage inventory and reversible maintenance controls."""

import streamlit as st

from ..application.storage_maintenance import StorageMaintenanceService
from ..localization import I18N, error_text


def render_storage_maintenance(runtime, language):
    t = I18N[language]
    service = StorageMaintenanceService(runtime.catalog)
    root = str(runtime.catalog.paths.root)
    with st.sidebar.expander(t["storage_maintenance"]):
        st.caption(t["maintenance_scope"])
        if st.button(t["storage_scan"], key="storage_scan"):
            st.session_state.pop("storage_review", None)
            st.session_state.pop("storage_selection", None)
            try:
                st.session_state.storage_review = service.scan()
            except Exception as exc:
                st.error(error_text(exc, language))
        review = st.session_state.get("storage_review")
        if review and review.root == root:
            st.metric(t["storage_total"], _size(sum(review.usage.values())))
            st.dataframe(
                [
                    {t["storage_category"]: t["storage_" + key], t["storage_size"]: _size(value)}
                    for key, value in review.usage.items()
                ],
                hide_index=True,
            )
            st.caption(t["maintenance_links"].format(count=review.skipped_links))
            candidates = {item.path: item for item in review.candidates}
            if candidates:
                st.dataframe(
                    [
                        {t["storage_path"]: i.path, t["storage_size"]: _size(i.size)}
                        for i in candidates.values()
                    ],
                    hide_index=True,
                )
                selected = st.multiselect(
                    t["maintenance_select"], list(candidates), key="storage_selection"
                )
                st.caption(
                    t["maintenance_selected"].format(
                        size=_size(sum(candidates[p].size for p in selected))
                    )
                )
                if st.button(
                    t["maintenance_quarantine"], key="maintenance_quarantine", disabled=not selected
                ):
                    try:
                        batch = service.quarantine(review, tuple(selected))
                        st.session_state.pop("storage_review", None)
                        st.success(t["maintenance_done"].format(batch=batch))
                    except Exception as exc:
                        st.error(error_text(exc, language))
            else:
                st.info(t["maintenance_empty"])
        try:
            batches = service.batches()
            if batches:
                batch = st.selectbox(t["maintenance_batch"], batches, key="maintenance_batch")
                if st.button(t["maintenance_restore"], key="maintenance_restore"):
                    count = service.restore(batch)
                    st.session_state.pop("storage_review", None)
                    st.success(t["maintenance_restored"].format(count=count))
        except Exception as exc:
            st.error(error_text(exc, language))


def _size(value):
    return f"{value / 1024**2:.2f} MiB ({value:,} B)"
