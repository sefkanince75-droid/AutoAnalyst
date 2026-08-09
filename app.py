"""Streamlit entry point for AutoAnalyst."""

from __future__ import annotations

from functools import partial

import pandas as pd
import streamlit as st

from src.data_loader import CSVLoadError, load_csv
from src.diagnostics import binary_target_candidates, diagnose_dataset, validate_binary_target
from src.evaluation import (
    compare_models_on_validation,
    comparison_table,
    plot_precision_recall_curves,
    plot_roc_curves,
)
from src.final_evaluation import evaluate_locked_model_on_test
from src.i18n import translate
from src.models import train_baseline_models
from src.preprocessing import fit_preprocessor
from src.reporting import (
    ReportData,
    build_final_interpretation,
    build_metrics_export,
    generate_markdown_report,
    serialize_model_package,
)
from src.splitting import SplitError, split_class_summary, stratified_train_validation_test_split
from src.thresholding import optimize_model_thresholds, plot_threshold_tradeoff, recommend_model
from src.ui_state import ANALYSIS_STATE_KEYS, reset_analysis_state, workflow_stage


st.set_page_config(page_title="AutoAnalyst", page_icon="📊", layout="wide")
language_name = st.sidebar.selectbox("Language / Dil", ["English", "Türkçe"], key="language")
language = "en" if language_name == "English" else "tr"
t = partial(translate, language)
analysis_nonce = int(st.session_state.get("analysis_nonce", 0))


def render_workflow(current_stage: int) -> None:
    """Render a compact, theme-compatible seven-stage status row."""
    labels = [
        t("stage_data"),
        t("stage_target"),
        t("stage_diagnostics"),
        t("stage_models"),
        t("stage_threshold"),
        t("stage_final"),
        t("stage_complete"),
    ]
    columns = st.columns(len(labels))
    for index, (column, label) in enumerate(zip(columns, labels)):
        marker = "✅" if index < current_stage else "▶️" if index == current_stage else "○"
        column.caption(f"{marker} **{index + 1}. {label}**")


def render_metric_guide() -> None:
    """Keep concise metric definitions available without crowding the main flow."""
    with st.expander(t("metric_help")):
        for metric, key in (
            ("Precision", "help_precision"),
            ("Recall", "help_recall"),
            ("F1", "help_f1"),
            ("PR-AUC", "help_pr_auc"),
            ("ROC-AUC", "help_roc_auc"),
            ("Accuracy", "help_accuracy"),
            (t("false_positives"), "help_fp"),
            (t("false_negatives"), "help_fn"),
            (t("minimum_recall"), "help_minimum_recall"),
            (t("selected_threshold"), "help_threshold"),
        ):
            st.write(f"**{metric}:** {t(key)}")


def render_about() -> None:
    """Place stable V1 limitations at the bottom of the currently visible flow."""
    with st.expander(t("about_limitations")):
        for key in (
            "limitation_scope",
            "limitation_cv",
            "limitation_tuning",
            "limitation_causal",
            "limitation_quality",
        ):
            st.write(f"- {t(key)}")


st.title(t("title"))
st.caption(t("subtitle"))
progress_slot = st.container()

uploaded_file = st.file_uploader(t("upload"), type=["csv"], key=f"uploader_{analysis_nonce}")
if uploaded_file is None:
    with progress_slot:
        render_workflow(0)
    st.info(t("upload_hint"))
    render_about()
    st.stop()

try:
    with st.spinner(t("reading")):
        dataframe = load_csv(uploaded_file)
except CSVLoadError:
    with progress_slot:
        render_workflow(0)
    st.error(t("upload_hint"))
    render_about()
    st.stop()

st.subheader(t("overview"))
row_col, column_col, duplicate_col = st.columns(3)
row_col.metric(t("rows"), f"{len(dataframe):,}")
column_col.metric(t("columns"), f"{len(dataframe.columns):,}")
duplicate_col.metric(t("duplicates"), f"{dataframe.duplicated().sum():,}")
with st.expander(t("preview")):
    st.dataframe(dataframe.head(10), use_container_width=True)
    schema = dataframe.dtypes.astype(str).rename(t("dtype")).to_frame()
    schema[t("missing_values")] = dataframe.isna().sum()
    schema[t("missing_pct")] = (dataframe.isna().mean() * 100).round(2)
    schema.index.name = t("column")
    st.markdown(f"#### {t('schema')}")
    st.dataframe(schema, use_container_width=True)

st.subheader(t("target_selection"))
target_candidates = binary_target_candidates(dataframe)
if not target_candidates:
    with progress_slot:
        render_workflow(1)
    st.error(t("target_none"))
    render_about()
    st.stop()

target_key = f"target_{analysis_nonce}"
if len(target_candidates) == 1:
    target = st.selectbox(t("target_label"), target_candidates, index=0, key=target_key)
    st.success(t("target_detected", target=target))
else:
    st.info(t("target_multiple"))
    target = st.selectbox(
        t("target_label"),
        target_candidates,
        index=None,
        placeholder=t("target_placeholder"),
        key=target_key,
    )
if target is None:
    with progress_slot:
        render_workflow(1)
    st.info(t("target_wait"))
    render_about()
    st.stop()

target_validation = validate_binary_target(dataframe[target])
validation_message = t(target_validation.translation_key, **(target_validation.message_values or {}))
if not target_validation.is_valid:
    with progress_slot:
        render_workflow(1)
    st.error(validation_message)
    render_about()
    st.stop()

analysis_key = (uploaded_file.name, uploaded_file.size, target)
if st.session_state.get("analysis_key") != analysis_key:
    for state_key in ANALYSIS_STATE_KEYS - {"analysis_key"}:
        st.session_state.pop(state_key, None)
    st.session_state.analysis_key = analysis_key

validation_results = st.session_state.get("validation_results")
threshold_results = st.session_state.get("threshold_results")
locked_model_name = st.session_state.get("recommended_model_name")
locked_threshold = st.session_state.get("selected_threshold")
final_result = st.session_state.get("final_evaluation")
current_stage = workflow_stage(
    has_data=True,
    has_target=True,
    has_diagnostics=True,
    has_models=validation_results is not None,
    has_threshold=locked_model_name is not None and locked_threshold is not None,
    has_final_result=final_result is not None,
)
with progress_slot:
    render_workflow(current_stage)

class_summary = target_validation.class_summary.copy().rename(
    columns={"count": t("count"), "percentage": t("percentage")}
)
class_summary.index = class_summary.index.map(str)
st.dataframe(class_summary, use_container_width=True)

with st.spinner(t("analyzing")):
    diagnostics = diagnose_dataset(dataframe, target)
st.subheader(t("diagnostics"))
if diagnostics.severe_class_imbalance:
    st.warning(t("severe_imbalance"))
elif diagnostics.class_imbalance:
    st.warning(t("imbalance"))
else:
    st.info(t("no_imbalance"))

left, right = st.columns(2)
with left:
    st.markdown(f"#### {t('feature_types')}")
    st.write(f"{t('numerical')}: {len(diagnostics.numerical_columns)}")
    st.write(f"{t('categorical')}: {len(diagnostics.categorical_columns)}")
    st.write(f"{t('minority_ratio')}: {diagnostics.minority_class_ratio:.1%}")
with right:
    st.markdown(f"#### {t('quality_flags')}")
    st.write(f"{t('missing_columns')}: {len(diagnostics.columns_with_missing)}")
    st.write(f"{t('constant_columns')}: {len(diagnostics.constant_columns)}")
    st.write(f"{t('high_cardinality')}: {len(diagnostics.high_cardinality_columns)}")
    st.write(f"{t('outlier_columns')}: {len(diagnostics.outlier_counts)}")

with st.expander(t("diagnostic_details")):
    detail_rows = {
        t("numerical"): diagnostics.numerical_columns,
        t("categorical"): diagnostics.categorical_columns,
        t("missing_columns"): diagnostics.columns_with_missing,
        t("constant_columns"): diagnostics.constant_columns,
        t("high_cardinality"): diagnostics.high_cardinality_columns,
    }
    for label, values in detail_rows.items():
        st.write(f"**{label}:** {', '.join(values) if values else t('none')}")
    if diagnostics.outlier_counts:
        st.write(f"**{t('outlier_detail')}**")
        st.json(diagnostics.outlier_counts)
    st.info(t("outlier_note"))

st.subheader(t("split_title"))
try:
    splits = stratified_train_validation_test_split(dataframe, target)
except SplitError:
    st.error(t("split_error"))
    render_about()
    st.stop()
train_col, validation_col, test_col = st.columns(3)
train_col.metric(t("train_rows"), f"{len(splits.X_train):,}")
validation_col.metric(t("validation_rows"), f"{len(splits.X_validation):,}")
test_col.metric(t("test_rows"), f"{len(splits.X_test):,}")
localized_split_summary = split_class_summary(splits).rename(
    columns={
        "split": t("split"),
        "rows": t("rows"),
        "class": t("class"),
        "class count": t("class_count"),
        "class percentage": t("class_percentage"),
    }
)
st.dataframe(localized_split_summary, use_container_width=True, hide_index=True)

st.subheader(t("preprocessing"))
with st.spinner(t("preprocessing_status")):
    fitted_preprocessor = fit_preprocessor(splits.X_train, scale_numeric=True)
st.write(
    t(
        "preprocessing_fitted",
        numeric=len(fitted_preprocessor.numerical_columns),
        categorical=len(fitted_preprocessor.categorical_columns),
    )
)
with st.expander(t("methodology")):
    st.write(t("methodology_detail"))
    st.write(t("preprocessing_detail"))
    st.success(t("preprocessing_safe"))

st.subheader(t("baseline_title"))
if diagnostics.severe_class_imbalance:
    st.info(t("metric_warning"))
else:
    st.info(t("metric_info"))
render_metric_guide()

if validation_results is None:
    st.info(t("next_train"))
    if st.button(t("train_button"), type="primary"):
        with st.spinner(t("training")):
            st.session_state.trained_models = train_baseline_models(splits.X_train, splits.y_train)
            st.session_state.validation_results = compare_models_on_validation(
                st.session_state.trained_models,
                splits.X_validation,
                splits.y_validation,
            )
        for state_key in ANALYSIS_STATE_KEYS - {"analysis_key", "trained_models", "validation_results"}:
            st.session_state.pop(state_key, None)
        st.rerun()
    render_about()
    st.stop()

st.markdown(f"#### {t('validation_comparison')}")
baseline_table = comparison_table(validation_results).rename(
    columns={
        "Model": t("model"),
        "False positives": t("false_positives"),
        "False negatives": t("false_negatives"),
    }
)
baseline_display = baseline_table.copy()
metric_columns = ["PR-AUC", "ROC-AUC", "Precision @ 0.5", "Recall @ 0.5", "F1 @ 0.5"]
baseline_display[metric_columns] = baseline_display[metric_columns].round(4)
st.dataframe(
    baseline_display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "PR-AUC": st.column_config.NumberColumn(help=t("help_pr_auc"), format="%.4f"),
        "ROC-AUC": st.column_config.NumberColumn(help=t("help_roc_auc"), format="%.4f"),
        "Precision @ 0.5": st.column_config.NumberColumn(help=t("help_precision"), format="%.4f"),
        "Recall @ 0.5": st.column_config.NumberColumn(help=t("help_recall"), format="%.4f"),
        "F1 @ 0.5": st.column_config.NumberColumn(help=t("help_f1"), format="%.4f"),
        t("false_positives"): st.column_config.NumberColumn(help=t("help_fp")),
        t("false_negatives"): st.column_config.NumberColumn(help=t("help_fn")),
    },
)

with st.expander(t("confusion_default")):
    matrix_columns = st.columns(len(validation_results))
    for column, result in zip(matrix_columns, validation_results.values()):
        with column:
            st.write(f"**{result.model_name}**")
            st.dataframe(
                pd.DataFrame(
                    result.confusion_matrix,
                    index=[t("actual_negative"), t("actual_positive")],
                    columns=[t("pred_negative"), t("pred_positive")],
                ),
                use_container_width=True,
            )
    roc_column, pr_column = st.columns(2)
    with roc_column:
        st.pyplot(
            plot_roc_curves(
                validation_results,
                {"title": t("roc_title"), "fpr": t("fpr"), "tpr": t("tpr"), "random": t("random_baseline")},
            ),
            use_container_width=True,
        )
    with pr_column:
        positive_class = next(iter(validation_results.values())).positive_class
        prevalence = float((splits.y_validation == positive_class).mean())
        st.pyplot(
            plot_precision_recall_curves(
                validation_results,
                prevalence,
                {"title": t("pr_title"), "baseline": t("prevalence_baseline")},
            ),
            use_container_width=True,
        )

st.subheader(t("threshold_title"))
minimum_recall = st.slider(
    t("minimum_recall"),
    min_value=0.50,
    max_value=0.99,
    value=0.85,
    step=0.01,
    help=t("help_minimum_recall"),
    disabled=final_result is not None,
)
if "optimized_minimum_recall" in st.session_state and st.session_state.optimized_minimum_recall != minimum_recall:
    for state_key in {
        "threshold_results",
        "recommendation",
        "recommended_model_name",
        "selected_threshold",
        "optimized_minimum_recall",
        "final_evaluation",
        "final_evaluation_key",
        "model_package_bytes",
    }:
        st.session_state.pop(state_key, None)
    threshold_results = None
    locked_model_name = None
    locked_threshold = None

if threshold_results is None:
    st.info(t("next_threshold"))
    if st.button(t("optimize_button")):
        with st.spinner(t("optimizing")):
            threshold_results = optimize_model_thresholds(validation_results, minimum_recall)
            recommendation = recommend_model(threshold_results, validation_results)
            st.session_state.threshold_results = threshold_results
            st.session_state.recommendation = recommendation
            st.session_state.optimized_minimum_recall = minimum_recall
            if recommendation is not None:
                st.session_state.recommended_model_name = recommendation.model_name
                st.session_state.selected_threshold = recommendation.threshold
        st.rerun()
    render_about()
    st.stop()

threshold_rows: list[dict[str, object]] = []
for result in threshold_results.values():
    if not result.is_feasible:
        st.error(t("no_threshold", model=result.model_name, recall=minimum_recall))
        continue
    _, fp, fn, _ = result.confusion_matrix.ravel()
    threshold_rows.append(
        {
            t("model"): result.model_name,
            t("selected_threshold"): result.threshold,
            "Precision": result.precision,
            "Recall": result.recall,
            "F1": result.f1,
            t("false_positives"): int(fp),
            t("false_negatives"): int(fn),
        }
    )
if threshold_rows:
    threshold_table = pd.DataFrame(threshold_rows)
    st.dataframe(
        threshold_table,
        use_container_width=True,
        hide_index=True,
        column_config={
            t("selected_threshold"): st.column_config.NumberColumn(help=t("help_threshold"), format="%.6f"),
            "Precision": st.column_config.NumberColumn(help=t("help_precision"), format="%.4f"),
            "Recall": st.column_config.NumberColumn(help=t("help_recall"), format="%.4f"),
            "F1": st.column_config.NumberColumn(help=t("help_f1"), format="%.4f"),
            t("false_positives"): st.column_config.NumberColumn(help=t("help_fp")),
            t("false_negatives"): st.column_config.NumberColumn(help=t("help_fn")),
        },
    )

with st.expander(t("tradeoff")):
    tradeoff_columns = st.columns(len(threshold_results))
    for column, result in zip(tradeoff_columns, threshold_results.values()):
        with column:
            st.pyplot(
                plot_threshold_tradeoff(
                    result,
                    minimum_recall,
                    {
                        "minimum": t("minimum_recall"),
                        "selected": t("selected_threshold"),
                        "threshold": t("threshold"),
                        "metric": t("metric_value"),
                    },
                ),
                use_container_width=True,
            )

recommendation = st.session_state.get("recommendation")
st.markdown(f"#### {t('recommendation')}")
if recommendation is None:
    st.error(t("no_recommendation"))
    render_about()
    st.stop()
locked_model_name = recommendation.model_name
locked_threshold = recommendation.threshold
st.success(
    t(
        "recommended_text",
        model=locked_model_name,
        precision=recommendation.precision,
        recall=recommendation.recall,
        pr_auc=recommendation.pr_auc,
        threshold=locked_threshold,
        minimum=minimum_recall,
    )
)
st.caption(t("objective_note"))

if final_result is None:
    st.subheader(t("final_title"))
    st.info(t("next_final"))
    st.markdown(f"**{t('model')}:** {locked_model_name}  \n**{t('selected_threshold')}:** {locked_threshold:.6f}  \n**{t('minimum_recall')}:** {minimum_recall:.2f}")
    st.warning(t("final_warning"))
    expected_final_key = (analysis_key, locked_model_name, locked_threshold)
    if st.button(t("evaluate_final"), type="primary"):
        with st.spinner(t("evaluating_final")):
            final_result = evaluate_locked_model_on_test(
                st.session_state.trained_models,
                locked_model_name,
                locked_threshold,
                splits.X_test,
                splits.y_test,
            )
            st.session_state.final_evaluation = final_result
            st.session_state.final_evaluation_key = expected_final_key
            st.session_state.model_package_bytes = serialize_model_package(
                st.session_state.trained_models[locked_model_name],
                model_name=locked_model_name,
                selected_threshold=locked_threshold,
                target_column=target,
                positive_class=final_result.positive_class,
                feature_columns=list(splits.X_train.columns),
            )
        st.rerun()
    render_about()
    st.stop()

st.subheader(t("final_performance"))
st.caption(t("validation_selected"))
st.markdown(f"**{t('model')}:** {locked_model_name}  \n**{t('selected_threshold')}:** {locked_threshold:.6f}  \n**{t('minimum_recall')}:** {minimum_recall:.2f}")

primary_columns = st.columns(4)
for column, label, value, help_key in zip(
    primary_columns,
    ("Precision", "Recall", "F1", "PR-AUC"),
    (final_result.precision, final_result.recall, final_result.f1, final_result.pr_auc),
    ("help_precision", "help_recall", "help_f1", "help_pr_auc"),
):
    column.metric(label, f"{value:.4f}", help=t(help_key))
secondary_columns = st.columns(2)
secondary_columns[0].metric("ROC-AUC", f"{final_result.roc_auc:.4f}", help=t("help_roc_auc"))
secondary_columns[1].metric(t("accuracy_reference"), f"{final_result.accuracy:.4f}", help=t("help_accuracy"))

st.markdown(f"#### {t('operational_outcomes')}")
outcome_columns = st.columns(4)
outcome_columns[0].metric(t("true_positives"), f"{final_result.true_positives:,}")
outcome_columns[1].metric(t("false_negatives"), f"{final_result.false_negatives:,}", help=t("help_fn"))
outcome_columns[2].metric(t("false_positives"), f"{final_result.false_positives:,}", help=t("help_fp"))
outcome_columns[3].metric(t("true_negatives"), f"{final_result.true_negatives:,}")
st.dataframe(
    pd.DataFrame(
        final_result.confusion_matrix,
        index=[t("actual_negative"), t("actual_positive")],
        columns=[t("pred_negative"), t("pred_positive")],
    ),
    use_container_width=True,
)
st.markdown(f"#### {t('interpretation')}")
st.write(build_final_interpretation(final_result, language))
st.warning(t("final_warning"))

canonical_baseline = comparison_table(validation_results)
canonical_threshold_rows = []
for result in threshold_results.values():
    if result.is_feasible:
        _, fp, fn, _ = result.confusion_matrix.ravel()
        canonical_threshold_rows.append(
            {
                "Model": result.model_name,
                "Selected threshold": result.threshold,
                "Precision": result.precision,
                "Recall": result.recall,
                "F1": result.f1,
                "False positives": int(fp),
                "False negatives": int(fn),
            }
        )
canonical_threshold = pd.DataFrame(canonical_threshold_rows)
report_data = ReportData(
    dataset_name=uploaded_file.name,
    dataset_rows=len(dataframe),
    dataset_columns=len(dataframe.columns),
    target_name=target,
    target_distribution=class_summary,
    minority_ratio=diagnostics.minority_class_ratio,
    severe_imbalance=diagnostics.severe_class_imbalance,
    train_rows=len(splits.X_train),
    validation_rows=len(splits.X_validation),
    test_rows=len(splits.X_test),
    numerical_features=len(fitted_preprocessor.numerical_columns),
    categorical_features=len(fitted_preprocessor.categorical_columns),
    baseline_comparison=canonical_baseline.rename(
        columns={"Model": t("model"), "False positives": t("false_positives"), "False negatives": t("false_negatives")}
    ),
    minimum_recall=st.session_state.optimized_minimum_recall,
    threshold_comparison=canonical_threshold.rename(
        columns={
            "Model": t("model"),
            "Selected threshold": t("selected_threshold"),
            "False positives": t("false_positives"),
            "False negatives": t("false_negatives"),
        }
    ),
    recommended_model=locked_model_name,
    selected_threshold=locked_threshold,
    final_result=final_result,
)

st.subheader(t("exports"))
with st.spinner(t("generating_report")):
    markdown_report = generate_markdown_report(report_data, language)
    metrics_csv = build_metrics_export(canonical_baseline, canonical_threshold, final_result).to_csv(index=False)
download_columns = st.columns(3)
with download_columns[0]:
    st.write(f"**{t('download_report')}**")
    st.caption(t("report_export_desc"))
    st.download_button(t("download_report"), markdown_report, "autoanalyst_report.md", "text/markdown")
with download_columns[1]:
    st.write(f"**{t('download_metrics')}**")
    st.caption(t("metrics_export_desc"))
    st.download_button(t("download_metrics"), metrics_csv, "autoanalyst_metrics.csv", "text/csv")
with download_columns[2]:
    st.write(f"**{t('download_model')}**")
    st.caption(t("model_export_desc"))
    st.download_button(
        t("download_model"),
        st.session_state.model_package_bytes,
        "autoanalyst_model.joblib",
        "application/octet-stream",
    )

st.success(f"### {t('analysis_complete')}")
st.write(
    t(
        "completion_summary",
        model=locked_model_name,
        threshold=locked_threshold,
        recall=final_result.recall,
        precision=final_result.precision,
        pr_auc=final_result.pr_auc,
    )
)
st.button(
    t("start_new"),
    type="primary",
    on_click=reset_analysis_state,
    args=(st.session_state,),
)
render_about()
