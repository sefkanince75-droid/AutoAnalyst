"""Automatic, restart-safe monitoring backed by workspace run/event records."""

from datetime import datetime

import pandas as pd
import streamlit as st

from ..domain.codec import utc_now
from ..domain.runs import RunStatus
from ..localization import I18N, failure_text, label

ACTIVE = {RunStatus.PENDING, RunStatus.RUNNING}


def _stage(stage, language):
    return I18N[language].get("stage." + str(stage), I18N[language]["stage.analysis"])


def _progress_text(payload, language):
    fraction = payload.get("fraction")
    if (
        isinstance(fraction, bool)
        or not isinstance(fraction, (int, float))
        or not 0 <= fraction <= 1
    ):
        return ""
    return I18N[language]["stage_percent"].format(percent=round(fraction * 100))


def render_events(runtime, run_id: str, language: str) -> None:
    t = I18N[language]
    events = runtime.run_store.list_events(run_id, limit=100)
    if not events:
        st.caption(t["no_events"])
        return
    rows = []
    for event in events:
        payload = event["payload"]
        rows.append(
            {
                t["event_time"]: event["created_at"],
                t["event_type"]: t.get("event." + event["event_type"], t["event.other"]),
                t["event_stage"]: _stage(event["stage"], language),
                t["event_progress"]: _progress_text(payload, language),
                t["event_detail"]: failure_text(
                    payload.get("error_code"), payload.get("reason"), language
                )
                if payload.get("error_code")
                else label(payload.get("model_id", ""), language)
                if payload.get("model_id") in {"dummy", "logistic_regression", "random_forest"}
                else "",
            }
        )
    st.caption(t["events_limit"])
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _remember_terminal(run, project_id):
    st.session_state.last_run_notice = run.run_id
    if run.project_id == project_id and run.status is RunStatus.COMPLETED:
        st.session_state.last_result_id = run.result_id
        st.session_state.pending_workspace_tab = "results"
    st.session_state.pop("active_run_id", None)


def render_run_monitor(runtime, project_id: str, language: str) -> None:
    active = runtime.run_store.active_analysis_runs()
    tracked = st.session_state.get("active_run_id")
    if tracked:
        run = runtime.run_store.get_run(tracked)
        if run.status not in ACTIVE:
            _remember_terminal(run, project_id)
    t = I18N[language]
    notice_id = st.session_state.get("last_run_notice")
    if notice_id:
        notice = runtime.run_store.get_run(notice_id)
        owner = runtime.catalog.get_project(notice.project_id)
        st.caption(t["run_owner"].format(project=owner.name, run=notice.run_id[:8]))
        if notice.project_id == project_id:
            if notice.status is RunStatus.COMPLETED:
                st.session_state.last_result_id = notice.result_id
                st.success(t["run_completed_notice"].format(run=notice.run_id[:8]))
            elif notice.status is RunStatus.CANCELLED:
                st.warning(t["cancelled"])
            else:
                st.error(run_failure_text(runtime, notice, language))
            if st.button(t["dismiss_notice"], key="dismiss_run_notice"):
                st.session_state.pop("last_run_notice", None)
                st.rerun()
        else:
            st.info(t["other_project_run"] + " · " + label(notice.status.value, language))
    if not active:
        return
    choices = {run.run_id: run for run in active}
    tracked = st.session_state.get("active_run_id")
    if tracked not in choices:
        tracked = next(
            (run.run_id for run in active if run.status is RunStatus.RUNNING), None
        ) or next((run.run_id for run in active if run.project_id == project_id), active[0].run_id)
        st.session_state.active_run_id = tracked
    if len(choices) > 1:
        if st.session_state.get("monitor_run") not in choices:
            st.session_state.monitor_run = tracked
        run_labels = {
            value: f"{runtime.catalog.get_project(run.project_id).name} · {value[:8]}"
            for value, run in choices.items()
        }
        st.session_state.active_run_id = st.selectbox(
            t["active_analysis"],
            tuple(choices),
            key="monitor_run",
            format_func=run_labels.__getitem__,
        )

    @st.fragment(run_every=1.0)
    def poll():
        run_id = st.session_state.get("active_run_id", tracked)
        current = runtime.run_store.get_run(run_id)
        if current.status is RunStatus.RUNNING:
            current = runtime.coordinator.reconcile_worker(run_id, runtime.catalog.paths.root)
        if current.status not in ACTIVE:
            _remember_terminal(current, project_id)
            st.rerun(scope="app")
        with st.container(border=True):
            st.subheader(t["active_analysis"])
            project = runtime.catalog.get_project(current.project_id)
            spec = runtime.run_store.get_spec(current.spec_id) if current.spec_id else None
            st.caption(t["run_owner"].format(project=project.name, run=current.run_id[:8]))
            if current.project_id != project_id:
                st.info(t["other_project_run"])
            if spec:
                st.write(label(spec.operation, language))
            st.write(label(current.status.value, language))
            elapsed = max(
                0, int((utc_now() - (current.started_at or current.created_at)).total_seconds())
            )
            st.caption(t["run_elapsed"].format(seconds=elapsed))
            events = runtime.run_store.list_events(run_id, limit=100)
            progress = next(
                (event for event in reversed(events) if event["event_type"] == "progress"), None
            )
            if progress and _progress_text(progress["payload"], language):
                st.progress(
                    float(progress["payload"]["fraction"]),
                    text=_stage(progress["stage"], language)
                    + " · "
                    + _progress_text(progress["payload"], language),
                )
                st.caption(t["stage_only_hint"])
                stamp = datetime.fromisoformat(progress["created_at"].replace("Z", "+00:00"))
                age = max(0, int((utc_now() - stamp).total_seconds()))
                st.caption(t["last_progress_age"].format(seconds=age))
                if age >= 15:
                    st.info(t["progress_quiet"])
            else:
                st.info(t["progress_waiting"])
            st.caption(t["automatic_refresh"])
            left, right = st.columns(2)
            if left.button(t["refresh"], key="refresh_active_run"):
                st.rerun(scope="app")
            if right.button(t["cancel"], key="cancel_active_run"):
                runtime.coordinator.request_cancel(run_id, runtime.catalog.paths.root)
                _remember_terminal(runtime.run_store.get_run(run_id), project_id)
                st.rerun(scope="app")
            with st.expander(t["run_events"]):
                render_events(runtime, run_id, language)

    poll()


def run_failure_text(runtime, run, language):
    events = runtime.run_store.list_events(run.run_id, limit=100)
    reason = next(
        (
            event["payload"].get("reason")
            for event in reversed(events)
            if event["event_type"] == "failed"
        ),
        None,
    )
    return failure_text(run.error_code, reason, language)
