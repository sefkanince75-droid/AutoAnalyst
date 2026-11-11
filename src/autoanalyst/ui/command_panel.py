"""Command proposals remain inspectable and require separate preview/apply actions."""

import streamlit as st

from ..application.commands import Clarification, CommandService
from ..application.planner import HTTPPlanner, propose_with_fallback
from ..domain.codec import fingerprint, to_primitive
from ..execution.data_tasks import DataTaskService
from ..localization import I18N, error_text


def render_command_panel(runtime, project, dataset, version, language):
    t = I18N[language]
    service = CommandService(runtime.catalog, runtime.artifact_store)
    with st.expander(t["command_title"]):
        st.caption(t["command_examples"])
        text = st.text_input(t["command_input"], max_chars=1000, key="command_text")
        provider = None
        consent = False
        if st.checkbox(t["planner_enable"], key="planner_enabled"):
            endpoint = st.text_input(
                t["planner_endpoint"],
                value="http://127.0.0.1:11434/api/chat",
                key="planner_endpoint",
            )
            model = st.text_input(t["planner_model"], key="planner_model")
            tokens = st.number_input(
                t["planner_tokens"],
                min_value=32,
                max_value=2048,
                value=512,
                step=32,
                key="planner_tokens",
            )
            permission = fingerprint(
                {
                    "text": text,
                    "endpoint": endpoint,
                    "model": model,
                    "tokens": tokens,
                    "version": version.version_id,
                }
            )
            consent = st.checkbox(
                t["planner_consent"].format(endpoint=endpoint), key="planner_consent_" + permission
            )
            st.caption(t["planner_scope"])
            if model.strip():
                try:
                    provider = HTTPPlanner(endpoint, model, max_tokens=tokens)
                except Exception as exc:
                    st.error(error_text(exc, language))
        context = fingerprint(
            {
                "workspace": str(runtime.catalog.paths.root),
                "project": project.project_id,
                "dataset": dataset.dataset_id,
                "version": version.version_id,
                "revision": dataset.head_revision,
                "text": text,
            }
        )
        if st.session_state.get("command_context") != context:
            st.session_state.pop("command_plan", None)
            st.session_state.pop("command_preview", None)
            st.session_state.pop("command_task", None)
            st.session_state.command_context = context
        if st.button(t["command_plan"], key="make_command_plan", disabled=not text.strip()):
            st.session_state.pop("command_plan", None)
            st.session_state.pop("command_preview", None)
            try:
                proposal, fallback = propose_with_fallback(
                    service,
                    text,
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    version_id=version.version_id,
                    provider=provider,
                    consent=consent,
                )
                if provider and fallback:
                    st.info(t["planner_fallback"])
                if isinstance(proposal, Clarification):
                    st.info(t[proposal.reason])
                    if proposal.choices:
                        st.write(list(proposal.choices))
                else:
                    st.session_state.command_plan = proposal
            except Exception as exc:
                st.error(error_text(exc, language))
        plan = st.session_state.get("command_plan")
        if plan:
            names = {
                str(c["column_id"]): str(c["display_name"])
                for c in runtime.catalog.list_columns(version.version_id)
            }
            for index, step in enumerate(plan.steps, 1):
                from ..localization import label

                parameters = {
                    label(k, language): (
                        [names.get(v, v) for v in value]
                        if k == "column_ids"
                        else names.get(value, value)
                        if k == "column_id"
                        else to_primitive(value)
                    )
                    for k, value in step["parameters"].items()
                }
                st.write(f"{index}. {label(step['operation'], language)}")
                st.write(parameters)
            if pending := st.session_state.get("command_task"):
                state = DataTaskService(runtime.catalog).poll(pending)
                if state["status"] == "completed":
                    st.session_state.command_preview = state["result_id"]
                    st.session_state.pop("command_task", None)
                elif state["status"] in {"failed", "cancelled"}:
                    st.error(t["data_task_" + state["status"]])
                    st.session_state.pop("command_task", None)
            if st.button(t["preview"], key="preview_command"):
                try:
                    if (
                        version.row_count >= 100_000
                        or version.row_count * version.column_count >= 1_000_000
                    ):
                        recipe = service.recipe_for(plan)
                        identifier = DataTaskService(runtime.catalog).start(
                            operation="preparation",
                            project_id=project.project_id,
                            dataset_id=dataset.dataset_id,
                            recipe_id=recipe.recipe_id,
                        )
                        st.session_state.command_task = identifier
                        st.session_state.data_task_id = identifier
                        st.session_state.data_task_context = plan.proposal_hash
                        st.rerun()
                    else:
                        preview = service.preview(plan)
                        st.session_state.command_preview = preview.preview_id
                except Exception as exc:
                    st.error(error_text(exc, language))
            if preview_id := st.session_state.get("command_preview"):
                from ..application.preparation import PreparationService

                preview = PreparationService(runtime.catalog, runtime.artifact_store).get_preview(
                    preview_id
                )
                st.caption(f"{preview.before_row_count} → {preview.after_row_count}")
                before, after = PreparationService(
                    runtime.catalog, runtime.artifact_store
                ).preview_samples(preview_id)
                left, right = st.columns(2)
                left.dataframe(before.to_pandas(), hide_index=True)
                right.dataframe(after.to_pandas(), hide_index=True)
                if st.button(t["apply"], key="apply_command"):
                    try:
                        service.apply(plan, preview_id)
                        st.session_state.pop("command_plan", None)
                        st.session_state.pop("command_preview", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(error_text(exc, language))
