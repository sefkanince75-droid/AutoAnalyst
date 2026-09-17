"""Thin Streamlit V2 UI: Data -> Analysis -> Results -> History."""

from __future__ import annotations

from uuid import uuid4

import pandas as pd
import streamlit as st

from ..application.datasets import DatasetService
from ..application.preparation import PreparationService
from ..application.projects import ProjectService
from ..bootstrap import build_runtime
from ..data import CSVIngestor, TableAccess, XLSXIngestor
from ..domain.codec import encode_typed_label, fingerprint, utc_now
from ..domain.datasets import ColumnRole, ColumnUsage, SemanticType
from ..domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from ..domain.runs import RunStatus
from ..reporting import ReportService

I18N = {
    "en": {
        "title": "AutoAnalyst V2",
        "subtitle": "Local, deterministic tabular analytics with reproducible runs.",
        "data": "Data",
        "analysis": "Analysis",
        "results": "Results",
        "history": "History",
        "project": "Project",
        "dataset": "Dataset",
        "create_project": "Create project",
        "create_dataset": "Create dataset",
        "import": "Import",
        "prepare": "Prepare data",
        "preview": "Preview",
        "apply": "Apply preview",
        "run": "Run",
        "cancel": "Cancel run",
        "refresh": "Refresh status",
        "export": "Export report",
        "no_project": "Create a project to begin.",
        "no_dataset": "Create a dataset to continue.",
        "no_data": "Import data to continue.",
        "completed": "Completed",
        "running": "Run in progress",
        "cancel_requested": "Cancellation requested.",
    },
    "tr": {
        "title": "AutoAnalyst V2",
        "subtitle": "Yerel, deterministik ve tekrar üretilebilir tablo analizi.",
        "data": "Veri",
        "analysis": "Analiz",
        "results": "Sonuçlar",
        "history": "Geçmiş",
        "project": "Proje",
        "dataset": "Veri seti",
        "create_project": "Proje oluştur",
        "create_dataset": "Veri seti oluştur",
        "import": "İçe aktar",
        "prepare": "Veriyi hazırla",
        "preview": "Önizle",
        "apply": "Önizlemeyi uygula",
        "run": "Çalıştır",
        "cancel": "Çalışmayı iptal et",
        "refresh": "Durumu yenile",
        "export": "Rapor oluştur",
        "no_project": "Başlamak için bir proje oluşturun.",
        "no_dataset": "Devam etmek için bir veri seti oluşturun.",
        "no_data": "Devam etmek için veri içe aktarın.",
        "completed": "Tamamlandı",
        "running": "Çalışma sürüyor",
        "cancel_requested": "İptal isteği gönderildi.",
    },
}


def main() -> None:
    st.set_page_config(page_title="AutoAnalyst V2", layout="wide")
    language = st.sidebar.selectbox("Language / Dil", ("en", "tr"), index=0)
    t = I18N[language]
    st.title(t["title"])
    st.caption(t["subtitle"])

    runtime = build_runtime()
    projects = ProjectService(runtime.catalog)
    datasets = DatasetService(runtime.catalog)
    project = _select_project(projects, language)
    if project is None:
        st.info(t["no_project"])
        return
    dataset = _select_dataset(datasets, project.project_id, language)
    _active_run_panel(runtime, t)

    tabs = st.tabs((t["data"], t["analysis"], t["results"], t["history"]))
    with tabs[0]:
        _data_tab(runtime, project, dataset, datasets, language)
    with tabs[1]:
        _analysis_tab(runtime, project, dataset, datasets, language)
    with tabs[2]:
        _results_tab(runtime, project, language)
    with tabs[3]:
        _history_tab(runtime, project, dataset, datasets)


def _select_project(service: ProjectService, language: str):
    t = I18N[language]
    projects = service.list()
    with st.sidebar.expander(t["create_project"], expanded=not projects):
        name = st.text_input("Name / Ad", key="new_project_name")
        if st.button(t["create_project"], key="create_project") and name.strip():
            created = service.create(name.strip(), default_language=language)
            st.session_state.project_id = created.project_id
            st.rerun()
    if not projects:
        return None
    ids = [item.project_id for item in projects]
    selected_id = st.session_state.get("project_id")
    index = ids.index(selected_id) if selected_id in ids else 0
    selected = st.sidebar.selectbox(
        t["project"], projects, index=index, format_func=lambda item: item.name
    )
    st.session_state.project_id = selected.project_id
    return selected


def _select_dataset(service: DatasetService, project_id: str, language: str):
    t = I18N[language]
    datasets = service.list(project_id)
    with st.sidebar.expander(t["create_dataset"]):
        name = st.text_input("Name / Ad", key="new_dataset_name")
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


def _active_run_panel(runtime, t: dict[str, str]) -> None:
    run_id = st.session_state.get("active_run_id")
    if not run_id:
        return
    try:
        run = runtime.run_store.get_run(run_id)
    except Exception:
        st.session_state.pop("active_run_id", None)
        return
    if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
        if run.status is RunStatus.COMPLETED:
            st.success(f"{t['completed']}: {run.run_id[:8]}")
            st.session_state.last_result_id = run.result_id
        elif run.status is RunStatus.CANCELLED:
            st.warning(f"Cancelled / İptal: {run.run_id[:8]}")
        else:
            st.error(f"Failed / Başarısız: {run.error_code or run.run_id[:8]}")
        st.session_state.pop("active_run_id", None)
        return
    with st.container(border=True):
        st.write(f"**{t['running']}** · `{run.status.value}` · `{run.run_id[:8]}`")
        left, right = st.columns(2)
        if left.button(t["refresh"], key="refresh_active_run"):
            st.rerun()
        if right.button(t["cancel"], key="cancel_active_run"):
            runtime.coordinator.request_cancel(run.run_id, runtime.catalog.paths.root)
            st.warning(t["cancel_requested"])


def _data_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    t = I18N[language]
    if dataset is None:
        st.info(t["no_dataset"])
        return
    st.subheader(t["import"])
    uploaded = st.file_uploader(
        "CSV / XLSX", type=("csv", "xlsx"), key=f"upload_{dataset.dataset_id}"
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        if uploaded.name.lower().endswith(".xlsx"):
            xlsx = XLSXIngestor(runtime.catalog, runtime.artifact_store)
            sheet = st.selectbox("Worksheet / Sayfa", xlsx.sheet_names(raw))
            if st.button(t["import"] + " XLSX", key="import_xlsx"):
                xlsx.import_xlsx(
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    source=raw,
                    original_name=uploaded.name,
                    sheet_name=sheet,
                )
                st.rerun()
        elif st.button(t["import"] + " CSV", key="import_csv"):
            CSVIngestor(runtime.catalog, runtime.artifact_store).import_csv(
                project_id=project.project_id,
                dataset_id=dataset.dataset_id,
                source=raw,
                original_name=uploaded.name,
            )
            st.rerun()

    version = datasets.current_version(dataset.dataset_id)
    if version is None:
        st.info(t["no_data"])
        return
    st.success(
        f"{version.row_count:,} rows · {version.column_count} columns · `{version.version_id[:8]}`"
    )
    columns = _user_columns(runtime, version.version_id)
    st.dataframe(pd.DataFrame(columns), use_container_width=True, hide_index=True)
    _preparation_panel(runtime, project.project_id, version.version_id, columns, language)


def _preparation_panel(
    runtime, project_id: str, version_id: str, columns: list[dict[str, object]], language: str
) -> None:
    t = I18N[language]
    st.subheader(t["prepare"])
    service = PreparationService(runtime.catalog, runtime.artifact_store)
    by_name = {str(row["display_name"]): str(row["column_id"]) for row in columns}
    operation = st.selectbox(
        "Operation / İşlem",
        (
            "filter_rows",
            "rename_column",
            "cast_column",
            "drop_columns",
            "fill_missing",
            "drop_missing_rows",
            "remove_duplicates",
            "replace_non_finite",
        ),
    )
    params: dict[str, object] = {}
    if operation == "filter_rows":
        name = st.selectbox("Column / Sütun", tuple(by_name), key="prep_filter_col")
        operator = st.selectbox(
            "Operator",
            ("eq", "ne", "gt", "gte", "lt", "lte", "contains_literal", "is_null", "is_not_null"),
        )
        params = {"column_id": by_name[name], "operator": operator}
        if operator not in {"is_null", "is_not_null"}:
            params["value"] = st.text_input("Value / Değer", key="prep_filter_value")
    elif operation == "rename_column":
        name = st.selectbox("Column / Sütun", tuple(by_name), key="prep_rename_col")
        params = {"column_id": by_name[name], "new_name": st.text_input("New name / Yeni ad")}
    elif operation == "cast_column":
        name = st.selectbox("Column / Sütun", tuple(by_name), key="prep_cast_col")
        params = {
            "column_id": by_name[name],
            "target_type": st.selectbox(
                "Type / Tür", ("numeric", "categorical", "boolean", "datetime")
            ),
            "failure_policy": st.selectbox("Failure policy", ("strict", "invalid_to_null")),
        }
    elif operation in {
        "drop_columns",
        "drop_missing_rows",
        "remove_duplicates",
        "replace_non_finite",
    }:
        names = st.multiselect("Columns / Sütunlar", tuple(by_name), key=f"prep_{operation}")
        params = {"column_ids": [by_name[name] for name in names]}
        if operation == "remove_duplicates":
            params["keep"] = "first"
    elif operation == "fill_missing":
        names = st.multiselect("Columns / Sütunlar", tuple(by_name), key="prep_fill_cols")
        strategy = st.selectbox("Strategy / Strateji", ("median", "mean", "mode", "constant"))
        params = {"column_ids": [by_name[name] for name in names], "strategy": strategy}
        if strategy == "constant":
            params["value"] = encode_typed_label(st.text_input("Constant / Sabit değer"))

    if st.button(t["preview"], key="preview_preparation"):
        try:
            recipe = service.create_recipe(
                project_id=project_id,
                base_version_id=version_id,
                steps=({"operation": operation, "parameters": params},),
            )
            preview = service.preview_recipe(recipe.recipe_id)
            st.session_state.preview_id = preview.preview_id
        except Exception as exc:
            st.error(str(exc))
    preview_id = st.session_state.get("preview_id")
    if preview_id:
        try:
            preview = service.get_preview(preview_id)
            st.json(
                {
                    "rows_before": preview.before_row_count,
                    "rows_after": preview.after_row_count,
                    "columns_before": preview.before_column_count,
                    "columns_after": preview.after_column_count,
                    "warnings": list(preview.warnings),
                }
            )
            if st.button(t["apply"], key="apply_preparation"):
                service.apply_preview(preview.preview_id)
                st.session_state.pop("preview_id", None)
                st.rerun()
        except Exception as exc:
            st.warning(str(exc))


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
    )
    task = st.selectbox("Analysis / Analiz", choices)
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
            name = st.selectbox("Column / Sütun", tuple(by_name), key="dist_col")
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
            groups = st.multiselect(
                "Group columns", tuple(by_name), max_selections=2, key="summary_groups"
            )
            measures = st.multiselect(
                "Measures", tuple(by_name), max_selections=5, key="summary_measures"
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
            row_name = st.selectbox("Row", tuple(by_name), key="xtab_row")
            col_name = st.selectbox(
                "Column", tuple(by_name), key="xtab_col", index=min(1, len(by_name) - 1)
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
            x = st.selectbox("X", tuple(by_name), key="corr_x")
            y = st.selectbox("Y", tuple(by_name), key="corr_y", index=min(1, len(by_name) - 1))
            method = st.selectbox("Method", ("pearson", "spearman"))
            if st.button(t["run"], key="run_corr"):
                params = {
                    "x_column_id": str(by_name[x]["column_id"]),
                    "y_column_id": str(by_name[y]["column_id"]),
                    "method": method,
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
        else:
            _score_form(runtime, project.project_id, version.version_id, by_name, t)
    except Exception as exc:
        st.error(str(exc))


def _welch_form(
    runtime,
    project_id: str,
    version_id: str,
    by_name: dict[str, dict[str, object]],
    t: dict[str, str],
) -> None:
    group_name = st.selectbox("Group column", tuple(by_name), key="welch_group")
    measure_name = st.selectbox("Numeric measure", tuple(by_name), key="welch_measure")
    values = _distinct_values(runtime, version_id, by_name[group_name])
    if len(values) < 2:
        st.warning("At least two non-null groups are required.")
        return
    group_a = st.selectbox("Group A", values, key="welch_a", format_func=repr)
    group_b = st.selectbox(
        "Group B", values, key="welch_b", index=min(1, len(values) - 1), format_func=repr
    )
    if st.button(t["run"], key="run_welch"):
        params = {
            "group_column_id": str(by_name[group_name]["column_id"]),
            "measure_column_id": str(by_name[measure_name]["column_id"]),
            "group_a": group_a,
            "group_b": group_b,
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
    row_name = st.selectbox("Row variable", tuple(by_name), key="fisher_row")
    col_name = st.selectbox(
        "Column variable", tuple(by_name), key="fisher_col", index=min(1, len(by_name) - 1)
    )
    row_values = _distinct_values(runtime, version_id, by_name[row_name])
    col_values = _distinct_values(runtime, version_id, by_name[col_name])
    if len(row_values) != 2 or len(col_values) != 2:
        st.warning("Fisher 2x2 requires exactly two non-null categories in each variable.")
        return
    row_positive = st.selectbox("Row positive", row_values, format_func=repr)
    col_positive = st.selectbox("Column positive", col_values, format_func=repr)
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
    target_name = st.selectbox("Target", tuple(by_name), key="binary_target")
    feature_names = st.multiselect(
        "Features", tuple(name for name in by_name if name != target_name), key="binary_features"
    )
    target_id = str(by_name[target_name]["column_id"])
    labels = _distinct_values(runtime, version_id, by_name[target_name])
    positive = (
        st.selectbox("Positive event / Pozitif olay", labels, format_func=repr) if labels else None
    )
    split = st.selectbox("Split", ("stratified", "group", "time"))
    group_name = (
        st.selectbox("Group column", tuple(by_name), key="binary_group")
        if split == "group"
        else None
    )
    time_name = (
        st.selectbox("Time column", tuple(by_name), key="binary_time") if split == "time" else None
    )
    minimum_recall = st.slider("Minimum recall", 0.05, 1.0, 0.85, 0.05)
    class_weight = st.selectbox("Class weight", ("none", "balanced"))
    if st.button(t["run"], key="run_binary"):
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
        st.info("No candidate model / Uygun model yok.")
        return
    selected = st.selectbox(
        "Training run",
        candidates,
        format_func=lambda item: f"{item[0].created_at.isoformat()} · {item[2].provenance['recommended_model']['model_id']}",
    )
    run, training_spec, _ = selected
    lock = (
        __import__("autoanalyst.storage.binary", fromlist=["BinaryStore"])
        .BinaryStore(runtime.catalog)
        .get_holdout_lock(run.run_id)
    )
    if lock:
        st.info(f"Holdout: {lock['status']}")
    if st.button(
        "Open final holdout / Son testi aç",
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
        st.info("Train a model first / Önce model eğitin.")
        return
    selected = st.selectbox(
        "Model run",
        candidates,
        format_func=lambda item: f"{item[0].created_at.isoformat()} · {item[2].provenance['recommended_model']['model_id']}",
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
    if st.button("Score / Tahmin et", key="run_scoring"):
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
        st.info("No completed results / Tamamlanmış sonuç yok.")
        return
    default_id = st.session_state.get("last_result_id")
    default_index = next(
        (i for i, run in enumerate(runs) if run.result_id == default_id), len(runs) - 1
    )
    run = st.selectbox(
        "Run / Çalışma",
        runs,
        index=default_index,
        format_func=lambda item: f"{item.created_at.isoformat()} · {runtime.run_store.get_spec(item.spec_id).operation if item.spec_id else item.kind.value}",
    )
    result = runtime.run_store.get_result(run.result_id)
    st.write(f"**{result.module_id.value}** · `{result.outcome.value}`")
    if result.metrics:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "metric": metric.name,
                        "value": metric.value
                        if metric.value is not None
                        else metric.value_state.value,
                        **dict(metric.dimensions),
                    }
                    for metric in result.metrics
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
    for finding in result.findings:
        (st.info if finding.severity.value == "info" else st.warning)(
            f"{finding.code}: {dict(finding.parameters)}"
        )
    for table in result.tables:
        st.dataframe(
            pd.DataFrame(list(table.rows), columns=list(table.columns)),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader(t["export"])
    fmt = st.selectbox("Format", ("html", "markdown", "json", "csv"))
    report_language = st.selectbox(
        "Report language / Rapor dili", ("en", "tr"), index=0 if language == "en" else 1
    )
    service = ReportService(runtime.catalog, runtime.artifact_store)
    if st.button(t["export"], key="build_report"):
        report = service.render(result.result_id, format=fmt, language=report_language)
        st.session_state.report_id = report.report_id
    if report_id := st.session_state.get("report_id"):
        try:
            report = service.get(report_id)
            extension = (
                "zip"
                if report.format == "csv"
                else "md"
                if report.format == "markdown"
                else report.format
            )
            st.download_button(
                "Download / İndir",
                service.artifact_bytes(report),
                file_name=f"autoanalyst-{result.result_id[:8]}.{extension}",
            )
        except Exception:
            st.session_state.pop("report_id", None)


def _history_tab(runtime, project, dataset, datasets: DatasetService) -> None:
    runs = runtime.run_store.list_runs(project.project_id)
    st.subheader("Run history / Çalışma geçmişi")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "run_id": run.run_id,
                    "status": run.status.value,
                    "created_at": run.created_at,
                    "result_id": run.result_id,
                    "error_code": run.error_code,
                }
                for run in runs
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )
    if dataset is not None:
        st.subheader("Dataset versions / Veri sürümleri")
        versions = datasets.version_history(dataset.dataset_id)
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "version_id": v.version_id,
                        "kind": v.kind.value,
                        "rows": v.row_count,
                        "columns": v.column_count,
                        "created_at": v.created_at,
                        "recipe_id": v.recipe_id,
                    }
                    for v in versions
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Head movements")
        st.dataframe(
            pd.DataFrame(runtime.catalog.list_head_events(dataset.dataset_id)),
            use_container_width=True,
            hide_index=True,
        )


def _launch(runtime, spec: AnalysisSpec, *, parent_run_id: str | None = None) -> None:
    active = st.session_state.get("active_run_id")
    if active:
        current = runtime.run_store.get_run(active)
        if current.status in {RunStatus.PENDING, RunStatus.RUNNING}:
            st.warning("Another run is already active / Başka bir çalışma devam ediyor.")
            return
    run = runtime.coordinator.create_run(
        spec, request_key=str(uuid4()), parent_run_id=parent_run_id
    )
    runtime.coordinator.start_worker_process(run.run_id, runtime.catalog.paths.root)
    st.session_state.active_run_id = run.run_id
    st.info(f"Started / Başlatıldı: `{run.run_id[:8]}`")


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


def _distinct_values(runtime, version_id: str, column: dict[str, object]) -> list[object]:
    column_id = str(column["column_id"])
    physical = str(column["physical_name"])
    frame = (
        TableAccess(runtime.catalog, runtime.artifact_store)
        .selected_columns(version_id, (column_id,))
        .to_pandas()
    )
    values = frame[physical].dropna().drop_duplicates().tolist()
    return [_python_scalar(value) for value in values[:200]]


def _python_scalar(value):
    return value.item() if hasattr(value, "item") else value
