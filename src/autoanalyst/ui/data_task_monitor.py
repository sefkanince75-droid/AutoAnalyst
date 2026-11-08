"""Reconnectable resource-task status, sampled peaks and cancellation controls."""

import streamlit as st

from ..domain.errors import ResourceError
from ..execution.data_tasks import DataTaskService, active_data_task
from ..localization import I18N, error_text


def render_data_task_monitor(runtime, language):
    service = DataTaskService(runtime.catalog)
    t = I18N[language]

    @st.fragment(run_every=1.0)
    def panel():
        active = active_data_task(runtime.catalog.paths.root)
        with st.expander(t["data_task_panel"], expanded=bool(active)):
            st.caption(t["data_limits_hint"])
            try:
                job_id = st.session_state.get("data_task_id") or service.latest()
                if not job_id:
                    return
                state = service.poll(job_id)
                st.caption(f"{state['operation']} · {job_id[:8]}")
                st.write(t["data_task_" + state["status"]])
                st.caption(
                    t["data_task_metrics"].format(
                        seconds=state.get("elapsed_seconds", 0),
                        rss=state.get("peak_rss_bytes", 0) / 1024**2,
                        disk=state.get("disk_growth_bytes", 0) / 1024**2,
                    )
                )
                if state["status"] == "running":
                    if st.button(t["data_task_cancel"], key="cancel_data_task"):
                        service.cancel(job_id)
                        st.info(t["data_task_cancelling"])
                else:
                    if state["status"] == "failed":
                        st.error(
                            error_text(
                                ResourceError({"reason": state.get("reason", "data_task_failed")}),
                                language,
                            )
                        )
                    if state["status"] == "completed":
                        st.text(state["result_id"])
                    if st.session_state.get("data_task_delivered") != job_id:
                        st.session_state.data_task_delivery = {
                            **state,
                            "context": st.session_state.get("data_task_context"),
                        }
                        st.session_state.data_task_delivered = job_id
                        st.rerun()
            except Exception as exc:
                st.error(error_text(exc, language))

    with st.sidebar:
        panel()
