"""Thin Streamlit V2 UI: Data -> Analysis -> Results -> History."""

from __future__ import annotations

from collections import Counter
from uuid import uuid4

import pandas as pd
import streamlit as st

from ..application.datasets import DatasetService
from ..application.projects import ProjectService
from ..bootstrap import build_runtime
from ..data import TableAccess
from ..domain.codec import fingerprint, utc_now
from ..domain.datasets import ColumnRole, ColumnUsage, SemanticType
from ..domain.errors import MethodNotApplicableError, SchemaError
from ..domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from ..domain.runs import RunStatus
from ..localization import I18N, cell_text, error_text, label
from ..reporting import ReportService
from ..reporting.render import TEMPLATE_VERSION
from ..storage.binary import BinaryStore
from .command_panel import render_command_panel
from .data_browser import render_data_browser
from .data_export import render_data_export
from .data_import import render_import_panel
from .data_task_monitor import render_data_task_monitor
from .help_panel import render_help
from .interval_form import render_interval_form
from .model_audit import render_model_audit
from .preparation_panel import render_preparation_panel as _preparation_panel
from .project_management import render_archived_projects, render_project_management
from .recipe_editor import clear_draft as _clear_draft
from .result_view import render_result
from .run_monitor import render_events, render_run_monitor
from .storage_maintenance import render_storage_maintenance
from .supervised_panel import render_supervised
from .typed_inputs import input_kind
from .version_navigation import render_version_navigation
from .workspace_backups import render_workspace_backups


def main() -> None:
    st.set_page_config(page_title="AutoAnalyst V2", layout="wide")
    runtime = build_runtime(st.session_state.get("active_workspace"))
    projects = ProjectService(runtime.catalog)
    if "language" not in st.session_state:
        available = projects.list()
        selected = next(
            (p for p in available if p.project_id == st.session_state.get("project_id")),
            available[0] if available else None,
        )
        st.session_state.language = selected.default_language if selected else "en"
    language = st.sidebar.selectbox(
        "Language / Dil", ("en", "tr"), key="language", on_change=_remember_language_override
    )
    t = I18N[language]
    st.title(t["title"])
    st.caption(t["subtitle"])

    datasets = DatasetService(runtime.catalog)
    render_workspace_backups(runtime, language)
    render_storage_maintenance(runtime, language)
    render_data_task_monitor(runtime, language)
    render_help(runtime, language)
    render_archived_projects(runtime, language)
    project = _select_project(projects, language)
    if project is None:
        render_run_monitor(runtime, "", language)
        _clear_draft()
        st.session_state.pop("report_id", None)
        st.session_state.pop("report_context", None)
        st.info(t["no_project"])
        return
    render_project_management(runtime, project, language)
    dataset = _select_dataset(datasets, project.project_id, language)
    render_run_monitor(runtime, project.project_id, language)

    tabs = st.tabs((t["data"], t["analysis"], t["results"], t["history"]))
    with tabs[0]:
        _data_tab(runtime, project, dataset, datasets, language)
    with tabs[1]:
        _analysis_tab(runtime, project, dataset, datasets, language)
    with tabs[2]:
        _results_tab(runtime, project, language)
    with tabs[3]:
        _history_tab(runtime, project, dataset, datasets, language)


def _remember_language_override() -> None:
    st.session_state.language_override = True


def _project_language_changed() -> None:
    project = st.session_state.project_selector
    st.session_state.project_id = project.project_id
    if not st.session_state.get("language_override", False):
        st.session_state.language = project.default_language


def _select_project(service: ProjectService, language: str):
    t = I18N[language]
    projects = service.list()
    with st.sidebar.expander(t["create_project"], expanded=not projects):
        name = st.text_input(t["name"], key="new_project_name")
        if st.button(t["create_project"], key="create_project") and name.strip():
            created = service.create(name.strip(), default_language=language)
            st.session_state.project_id = created.project_id
            st.session_state.project_selector = created
            st.rerun()
    if not projects:
        return None
    ids = [item.project_id for item in projects]
    selected_id = st.session_state.get("project_id")
    index = ids.index(selected_id) if selected_id in ids else 0
    name_counts = Counter(item.name for item in projects)
    project_labels = {
        item.project_id: item.name
        if name_counts[item.name] == 1
        else f"{item.name} · {item.project_id[:8]}"
        for item in projects
    }
    selected = st.sidebar.selectbox(
        t["project"],
        projects,
        index=index,
        format_func=lambda item: project_labels[item.project_id],
        key="project_selector",
        on_change=_project_language_changed,
    )
    st.session_state.project_id = selected.project_id
    return selected


def _select_dataset(service: DatasetService, project_id: str, language: str):
    t = I18N[language]
    datasets = service.list(project_id)
    with st.sidebar.expander(t["create_dataset"]):
        name = st.text_input(t["name"], key="new_dataset_name")
        if st.button(t["create_dataset"], key="create_dataset") and name.strip():
            created = service.create(project_id, name.strip())
            st.session_state.dataset_id = created.dataset_id
            st.rerun()
    if not datasets:
        return None
    ids = [item.dataset_id for item in datasets]
    selected_id = st.session_state.get("dataset_id")
    index = ids.index(selected_id) if selected_id in ids else 0
    selected = st.sidebar.selectbox(
        t["dataset"], datasets, index=index, format_func=lambda item: item.name
    )
    st.session_state.dataset_id = selected.dataset_id
    return selected


def _data_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    t = I18N[language]
    if dataset is None:
        _clear_draft()
        st.info(t["no_dataset"])
        return
    render_import_panel(runtime, project, dataset, language, t)

    version = datasets.current_version(dataset.dataset_id)
    if version is None:
        _clear_draft()
        st.info(t["no_data"])
        return
    st.success(
        t["dataset_summary"].format(
            rows=version.row_count, columns=version.column_count, version=version.version_id[:8]
        )
    )
    columns = _user_columns(runtime, version.version_id)
    render_data_browser(runtime, version, columns, t)
    render_data_export(runtime, project, dataset, version, language)
    render_command_panel(runtime, project, dataset, version, language)
    with st.expander(t["column_schema"]):
        st.dataframe(
            pd.DataFrame(
                {
                    t["column_name"]: [column["display_name"] for column in columns],
                    t["stored_type"]: [column["physical_type"] for column in columns],
                }
            ),
            use_container_width=True,
            hide_index=True,
        )
    _preparation_panel(runtime, project.project_id, version.version_id, columns, language)


def _analysis_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    t = I18N[language]
    if dataset is None:
        st.info(t["no_dataset"])
        return
    version = datasets.current_version(dataset.dataset_id)
    if version is None:
        st.info(t["no_data"])
        return
    columns = _user_columns(runtime, version.version_id)
    by_name = {str(row["display_name"]): row for row in columns}
    choices = (
        "Data profile",
        "Distribution",
        "Group summary",
        "Crosstab",
        "Correlation",
        "Welch two groups",
        "Fisher 2x2",
        "Binary classification",
        "Final holdout",
        "Score new data",
        "Model audit",
        "Confidence interval",
        "Calibration report",
        "Data drift",
        "Regression",
        "Multiclass classification",
    )
    task = st.selectbox(
        t["analysis"],
        choices,
        key="analysis_task",
        format_func=lambda value: label(value, language),
    )
    try:
        if task == "Data profile":
            if st.button(t["run"], key="run_profile"):
                _launch(
                    runtime,
                    _spec(
                        project.project_id,
                        version.version_id,
                        AnalysisModuleId.PROFILING,
                        "profile",
                        {},
                        (),
                    ),
                )
        elif task == "Distribution":
            name = st.selectbox(t["column"], tuple(by_name), key="dist_col")
            if st.button(t["run"], key="run_distribution"):
                _launch(
                    runtime,
                    _spec(
                        project.project_id,
                        version.version_id,
                        AnalysisModuleId.COMPARISON,
                        "distribution",
                        {"column_id": str(by_name[name]["column_id"])},
                        (),
                    ),
                )
        elif task == "Group summary":
            numeric_names = tuple(
                name
                for name, column in by_name.items()
                if input_kind(column) in {"integer", "numeric"}
            )
            if not numeric_names:
                st.info(t["no_numeric_columns"])
                return
            groups = st.multiselect(
                t["group_columns"], tuple(by_name), max_selections=2, key="summary_groups"
            )
            measures = st.multiselect(
                t["measures"], numeric_names, max_selections=5, key="summary_measures"
            )
            if st.button(t["run"], key="run_group_summary"):
                params = {
                    "group_columns": [str(by_name[n]["column_id"]) for n in groups],
                    "measure_columns": [str(by_name[n]["column_id"]) for n in measures],
                }
                _launch(
                    runtime,
                    _spec(
                        project.project_id,
                        version.version_id,
                        AnalysisModuleId.COMPARISON,
                        "group_summary",
                        params,
                        (),
                    ),
                )
        elif task == "Crosstab":
            row_name = st.selectbox(t["row"], tuple(by_name), key="xtab_row")
            col_name = st.selectbox(
                t["column"], tuple(by_name), key="xtab_col", index=min(1, len(by_name) - 1)
            )
            if st.button(t["run"], key="run_crosstab"):
                params = {
                    "row_column_id": str(by_name[row_name]["column_id"]),
                    "column_column_id": str(by_name[col_name]["column_id"]),
                }
                _launch(
                    runtime,
                    _spec(
                        project.project_id,
                        version.version_id,
                        AnalysisModuleId.COMPARISON,
                        "crosstab",
                        params,
                        (),
                    ),
                )
        elif task == "Correlation":
            numeric_names = tuple(
                name
                for name, column in by_name.items()
                if input_kind(column) in {"integer", "numeric"}
            )
            if not numeric_names:
                st.info(t["no_numeric_columns"])
                return
            x = st.selectbox("X", numeric_names, key="corr_x")
            y = st.selectbox("Y", numeric_names, key="corr_y", index=min(1, len(numeric_names) - 1))
            method = st.selectbox(t["method"], ("pearson", "spearman"))
            independence = st.checkbox(t["independence"], key="correlation_independence")
            if st.button(t["run"], key="run_corr"):
                params = {
                    "x_column_id": str(by_name[x]["column_id"]),
                    "y_column_id": str(by_name[y]["column_id"]),
                    "method": method,
                    "independence_confirmed": independence,
                }
                _launch(
                    runtime,
                    _spec(
                        project.project_id,
                        version.version_id,
                        AnalysisModuleId.COMPARISON,
                        "correlation",
                        params,
                        (),
                    ),
                )
        elif task == "Welch two groups":
            _welch_form(runtime, project.project_id, version.version_id, by_name, t)
        elif task == "Fisher 2x2":
            _fisher_form(runtime, project.project_id, version.version_id, by_name, t)
        elif task == "Binary classification":
            _binary_form(runtime, project.project_id, version.version_id, by_name, t)
        elif task == "Final holdout":
            _final_form(runtime, project.project_id, t)
        elif task == "Model audit":
            render_model_audit(runtime, project.project_id, t, launch=_launch, make_spec=_spec)
        elif task in {"Regression", "Multiclass classification"}:
            render_supervised(
                runtime,
                project.project_id,
                version.version_id,
                by_name,
                t,
                module_id=AnalysisModuleId.REGRESSION
                if task == "Regression"
                else AnalysisModuleId.MULTICLASS_CLASSIFICATION,
                launch=_launch,
                make_spec=_spec,
                semantic=_semantic,
            )
        elif task in {"Calibration report", "Data drift"}:
            render_model_audit(
                runtime,
                project.project_id,
                t,
                launch=_launch,
                make_spec=_spec,
                operation="calibration_report" if task == "Calibration report" else "drift_report",
                current_version=version.version_id,
            )
        elif task == "Confidence interval":
            render_interval_form(
                runtime,
                project.project_id,
                version.version_id,
                by_name,
                t,
                launch=_launch,
                make_spec=_spec,
                distinct=_distinct_values,
            )
        else:
            _score_form(runtime, project.project_id, version.version_id, by_name, t)
    except Exception as exc:
        st.error(error_text(exc, language))


def _welch_form(
    runtime,
    project_id: str,
    version_id: str,
    by_name: dict[str, dict[str, object]],
    t: dict[str, str],
) -> None:
    group_name = st.selectbox(t["group_column"], tuple(by_name), key="welch_group")
    numeric_names = tuple(
        name for name, column in by_name.items() if input_kind(column) in {"integer", "numeric"}
    )
    if not numeric_names:
        st.info(t["no_numeric_columns"])
        return
    measure_name = st.selectbox(t["numeric_measure"], numeric_names, key="welch_measure")
    values = _distinct_values(runtime, version_id, by_name[group_name])
    if len(values) < 2:
        st.warning(t["two_groups_required"])
        return
    group_a = st.selectbox(t["group_a"], values, key="welch_a", format_func=repr)
    group_b = st.selectbox(
        t["group_b"], values, key="welch_b", index=min(1, len(values) - 1), format_func=repr
    )
    independent = st.checkbox(
        t["independence"], key=f"welch_independent_{version_id}", help=t["independence_hint"]
    )
    if group_a == group_b:
        st.info(t["distinct_groups"])
    if st.button(t["run"], key="run_welch", disabled=not independent or group_a == group_b):
        params = {
            "group_column_id": str(by_name[group_name]["column_id"]),
            "measure_column_id": str(by_name[measure_name]["column_id"]),
            "group_a": group_a,
            "group_b": group_b,
            "independence_confirmed": independent,
        }
        _launch(
            runtime,
            _spec(
                project_id, version_id, AnalysisModuleId.COMPARISON, "welch_two_groups", params, ()
            ),
        )


def _fisher_form(
    runtime,
    project_id: str,
    version_id: str,
    by_name: dict[str, dict[str, object]],
    t: dict[str, str],
) -> None:
    row_name = st.selectbox(t["row_variable"], tuple(by_name), key="fisher_row")
    col_name = st.selectbox(
        t["column_variable"], tuple(by_name), key="fisher_col", index=min(1, len(by_name) - 1)
    )
    row_values = _distinct_values(runtime, version_id, by_name[row_name], scope="fisher_row")
    col_values = _distinct_values(runtime, version_id, by_name[col_name], scope="fisher_col")
    if len(row_values) != 2 or len(col_values) != 2:
        st.warning(t["fisher_categories"])
        return
    row_positive = st.selectbox(t["row_positive"], row_values, format_func=repr)
    col_positive = st.selectbox(t["column_positive"], col_values, format_func=repr)
    if st.button(t["run"], key="run_fisher"):
        params = {
            "row_column_id": str(by_name[row_name]["column_id"]),
            "column_column_id": str(by_name[col_name]["column_id"]),
            "row_positive": row_positive,
            "column_positive": col_positive,
        }
        _launch(
            runtime,
            _spec(project_id, version_id, AnalysisModuleId.COMPARISON, "fisher_2x2", params, ()),
        )


def _binary_form(
    runtime,
    project_id: str,
    version_id: str,
    by_name: dict[str, dict[str, object]],
    t: dict[str, str],
) -> None:
    target_name = st.selectbox(t["target"], tuple(by_name), key="binary_target")
    feature_names = st.multiselect(
        t["features"], tuple(name for name in by_name if name != target_name), key="binary_features"
    )
    target_id = str(by_name[target_name]["column_id"])
    labels = _distinct_values(runtime, version_id, by_name[target_name])
    positive = st.selectbox(t["positive_event"], labels, format_func=repr) if labels else None
    split = st.selectbox(
        t["split"],
        ("stratified", "group", "time"),
        key="binary_split",
        format_func=lambda value: label(value, t["locale"]),
    )
    group_name = (
        st.selectbox(t["group_column"], tuple(by_name), key="binary_group")
        if split == "group"
        else None
    )
    time_names = tuple(
        name for name, column in by_name.items() if input_kind(column) in {"date", "datetime"}
    )
    if split == "time" and not time_names:
        st.info(t["no_time_columns"])
        return
    time_name = (
        st.selectbox(t["time_column"], time_names, key="binary_time") if split == "time" else None
    )
    minimum_recall = st.slider(t["minimum_recall"], 0.05, 1.0, 0.85, 0.05)
    class_weight = st.selectbox(
        t["class_weight"],
        ("none", "balanced"),
        key="binary_weight",
        format_func=lambda value: label(value, t["locale"]),
    )
    if len(labels) != 2:
        st.info(t["binary_target_required"])
    if not feature_names:
        st.info(t["features_required"])
    if st.button(t["run"], key="run_binary", disabled=len(labels) != 2 or not feature_names):
        roles: list[ColumnRole] = [
            ColumnRole(
                str(by_name[name]["column_id"]),
                _semantic(by_name[name]),
                (ColumnUsage.FEATURE,),
                True,
                True,
            )
            for name in feature_names
        ]
        roles.append(
            ColumnRole(target_id, _semantic(by_name[target_name]), (ColumnUsage.TARGET,), True)
        )
        if group_name:
            roles.append(
                ColumnRole(
                    str(by_name[group_name]["column_id"]),
                    _semantic(by_name[group_name]),
                    (ColumnUsage.GROUP,),
                    True,
                )
            )
        if time_name:
            roles.append(
                ColumnRole(
                    str(by_name[time_name]["column_id"]),
                    _semantic(by_name[time_name]),
                    (ColumnUsage.TIME,),
                    True,
                )
            )
        params = {
            "positive_label": positive,
            "minimum_recall": minimum_recall,
            "split_policy": split,
            "class_weight_policy": class_weight,
        }
        _launch(
            runtime,
            _spec(
                project_id,
                version_id,
                AnalysisModuleId.BINARY_CLASSIFICATION,
                "train_validate",
                params,
                tuple(roles),
            ),
        )


def _training_candidates(runtime, project_id: str):
    output = []
    for run in runtime.run_store.list_runs(project_id):
        if run.status is not RunStatus.COMPLETED or not run.spec_id:
            continue
        spec = runtime.run_store.get_spec(run.spec_id)
        result = runtime.run_store.result_for_run(run.run_id)
        if (
            spec.module_id is AnalysisModuleId.BINARY_CLASSIFICATION
            and spec.operation == "train_validate"
            and result is not None
            and result.provenance.get("recommended_model")
        ):
            output.append((run, spec, result))
    return output


def _final_form(runtime, project_id: str, t: dict[str, str]) -> None:
    candidates = _training_candidates(runtime, project_id)
    if not candidates:
        st.info(t["no_candidate"])
        return
    selected = st.selectbox(
        t["training_run"],
        candidates,
        format_func=lambda item: f"{item[0].created_at.isoformat()} · {label(item[2].provenance['recommended_model']['model_id'], t['locale'])}",
    )
    run, training_spec, _ = selected
    lock = BinaryStore(runtime.catalog).get_holdout_lock(run.run_id)
    if lock:
        st.info(f"{t['holdout']}: {label(lock['status'], t['locale'])}")
    if st.button(
        t["open_holdout"],
        key="open_holdout",
        disabled=bool(lock and lock["status"] == "reported"),
    ):
        spec = _spec(
            project_id,
            training_spec.input_version_id,
            AnalysisModuleId.BINARY_CLASSIFICATION,
            "final_evaluate",
            {"training_run_id": run.run_id},
            training_spec.column_roles,
        )
        _launch(runtime, spec, parent_run_id=run.run_id)


def _score_form(
    runtime,
    project_id: str,
    version_id: str,
    by_name: dict[str, dict[str, object]],
    t: dict[str, str],
) -> None:
    candidates = _training_candidates(runtime, project_id)
    if not candidates:
        st.info(t["train_first"])
        return
    candidates = [
        item
        for item in candidates
        if (
            (lock := BinaryStore(runtime.catalog).get_holdout_lock(item[0].run_id))
            and lock["status"] == "reported"
        )
    ]
    if not candidates:
        st.info(t["finalize_first"])
        return
    selected = st.selectbox(
        t["model_run"],
        candidates,
        format_func=lambda item: f"{item[0].created_at.isoformat()} · {label(item[2].provenance['recommended_model']['model_id'], t['locale'])}",
    )
    run, training_spec, result = selected
    training_columns = {
        str(row["column_id"]): row
        for row in runtime.catalog.list_columns(training_spec.input_version_id)
    }
    mapping: dict[str, str] = {}
    for training_id in result.provenance["feature_column_ids"]:
        training_id = str(training_id)
        name = str(training_columns[training_id]["display_name"])
        choices = tuple(by_name)
        default = choices.index(name) if name in choices else 0
        selected_name = st.selectbox(
            f"{name} →", choices, index=default, key=f"score_map_{training_id}"
        )
        mapping[training_id] = str(by_name[selected_name]["column_id"])
    if st.button(t["score"], key="run_scoring"):
        spec = _spec(
            project_id,
            version_id,
            AnalysisModuleId.BINARY_CLASSIFICATION,
            "score_new_data",
            {"training_run_id": run.run_id, "feature_mapping": mapping},
            (),
        )
        _launch(runtime, spec, parent_run_id=run.run_id)


def _results_tab(runtime, project, language: str) -> None:
    t = I18N[language]
    runs = [
        run
        for run in runtime.run_store.list_runs(project.project_id)
        if run.status is RunStatus.COMPLETED and run.result_id
    ]
    if not runs:
        st.session_state.pop("report_id", None)
        st.session_state.pop("report_context", None)
        st.info(t["no_results"])
        return
    default_id = st.session_state.get("last_result_id")
    default_index = next(
        (i for i, run in enumerate(runs) if run.result_id == default_id), len(runs) - 1
    )
    run = st.selectbox(
        t["result_run"],
        runs,
        index=default_index,
        format_func=lambda item: f"{item.created_at.isoformat()} · {label(runtime.run_store.get_spec(item.spec_id).operation if item.spec_id else item.kind.value, language)}",
    )
    result = runtime.run_store.get_result(run.result_id)
    render_result(result, language)
    st.subheader(t["export"])
    fmt = st.selectbox(t["format"], ("html", "markdown", "json", "csv", "pdf", "xlsx"))
    report_language = st.selectbox(
        t["report_language"], ("en", "tr"), index=0 if language == "en" else 1
    )
    service = ReportService(runtime.catalog, runtime.artifact_store)
    report_context = fingerprint(
        {
            "project_id": project.project_id,
            "result_id": result.result_id,
            "format": fmt,
            "language": report_language,
            "template_version": TEMPLATE_VERSION,
            "options_hash": fingerprint({}),
        }
    )
    if st.session_state.get("report_context") != report_context:
        st.session_state.pop("report_id", None)
        st.session_state.pop("report_context", None)
    if st.button(t["export"], key="build_report"):
        report = service.render(result.result_id, format=fmt, language=report_language)
        st.session_state.report_id = report.report_id
        st.session_state.report_context = report_context
    if report_id := st.session_state.get("report_id"):
        try:
            report = service.get(report_id)
            artifact = runtime.catalog.get_artifact(report.artifact_id)
            if (
                report.root_result_id != result.result_id
                or report.format != fmt
                or report.language != report_language
                or report.template_version != TEMPLATE_VERSION
                or report.render_options_hash != fingerprint({})
                or artifact.project_id != project.project_id
            ):
                raise SchemaError({"reason": "report_context_mismatch"})
            extension = (
                "zip"
                if report.format == "csv"
                else "md"
                if report.format == "markdown"
                else report.format
            )
            st.download_button(
                t["download"],
                service.artifact_bytes(report),
                file_name=f"autoanalyst-{report.root_result_id[:8]}.{extension}",
            )
        except Exception:
            st.session_state.pop("report_id", None)
            st.session_state.pop("report_context", None)


def _history_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    t = I18N[language]
    runs = runtime.run_store.list_runs(project.project_id)
    st.subheader(t["run_history"])
    st.dataframe(
        pd.DataFrame(
            [
                {
                    label("run_id", language): run.run_id,
                    t["value.status"]: label(run.status.value, language),
                    t["value.created_at"]: run.created_at,
                    label("result_id", language): run.result_id,
                    t["failed"]: t.get(run.error_code, t["generic_error"])
                    if run.error_code
                    else "",
                }
                for run in runs
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    if runs:
        run_labels = {
            run.run_id: f"{run.run_id[:8]} · {label(run.status.value, language)}" for run in runs
        }
        selected_run = st.selectbox(
            t["run_events"],
            [run.run_id for run in reversed(runs)],
            format_func=run_labels.__getitem__,
            key=f"history_event_run_{project.project_id}",
        )
        with st.expander(t["run_events"]):
            render_events(runtime, selected_run, language)
    if dataset is not None:
        st.subheader(t["dataset_versions"])
        versions = datasets.version_history(dataset.dataset_id)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        label("version_id", language): v.version_id,
                        t["value.kind"]: label(v.kind.value, language),
                        t["value.rows"]: v.row_count,
                        t["value.columns"]: v.column_count,
                        t["value.created_at"]: v.created_at,
                        label("recipe_id", language): v.recipe_id,
                    }
                    for v in versions
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        render_version_navigation(
            runtime, project, datasets.get(dataset.dataset_id), versions, datasets, language
        )
        st.subheader(t["head_movements"])
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        label(key, language): cell_text(value, key, language)
                        for key, value in event.items()
                    }
                    for event in runtime.catalog.list_head_events(dataset.dataset_id)
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )


def _launch(runtime, spec: AnalysisSpec, *, parent_run_id: str | None = None) -> None:
    t = I18N[st.session_state.get("language", "en")]
    if runtime.run_store.active_analysis_runs():
        st.warning(t["another_active"])
        return
    module = runtime.registry.get(spec.module_id)
    issues = module.validate(spec)
    if issues:
        raise MethodNotApplicableError({"blocking_codes": tuple(issue.code for issue in issues)})
    module.check_applicability(spec).require_runnable()
    run = runtime.coordinator.create_run(
        spec, request_key=str(uuid4()), parent_run_id=parent_run_id
    )
    runtime.coordinator.start_worker_process(run.run_id, runtime.catalog.paths.root)
    st.session_state.active_run_id = run.run_id
    st.rerun()


def _spec(
    project_id: str,
    version_id: str,
    module_id: AnalysisModuleId,
    operation: str,
    parameters: dict[str, object],
    roles: tuple[ColumnRole, ...],
) -> AnalysisSpec:
    payload = {
        "project_id": project_id,
        "version_id": version_id,
        "module": module_id.value,
        "module_version": "2.0",
        "operation": operation,
        "parameters": parameters,
        "roles": roles,
    }
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project_id,
        module_id=module_id,
        module_version="2.0",
        operation=operation,
        input_version_id=version_id,
        column_roles=roles,
        parameters=parameters,
        seed=42,
        resource_budget=ResourceBudget(2_000_000_000, 4_000_000_000, 600, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _user_columns(runtime, version_id: str) -> list[dict[str, object]]:
    return [row for row in runtime.catalog.list_columns(version_id) if not row["is_system"]]


def _semantic(row: dict[str, object]) -> SemanticType:
    hint = str(row.get("semantic_hint") or "").lower()
    if hint == "numeric":
        return SemanticType.NUMERIC
    if hint == "boolean":
        return SemanticType.BOOLEAN
    if hint == "datetime":
        return SemanticType.DATETIME
    return SemanticType.CATEGORICAL


def _distinct_values(
    runtime, version_id: str, column: dict[str, object], *, scope: str = "input"
) -> list[object]:
    column_id = str(column["column_id"])
    access = TableAccess(runtime.catalog, runtime.artifact_store)
    choices = access.distinct_values(version_id, column_id)
    if choices.has_more:
        t = I18N[st.session_state.get("language", "en")]
        st.caption(t["distinct_limited"])
        text = st.text_input(
            t["distinct_search"],
            key=f"distinct_search_{version_id}_{column_id}_{scope}",
            max_chars=200,
        )
        choices = access.distinct_values(version_id, column_id, search=text)
    return [_python_scalar(value) for value in choices.values]


def _python_scalar(value):
    return value.item() if hasattr(value, "item") else value
