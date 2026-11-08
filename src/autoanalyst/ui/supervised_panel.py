"""Separate regression and multiclass workflows with explicit final-test access."""

import streamlit as st

from ..domain.datasets import ColumnRole, ColumnUsage
from ..domain.runs import RunStatus
from ..storage.binary import BinaryStore
from .typed_inputs import input_kind


def render_supervised(
    runtime, project_id, version_id, by_name, t, *, module_id, launch, make_spec, semantic
):
    prefix = module_id.value
    action = st.selectbox(
        t["model_action"],
        ("train_validate", "final_evaluate", "score_new_data"),
        format_func=lambda v: t["action_" + v],
        key=prefix + "_action",
    )
    if action == "train_validate":
        targets = tuple(
            n
            for n, c in by_name.items()
            if prefix != "regression" or input_kind(c) in {"integer", "numeric"}
        )
        if not targets:
            st.info(t["no_numeric_columns"])
            return
        target = st.selectbox(t["target"], targets, key=prefix + "_target")
        features = st.multiselect(
            t["features"], tuple(n for n in by_name if n != target), key=prefix + "_features"
        )
        default = "random" if prefix == "regression" else "stratified"
        policy = st.selectbox(t["split"], (default, "group", "time"), key=prefix + "_split")
        extra = (
            st.selectbox(
                t["group_column"] if policy == "group" else t["time_column"],
                tuple(by_name),
                key=prefix + "_split_column",
            )
            if policy in {"group", "time"}
            else None
        )
        available = st.checkbox(t["features_available"], key=prefix + "_available")
        if st.button(t["run"], key=prefix + "_train", disabled=not features or not available):
            roles = [
                ColumnRole(
                    str(by_name[n]["column_id"]),
                    semantic(by_name[n]),
                    (ColumnUsage.FEATURE,),
                    True,
                    True,
                )
                for n in features
            ]
            roles.append(
                ColumnRole(
                    str(by_name[target]["column_id"]),
                    semantic(by_name[target]),
                    (ColumnUsage.TARGET,),
                    True,
                )
            )
            if extra:
                if extra in features or extra == target:
                    st.error(t["split_column_distinct"])
                    return
                roles.append(
                    ColumnRole(
                        str(by_name[extra]["column_id"]),
                        semantic(by_name[extra]),
                        (ColumnUsage.GROUP if policy == "group" else ColumnUsage.TIME,),
                        True,
                    )
                )
            launch(
                runtime,
                make_spec(
                    project_id,
                    version_id,
                    module_id,
                    action,
                    {"split_policy": policy},
                    tuple(roles),
                ),
            )
        return
    candidates = []
    for run in runtime.run_store.list_runs(project_id):
        if run.status is not RunStatus.COMPLETED or not run.spec_id:
            continue
        original = runtime.run_store.get_spec(run.spec_id)
        saved = runtime.run_store.result_for_run(run.run_id)
        if (
            original.module_id is module_id
            and original.operation == "train_validate"
            and saved
            and saved.provenance.get("recommended_model")
        ):
            candidates.append((run, original, saved))
    if not candidates:
        st.info(t["no_candidate"])
        return
    run, original, saved = st.selectbox(
        t["training_run"],
        candidates,
        format_func=lambda v: v[0].run_id[:8],
        key=prefix + "_training_run",
    )
    lock = BinaryStore(runtime.catalog).get_holdout_lock(run.run_id)
    params = {"training_run_id": run.run_id}
    if action == "score_new_data":
        if not lock or lock["status"] != "reported":
            st.info(t["finalize_first"])
            return
        old = {
            str(c["column_id"]): c for c in runtime.catalog.list_columns(original.input_version_id)
        }
        params["feature_mapping"] = {}
        for feature in saved.provenance["feature_column_ids"]:
            name = old[feature]["display_name"]
            names = tuple(by_name)
            selected = st.selectbox(
                str(name),
                names,
                index=names.index(name) if name in names else 0,
                key=f"{prefix}_map_{feature}",
            )
            params["feature_mapping"][feature] = str(by_name[selected]["column_id"])
    if st.button(
        t["run"],
        key=prefix + "_evaluate",
        disabled=action == "final_evaluate" and bool(lock and lock["status"] == "reported"),
    ):
        launch(
            runtime,
            make_spec(
                project_id,
                original.input_version_id if action == "final_evaluate" else version_id,
                module_id,
                action,
                params,
                (),
            ),
            parent_run_id=run.run_id,
        )
