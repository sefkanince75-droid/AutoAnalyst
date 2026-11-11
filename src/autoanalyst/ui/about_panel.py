"""User-facing version/build identity, available even without a project."""

import streamlit as st

from ..localization import I18N
from ..version_info import version_info


def render_about(language: str) -> None:
    t = I18N[language]
    info = version_info()
    channel = t["about_prerelease"] if info.prerelease else t["about_versioned"]
    st.sidebar.caption(f"AutoAnalyst {info.version} · {channel}")
    with st.sidebar.expander(t["about_title"]):
        st.text(f"{t['about_version']}: {info.version}")
        st.text(channel)
        st.text(f"{t['about_build']}: {info.build_id or t['about_unknown']}")
        if info.built_at:
            st.text(f"{t['about_built_at']}: {info.built_at}")
        if info.commit:
            st.text(f"{t['about_commit']}: {info.commit[:12]}")
        source = (
            t["about_development"]
            if not info.packaged
            else {
                "github-actions": t["about_ci"],
                "local": t["about_local"],
            }.get(info.source, t["about_unknown"])
        )
        st.text(f"{t['about_source']}: {source}")
        if info.executable:
            st.caption(t["about_location"])
            st.text(info.executable)
        st.caption(t["about_help"])
        if info.prerelease:
            st.info(t["about_prerelease_notice"])
