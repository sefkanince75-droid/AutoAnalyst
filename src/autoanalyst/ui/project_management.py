"""Reversible project metadata actions with revision-bound widget callbacks."""

import streamlit as st

from ..application.projects import ProjectService
from ..localization import I18N, error_text


def _clear_edit_state():
    from .recipe_editor import clear_draft

    clear_draft()
    for key in (
        "preview_id",
        "preview_context",
        "report_id",
        "report_context",
        "version_restore_review",
        "import_sample",
        "import_sheet_names",
    ):
        st.session_state.pop(key, None)


def _rename(runtime, project_id, revision, name_key, language):
    try:
        updated = ProjectService(runtime.catalog).rename(
            project_id,
            st.session_state[name_key],
            expected_revision=revision,
        )
        st.session_state.project_selector = updated
        st.session_state.project_mgmt_notice = ("success", I18N[language]["project_renamed"])
    except Exception as exc:
        st.session_state.project_mgmt_notice = ("error", error_text(exc, language))


def _archive(runtime, project_id, revision, archived, language):
    try:
        service = ProjectService(runtime.catalog)
        updated = service.set_archived(project_id, archived, expected_revision=revision)
        _clear_edit_state()
        st.session_state.pop("project_selector", None)
        st.session_state.pop("project_id", None)
        if archived:
            remaining = service.list()
            updated = remaining[0] if remaining else None
        if updated is not None:
            st.session_state.project_id = updated.project_id
            st.session_state.project_selector = updated
            if not st.session_state.get("language_override", False):
                st.session_state.language = updated.default_language
        st.session_state.project_mgmt_notice = (
            "success",
            I18N[language]["project_archived" if archived else "project_restored"],
        )
    except Exception as exc:
        st.session_state.project_mgmt_notice = ("error", error_text(exc, language))


def render_archived_projects(runtime, language):
    t = I18N[language]
    notice = st.session_state.pop("project_mgmt_notice", None)
    if notice:
        (st.sidebar.success if notice[0] == "success" else st.sidebar.error)(notice[1])
    archived = [
        project
        for project in ProjectService(runtime.catalog).list(include_archived=True)
        if project.archived_at is not None
    ]
    if archived:
        by_id = {project.project_id: project for project in archived}
        names = {key: f"{project.name} · {key[:8]}" for key, project in by_id.items()}
        with st.sidebar.expander(t["archived_projects"]):
            selected = st.selectbox(
                t["archived_project"],
                tuple(by_id),
                format_func=names.__getitem__,
                key="archived_project_selector",
            )
            st.caption(t["archive_hint"])
            project = by_id[selected]
            st.button(
                t["restore_project"],
                key="restore_project",
                on_click=_archive,
                args=(runtime, project.project_id, project.revision, False, language),
            )


def render_project_management(runtime, project, language):
    t = I18N[language]
    with st.sidebar.expander(t["manage_project"]):
        name_key = f"project_name_{project.project_id}_{project.revision}"
        st.text_input(t["project_new_name"], value=project.name, key=name_key)
        st.button(
            t["rename_project"],
            key="rename_project",
            on_click=_rename,
            args=(runtime, project.project_id, project.revision, name_key, language),
            disabled=not st.session_state[name_key].strip()
            or st.session_state[name_key].strip() == project.name,
        )
        st.caption(t["archive_hint"])
        active = any(
            run.project_id == project.project_id for run in runtime.run_store.active_analysis_runs()
        )
        if active:
            st.info(t["error.project_has_active_runs"])
        st.button(
            t["archive_project"],
            key="archive_project",
            disabled=active,
            on_click=_archive,
            args=(runtime, project.project_id, project.revision, True, language),
        )
