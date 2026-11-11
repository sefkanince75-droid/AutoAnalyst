"""Explicit operations against a persisted training run, never current-head inference."""

import streamlit as st

from ..domain.plans import AnalysisModuleId
from ..domain.runs import RunStatus


def render_model_audit(
    runtime, project_id, t, *, launch, make_spec, operation="audit_model", current_version=None
):
    candidates = []
    for run in runtime.run_store.list_runs(project_id):
        if run.status is not RunStatus.COMPLETED or not run.spec_id:
            continue
        original = runtime.run_store.get_spec(run.spec_id)
        if (
            original.module_id is AnalysisModuleId.BINARY_CLASSIFICATION
            and original.operation == "train_validate"
        ):
            candidates.append((run, original))
    if not candidates:
        st.info(t["train_first"])
        return
    run, original = st.selectbox(
        t["training_run"],
        candidates,
        format_func=lambda item: item[0].run_id[:8],
        key=f"audit_run_{operation}",
    )
    st.caption(t["audit_scope"])
    params = {"training_run_id": run.run_id}
    if operation == "audit_model":
        params["folds"] = st.number_input(t["folds"], min_value=2, max_value=5, value=3, step=1)
    if operation == "calibration_report":
        st.caption(t["calibration_scope"])
    if operation == "drift_report":
        saved = runtime.run_store.result_for_run(run.run_id)
        old_columns = {
            str(c["column_id"]): c for c in runtime.catalog.list_columns(original.input_version_id)
        }
        new_columns = {
            str(c["column_id"]): c
            for c in runtime.catalog.list_columns(current_version)
            if not c["is_system"]
        }
        params["feature_mapping"] = {}
        for feature in saved.provenance["feature_column_ids"]:
            old_name = old_columns[feature]["display_name"]
            choices = tuple(new_columns)
            default = next(
                (i for i, v in enumerate(choices) if new_columns[v]["display_name"] == old_name), 0
            )
            params["feature_mapping"][feature] = st.selectbox(
                str(old_name),
                choices,
                index=default,
                format_func=lambda c: str(new_columns[c]["display_name"]),
                key=f"drift_map_{feature}",
            )
        st.caption(t["drift_scope"])
    if st.button(t["run"], key=f"run_{operation}"):
        launch(
            runtime,
            make_spec(
                project_id,
                current_version if operation == "drift_report" else original.input_version_id,
                AnalysisModuleId.BINARY_CLASSIFICATION,
                operation,
                params,
                (),
            ),
            parent_run_id=run.run_id,
        )
