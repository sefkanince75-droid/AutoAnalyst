"""Read-only result layout using the shared persisted-chart presenter."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..domain.codec import to_primitive
from ..domain.results import AnalysisResult
from ..localization import I18N, cell_text, label, result_finding_text
from ..reporting.charts import chart_views, summary_metrics, table_title
from ..reporting.summary import result_summary


def render_result(result: AnalysisResult, language: str) -> None:
    t = I18N[language]
    if result.provenance.get("model_card"):
        with st.expander(t["model_card"]):
            st.json(to_primitive(result.provenance["model_card"]))
    overview = result_summary(result, language)
    with st.expander(t["report_overview"], expanded=False):
        for line in overview.lines:
            st.text(line)
        st.caption(overview.scope)
    st.write(
        f"**{label(result.module_id.value, language)}** · {label(result.outcome.value, language)}"
    )
    recommendation = result.provenance.get("recommended_model")
    if recommendation:
        st.success(
            t["recommended_model_notice"].format(
                model=label(recommendation["model_id"], language),
                threshold=format(float(recommendation["threshold"]), ".4g"),
            )
        )
        st.info(t["model_next_step"])
    if result.methodology.get("operation") == "score_new_data":
        st.caption(t["prediction_score_hint"])
    if result.methodology.get("operation") == "final_evaluate":
        metrics = {metric.name: metric.value for metric in result.metrics}
        shown = [
            name
            for name in ("precision", "recall", "roc_auc", "f1")
            if metrics.get(name) is not None
        ]
        if shown:
            for column, name in zip(st.columns(len(shown)), shown, strict=True):
                column.metric(label(name, language), format(float(metrics[name]), ".4g"))
        if all(metrics.get(name) is not None for name in ("tn", "fp", "fn", "tp")):
            from matplotlib.figure import Figure

            figure = Figure(figsize=(5, 3.5), layout="constrained")
            axis = figure.subplots()
            matrix = [[metrics["tn"], metrics["fp"]], [metrics["fn"], metrics["tp"]]]
            axis.imshow(matrix, cmap="Blues")
            axis.set_title(t["confusion_matrix"])
            axis.set_xticks((0, 1), ("0", "1"))
            axis.set_yticks((0, 1), ("0", "1"))
            axis.set_xlabel(t["predicted_axis"])
            axis.set_ylabel(t["actual_axis"])
            for row in range(2):
                for col in range(2):
                    axis.text(
                        col, row, str(matrix[row][col]), ha="center", va="center", color="#d97706"
                    )
            st.pyplot(figure)
            figure.clear()
    summary = summary_metrics(result)
    if summary:
        st.subheader(t["summary"])
        for column, metric in zip(st.columns(len(summary)), summary, strict=True):
            column.metric(label(metric.name, language), str(metric.value))
    for finding in result.findings:
        show = {"info": st.info, "warning": st.warning, "error": st.error}[finding.severity.value]
        show(result_finding_text(result, finding, language))
    if result.charts:
        st.subheader(t["charts"])
        indexes = {table.table_id: index for index, table in enumerate(result.tables, 1)}
        for chart in chart_views(result, language):
            st.caption(chart.title)
            if chart.svg:
                st.image(chart.svg, width="stretch")
                st.caption(t["chart_source"].format(index=indexes[chart.table_id]))
            else:
                st.warning(chart.notice)
    if result.metrics:
        with st.expander(t["all_metrics"], expanded=not bool(summary)):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            t["metric"]: label(metric.name, language),
                            t["value"]: metric.value
                            if metric.value is not None
                            else label(metric.value_state.value, language),
                            t["confidence_interval"]: str(metric.interval)
                            if metric.interval
                            else "",
                            t["sample_size"]: metric.sample_size,
                            **{
                                label(k, language): label(v, language)
                                for k, v in metric.dimensions.items()
                            },
                        }
                        for metric in result.metrics
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
    if result.tables:
        with st.expander(t["chart_details"], expanded=not bool(result.charts)):
            for index, table in enumerate(result.tables, 1):
                st.caption(table_title(table, index, language))
                st.dataframe(
                    pd.DataFrame(
                        [
                            [
                                cell_text(value, column, language)
                                for column, value in zip(table.columns, row, strict=True)
                            ]
                            for row in table.rows
                        ],
                        columns=[label(column, language) for column in table.columns],
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
