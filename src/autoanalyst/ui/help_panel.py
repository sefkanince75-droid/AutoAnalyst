"""Offline quick start and explicit creation of independent synthetic projects."""

import streamlit as st

from ..application.examples import create_example
from ..localization import I18N, error_text


def render_help(runtime, language):
    t = I18N[language]
    with st.sidebar.expander(t["help_title"]):
        st.markdown(t["quick_start"])
        kind = st.selectbox(
            t["example_kind"], ("quality", "binary", "regression", "multiclass"), key="example_kind"
        )
        st.caption(t["example_synthetic"])
        if st.button(t["example_create"], key="create_example"):
            try:
                project = create_example(runtime, kind, language=language)
                st.session_state.project_id = project.project_id
                st.session_state.project_selector = project
                st.rerun()
            except Exception as exc:
                st.error(error_text(exc, language))
