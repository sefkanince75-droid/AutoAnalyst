"""Review a saved version, then move the head without rewriting any stored data."""

import streamlit as st

from ..data import TableAccess
from ..domain.codec import fingerprint
from ..localization import I18N, error_text, label


def render_version_navigation(runtime, project, dataset, versions, datasets, language):
    t = I18N[language]
    notice = st.session_state.pop("version_restore_notice", None)
    if notice and notice[0] == dataset.dataset_id:
        st.success(t["version_restored"].format(version=notice[1][:8]))
    choices = [version for version in versions if version.version_id != dataset.head_version_id]
    if not choices or not dataset.head_version_id:
        st.caption(t["no_restore_versions"])
        st.session_state.pop("version_restore_review", None)
        return
    st.subheader(t["restore_version"])
    st.caption(t["restore_version_hint"])
    by_id = {version.version_id: version for version in choices}
    names = {
        value: f"{v.created_at.isoformat()} · {label(v.kind.value, language)} · {v.row_count} × {v.column_count} · {value[:8]}"
        for value, v in by_id.items()
    }
    target_id = st.selectbox(
        t["restore_target"],
        tuple(by_id),
        format_func=names.__getitem__,
        key=f"restore_target_{dataset.dataset_id}_{dataset.head_revision}",
    )
    context = fingerprint(
        {
            "project_id": project.project_id,
            "dataset_id": dataset.dataset_id,
            "head_version_id": dataset.head_version_id,
            "head_revision": dataset.head_revision,
            "target_id": target_id,
        }
    )
    review = st.session_state.get("version_restore_review")
    if review and review["context"] != context:
        st.session_state.pop("version_restore_review", None)
        review = None
    if st.button(t["preview_version_restore"], key="preview_version_restore"):
        st.session_state.pop("version_restore_review", None)
        try:
            columns = [
                column
                for column in runtime.catalog.list_columns(target_id)
                if not column["is_system"]
            ][:20]
            sample = TableAccess(runtime.catalog, runtime.artifact_store).page(
                target_id, tuple(column["column_id"] for column in columns), limit=25
            )
            frame = sample.to_pandas()
            frame.columns = [column["display_name"] for column in columns]
            review = {"context": context, "frame": frame}
            st.session_state.version_restore_review = review
        except Exception as exc:
            st.error(error_text(exc, language))
            review = None
    if review:
        current = datasets.get_version(dataset.head_version_id)
        target = by_id[target_id]
        st.info(
            t["restore_comparison"].format(
                current=current.version_id[:8],
                current_rows=current.row_count,
                current_columns=current.column_count,
                target=target_id[:8],
                target_rows=target.row_count,
                target_columns=target.column_count,
            )
        )
        st.caption(t["restore_sample_hint"])
        st.dataframe(review["frame"], hide_index=True, width="stretch")
        if st.button(t["apply_version_restore"], key="apply_version_restore"):
            try:
                datasets.restore_version(
                    dataset.dataset_id,
                    target_id,
                    expected_project_id=project.project_id,
                    expected_head_version_id=dataset.head_version_id,
                    expected_head_revision=dataset.head_revision,
                )
                for key in (
                    "preview_id",
                    "preview_context",
                    "version_restore_review",
                    "import_sample",
                    "import_sheet_names",
                ):
                    st.session_state.pop(key, None)
                st.session_state.version_restore_notice = (dataset.dataset_id, target_id)
                st.rerun()
            except Exception as exc:
                st.session_state.pop("version_restore_review", None)
                st.error(error_text(exc, language))
