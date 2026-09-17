"""Streamlit V2 UI: Data -> Analysis -> Results -> History."""

from __future__ import annotations

import time
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


TEXT = {
    "en": {"title": "AutoAnalyst V2", "data": "Data", "analysis": "Analysis", "results": "Results", "history": "History", "project": "Project", "dataset": "Dataset"},
    "tr": {"title": "AutoAnalyst V2", "data": "Veri", "analysis": "Analiz", "results": "Sonuçlar", "history": "Geçmiş", "project": "Proje", "dataset": "Veri seti"},
}


def main() -> None:
    st.set_page_config(page_title="AutoAnalyst V2", layout="wide")
    language = st.sidebar.selectbox("Language / Dil", ["en", "tr"], index=0)
    t = TEXT[language]
    st.title(t["title"])
    st.caption("Local-first, deterministic tabular analytics. No external AI service is required.")
    runtime = build_runtime()
    projects = ProjectService(runtime.catalog)
    datasets = DatasetService(runtime.catalog)

    project = _project_selector(projects, language)
    if project is None:
        st.info("Create a project to begin." if language == "en" else "Başlamak için bir proje oluşturun.")
        return
    dataset = _dataset_selector(datasets, project.project_id, language)

    labels = [t["data"], t["analysis"], t["results"], t["history"]]
    tab_data, tab_analysis, tab_results, tab_history = st.tabs(labels)
    with tab_data:
        _data_tab(runtime, project, dataset, datasets, language)
    with tab_analysis:
        _analysis_tab(runtime, project, dataset, datasets, language)
    with tab_results:
        _results_tab(runtime, project, language)
    with tab_history:
        _history_tab(runtime, project, dataset, datasets)


def _project_selector(service: ProjectService, language: str):
    projects = service.list()
    with st.sidebar.expander("New project / Yeni proje", expanded=not projects):
        name = st.text_input("Project name / Proje adı", key="new_project_name")
        if st.button("Create / Oluştur", key="create_project") and name.strip():
            created = service.create(name.strip(), default_language=language)
            st.session_state.project_id = created.project_id
            st.rerun()
    if not projects:
        return None
    ids = [p.project_id for p in projects]
    current = st.session_state.get("project_id")
    index = ids.index(current) if current in ids else 0
    selected = st.sidebar.selectbox("Project / Proje", projects, index=index, format_func=lambda p: p.name)
    st.session_state.project_id = selected.project_id
    return selected


def _dataset_selector(service: DatasetService, project_id: str, language: str):
    items = service.list(project_id)
    with st.sidebar.expander("New dataset / Yeni veri seti", expanded=False):
        name = st.text_input("Dataset name / Veri seti adı", key="new_dataset_name")
        if st.button("Create dataset / Veri seti oluştur", key="create_dataset") and name.strip():
            created = service.create(project_id, name.strip())
            st.session_state.dataset_id = created.dataset_id
            st.rerun()
    if not items:
        return None
    ids = [item.dataset_id for item in items]
    current = st.session_state.get("dataset_id")
    index = ids.index(current) if current in ids else 0
    selected = st.sidebar.selectbox("Dataset / Veri seti", items, index=index, format_func=lambda d: d.name)
    st.session_state.dataset_id = selected.dataset_id
    return selected


def _data_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    if dataset is None:
        st.info("Create a dataset first.")
        return
    st.subheader("Import")
    uploaded = st.file_uploader("CSV or XLSX", type=["csv", "xlsx"], key=f"upload_{dataset.dataset_id}")
    if uploaded is not None:
        raw = uploaded.getvalue()
        if uploaded.name.lower().endswith(".xlsx"):
            xlsx = XLSXIngestor(runtime.catalog, runtime.artifact_store)
            sheets = xlsx.sheet_names(raw)
            sheet = st.selectbox("Worksheet", sheets)
            if st.button("Import XLSX"):
                xlsx.import_xlsx(project_id=project.project_id, dataset_id=dataset.dataset_id, source=raw, original_name=uploaded.name, sheet_name=sheet)
                st.rerun()
        elif st.button("Import CSV"):
            CSVIngestor(runtime.catalog, runtime.artifact_store).import_csv(project_id=project.project_id, dataset_id=dataset.dataset_id, source=raw, original_name=uploaded.name)
            st.rerun()

    version = datasets.current_version(dataset.dataset_id)
    if version is None:
        st.info("Import data to continue.")
        return
    st.success(f"Active version: {version.version_id[:8]} · {version.row_count:,} rows · {version.column_count} columns")
    columns = _user_columns(runtime, version.version_id)
    st.dataframe(pd.DataFrame(columns), use_container_width=True, hide_index=True)

    st.subheader("Prepare data")
    prep = PreparationService(runtime.catalog, runtime.artifact_store)
    by_name = {str(c["display_name"]): str(c["column_id"]) for c in columns}
    operation = st.selectbox("Operation", ["rename_column", "drop_columns", "fill_missing", "drop_missing_rows", "remove_duplicates", "replace_non_finite"])
    params: dict[str, object] = {}
    if operation == "rename_column":
        name = st.selectbox("Column", list(by_name), key="prep_rename_column")
        new_name = st.text_input("New name")
        params = {"column_id": by_name[name], "new_name": new_name}
    elif operation == "drop_columns":
        names = st.multiselect("Columns", list(by_name), key="prep_drop")
        params = {"column_ids": [by_name[n] for n in names]}
    elif operation == "fill_missing":
        names = st.multiselect("Columns", list(by_name), key="prep_fill_cols")
        strategy = st.selectbox("Strategy", ["median", "mean", "mode", "constant"])
        params = {"column_ids": [by_name[n] for n in names], "strategy": strategy}
        if strategy == "constant":
            value = st.text_input("Constant value")
            params["value"] = encode_typed_label(value)
    elif operation in {"drop_missing_rows", "remove_duplicates", "replace_non_finite"}:
        names = st.multiselect("Columns", list(by_name), key=f"prep_{operation}")
        params = {"column_ids": [by_name[n] for n in names]}
        if operation == "remove_duplicates":
            params["keep"] = "first"

    if st.button("Preview preparation", key="preview_prep"):
        try:
            recipe = prep.create_recipe(project_id=project.project_id, base_version_id=version.version_id, steps=[{"operation": operation, "parameters": params}])
            preview = prep.preview_recipe(recipe.recipe_id)
            st.session_state.preview_id = preview.preview_id
        except Exception as exc:
            st.error(str(exc))
    preview_id = st.session_state.get("preview_id")
    if preview_id:
        try:
            preview = prep.get_preview(preview_id)
            st.write({"rows_before": preview.before_row_count, "rows_after": preview.after_row_count, "columns_before": preview.before_column_count, "columns_after": preview.after_column_count, "warnings": list(preview.warnings)})
            if st.button("Apply preview", key="apply_preview"):
                prep.apply_preview(preview_id)
                st.session_state.pop("preview_id", None)
                st.rerun()
        except Exception as exc:
            st.warning(str(exc))


def _analysis_tab(runtime, project, dataset, datasets: DatasetService, language: str) -> None:
    if dataset is None:
        st.info("Create and import a dataset first.")
        return
    version = datasets.current_version(dataset.dataset_id)
    if version is None:
        st.info("Import data first.")
        return
    columns = _user_columns(runtime, version.version_id)
    by_name = {str(c["display_name"]): c for c in columns}
    task = st.selectbox("Analysis", ["Data profile", "Distribution", "Correlation", "Binary classification", "Final holdout"])
    try:
        if task == "Data profile":
            if st.button("Run profile"):
                spec = _spec(project.project_id, version.version_id, AnalysisModuleId.PROFILING, "profile", {}, ())
                _run_spec(runtime, spec)
        elif task == "Distribution":
            name = st.selectbox("Column", list(by_name), key="dist_col")
            if st.button("Run distribution"):
                params = {"column_id": str(by_name[name]["column_id"])}
                spec = _spec(project.project_id, version.version_id, AnalysisModuleId.COMPARISON, "distribution", params, ())
                _run_spec(runtime, spec)
        elif task == "Correlation":
            x = st.selectbox("X", list(by_name), key="corr_x")
            y = st.selectbox("Y", list(by_name), key="corr_y", index=min(1, max(0, len(by_name)-1)))
            method = st.selectbox("Method", ["pearson", "spearman"])
            if st.button("Run correlation"):
                params = {"x_column_id": str(by_name[x]["column_id"]), "y_column_id": str(by_name[y]["column_id"]), "method": method}
                spec = _spec(project.project_id, version.version_id, AnalysisModuleId.COMPARISON, "correlation", params, ())
                _run_spec(runtime, spec)
        elif task == "Binary classification":
            _binary_form(runtime, project.project_id, version.version_id, by_name)
        else:
            _final_form(runtime, project.project_id)
    except Exception as exc:
        st.error(str(exc))


def _binary_form(runtime, project_id: str, version_id: str, by_name: dict[str, dict[str, object]]) -> None:
    target_name = st.selectbox("Target", list(by_name), key="binary_target")
    feature_names = st.multiselect("Features", [name for name in by_name if name != target_name], key="binary_features")
    target_id = str(by_name[target_name]["column_id"])
    access = TableAccess(runtime.catalog, runtime.artifact_store)
    target_frame = access.selected_columns(version_id, (target_id,)).to_pandas()
    physical = str(by_name[target_name]["physical_name"])
    labels = [_python_scalar(v) for v in target_frame[physical].dropna().drop_duplicates().tolist()]
    positive = st.selectbox("Positive event", labels, format_func=lambda value: repr(value)) if labels else None
    split = st.selectbox("Split", ["stratified", "group", "time"])
    group_name = st.selectbox("Group column", list(by_name), key="binary_group") if split == "group" else None
    time_name = st.selectbox("Time column", list(by_name), key="binary_time") if split == "time" else None
    minimum_recall = st.slider("Minimum recall", 0.05, 1.0, 0.85, 0.05)
    class_weight = st.selectbox("Class weight", ["none", "balanced"])
    if st.button("Train and validate"):
        roles: list[ColumnRole] = []
        for name in feature_names:
            row = by_name[name]
            roles.append(ColumnRole(str(row["column_id"]), _semantic(row), (ColumnUsage.FEATURE,), True, True))
        roles.append(ColumnRole(target_id, _semantic(by_name[target_name]), (ColumnUsage.TARGET,), True))
        if group_name:
            row = by_name[group_name]
            roles.append(ColumnRole(str(row["column_id"]), _semantic(row), (ColumnUsage.GROUP,), True))
        if time_name:
            row = by_name[time_name]
            roles.append(ColumnRole(str(row["column_id"]), _semantic(row), (ColumnUsage.TIME,), True))
        params = {"positive_label": positive, "minimum_recall": minimum_recall, "split_policy": split, "class_weight_policy": class_weight}
        spec = _spec(project_id, version_id, AnalysisModuleId.BINARY_CLASSIFICATION, "train_validate", params, tuple(roles))
        _run_spec(runtime, spec)


def _final_form(runtime, project_id: str) -> None:
    candidates = []
    for run in runtime.run_store.list_runs(project_id):
        if run.status is RunStatus.COMPLETED and run.spec_id:
            spec = runtime.run_store.get_spec(run.spec_id)
            result = runtime.run_store.result_for_run(run.run_id)
            if spec.module_id is AnalysisModuleId.BINARY_CLASSIFICATION and spec.operation == "train_validate" and result is not None and result.provenance.get("recommended_model"):
                candidates.append((run, spec, result))
    if not candidates:
        st.info("No finalized candidate model is available yet.")
        return
    labels = {run.run_id: f"{run.created_at.isoformat()} · {result.provenance['recommended_model']['model_id']}" for run, spec, result in candidates}
    selected_id = st.selectbox("Training run", [run.run_id for run, _, _ in candidates], format_func=lambda rid: labels[rid])
    run, training_spec, _ = next(item for item in candidates if item[0].run_id == selected_id)
    if st.button("Open final holdout"):
        params = {"training_run_id": run.run_id}
        spec = _spec(run.project_id, training_spec.input_version_id, AnalysisModuleId.BINARY_CLASSIFICATION, "final_evaluate", params, training_spec.column_roles)
        _run_spec(runtime, spec, parent_run_id=run.run_id)


def _results_tab(runtime, project, language: str) -> None:
    completed = [run for run in runtime.run_store.list_runs(project.project_id) if run.status is RunStatus.COMPLETED and run.result_id]
    if not completed:
        st.info("No completed analyses yet.")
        return
    run = st.selectbox("Run", completed[::-1], format_func=lambda r: f"{r.created_at.isoformat()} · {runtime.run_store.get_spec(r.spec_id).operation if r.spec_id else r.kind.value}")
    result = runtime.run_store.get_result(run.result_id)
    st.write(f"**{result.module_id.value}** · `{result.outcome.value}`")
    if result.metrics:
        st.dataframe(pd.DataFrame([{"metric": m.name, "value": m.value if m.value is not None else m.value_state.value, **dict(m.dimensions)} for m in result.metrics]), use_container_width=True, hide_index=True)
    for finding in result.findings:
        st.warning(f"{finding.code}: {dict(finding.parameters)}") if finding.severity.value != "info" else st.info(f"{finding.code}: {dict(finding.parameters)}")
    for table in result.tables:
        st.dataframe(pd.DataFrame(list(table.rows), columns=list(table.columns)), use_container_width=True, hide_index=True)
    st.subheader("Export")
    fmt = st.selectbox("Format", ["html", "markdown", "json", "csv"])
    report_language = st.selectbox("Report language", ["en", "tr"], index=0 if language == "en" else 1)
    if st.button("Build report"):
        service = ReportService(runtime.catalog, runtime.artifact_store)
        report = service.render(result.result_id, format=fmt, language=report_language)
        st.session_state.report_id = report.report_id
    report_id = st.session_state.get("report_id")
    if report_id:
        service = ReportService(runtime.catalog, runtime.artifact_store)
        try:
            report = service.get(report_id)
            st.download_button("Download report", service.artifact_bytes(report), file_name=f"autoanalyst-{result.result_id[:8]}.{('zip' if report.format == 'csv' else 'md' if report.format == 'markdown' else report.format)}")
        except Exception:
            st.session_state.pop("report_id", None)


def _history_tab(runtime, project, dataset, datasets: DatasetService) -> None:
    st.subheader("Run history")
    runs = runtime.run_store.list_runs(project.project_id)
    st.dataframe(pd.DataFrame([{"run_id": r.run_id, "status": r.status.value, "created_at": r.created_at, "result_id": r.result_id, "error_code": r.error_code} for r in runs]), use_container_width=True, hide_index=True)
    if dataset is not None:
        st.subheader("Dataset versions")
        versions = datasets.version_history(dataset.dataset_id)
        st.dataframe(pd.DataFrame([{"version_id": v.version_id, "kind": v.kind.value, "rows": v.row_count, "columns": v.column_count, "created_at": v.created_at, "recipe_id": v.recipe_id} for v in versions]), use_container_width=True, hide_index=True)
        st.subheader("Head movements")
        st.dataframe(pd.DataFrame(runtime.catalog.list_head_events(dataset.dataset_id)), use_container_width=True, hide_index=True)


def _run_spec(runtime, spec: AnalysisSpec, *, parent_run_id: str | None = None) -> None:
    run = runtime.coordinator.create_run(spec, request_key=str(uuid4()), parent_run_id=parent_run_id)
    process = runtime.coordinator.start_worker_process(run.run_id, runtime.catalog.paths.root)
    status = st.empty()
    with st.spinner("Running analysis..."):
        while process.poll() is None:
            try:
                current = runtime.run_store.get_run(run.run_id)
                status.caption(f"{current.status.value} · run {run.run_id[:8]}")
            except Exception:
                pass
            time.sleep(0.25)
    final = runtime.run_store.get_run(run.run_id)
    status.empty()
    if final.status is RunStatus.COMPLETED:
        st.success(f"Completed: {run.run_id[:8]}")
        st.session_state.last_result_id = final.result_id
    else:
        st.error(f"Run ended with {final.status.value}: {final.error_code or 'worker exit ' + str(process.returncode)}")


def _spec(project_id: str, version_id: str, module_id: AnalysisModuleId, operation: str, parameters: dict[str, object], roles: tuple[ColumnRole, ...]) -> AnalysisSpec:
    payload = {"project_id": project_id, "version_id": version_id, "module": module_id.value, "module_version": "2.0", "operation": operation, "parameters": parameters, "roles": roles}
    return AnalysisSpec(spec_id=str(uuid4()), project_id=project_id, module_id=module_id, module_version="2.0", operation=operation, input_version_id=version_id, column_roles=roles, parameters=parameters, seed=42, resource_budget=ResourceBudget(2_000_000_000, 4_000_000_000, 600, 1), spec_hash=fingerprint(payload), created_at=utc_now())


def _user_columns(runtime, version_id: str) -> list[dict[str, object]]:
    return [row for row in runtime.catalog.list_columns(version_id) if not row["is_system"]]


def _semantic(row: dict[str, object]) -> SemanticType:
    hint = str(row.get("semantic_hint") or "").lower()
    if hint == "numeric": return SemanticType.NUMERIC
    if hint == "boolean": return SemanticType.BOOLEAN
    if hint == "datetime": return SemanticType.DATETIME
    return SemanticType.CATEGORICAL


def _python_scalar(value):
    return value.item() if hasattr(value, "item") else value
