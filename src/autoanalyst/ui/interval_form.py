"""Explicit confidence level and independence acknowledgement."""

import streamlit as st

from ..domain.plans import AnalysisModuleId
from .typed_inputs import input_kind


def render_interval_form(
    runtime, project_id, version_id, by_name, t, *, launch, make_spec, distinct
):
    mode = st.selectbox(
        t["ci_kind"], ("mean_interval", "proportion_interval"), format_func=lambda v: t[v]
    )
    choices = tuple(
        name
        for name, c in by_name.items()
        if mode != "mean_interval" or input_kind(c) in {"integer", "numeric"}
    )
    if not choices:
        st.info(t["no_numeric_columns"])
        return
    name = st.selectbox(t["column"], choices, key="ci_column")
    params = {"column_id": str(by_name[name]["column_id"])}
    if mode == "proportion_interval":
        labels = distinct(runtime, version_id, by_name[name])
        if not labels:
            return
        params["success_label"] = st.selectbox(
            t["positive_event"], labels, format_func=repr, key="ci_success"
        )
    params["confidence"] = st.slider(t["confidence_level"], 0.8, 0.99, 0.95, 0.01)
    params["independence_confirmed"] = st.checkbox(t["ci_independent"], key="ci_independent")
    st.caption(t["ci_scope"])
    if st.button(t["run"], key="run_interval", disabled=not params["independence_confirmed"]):
        launch(
            runtime,
            make_spec(project_id, version_id, AnalysisModuleId.COMPARISON, mode, params, ()),
        )
