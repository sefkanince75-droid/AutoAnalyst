"""Localized deterministic reports and reusable model-package exports."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline

from src.final_evaluation import FinalTestResult
from src.evaluation_sample import EvaluationSampleDiagnostic
from src.i18n import translate


@dataclass(frozen=True)
class ReportData:
    """Primitive and tabular values required for the V1 Markdown report."""

    dataset_name: str
    dataset_rows: int
    dataset_columns: int
    target_name: str
    target_distribution: pd.DataFrame
    minority_ratio: float
    severe_imbalance: bool
    train_rows: int
    validation_rows: int
    test_rows: int
    numerical_features: int
    categorical_features: int
    baseline_comparison: pd.DataFrame
    minimum_recall: float
    threshold_comparison: pd.DataFrame
    recommended_model: str
    selected_threshold: float
    final_result: FinalTestResult
    validation_sample: EvaluationSampleDiagnostic
    final_test_sample: EvaluationSampleDiagnostic


def _dataframe_to_markdown(dataframe: pd.DataFrame, *, include_index: bool = False) -> str:
    """Render a compact Markdown table without an optional third-party dependency."""
    frame = dataframe.reset_index() if include_index else dataframe.reset_index(drop=True)
    headers = [str(column) for column in frame.columns]
    rows = [[str(value) for value in row] for row in frame.itertuples(index=False, name=None)]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def build_final_interpretation(result: FinalTestResult, language: str) -> str:
    """Describe the error profile without making domain-specific claims."""
    counts = translate(
        language,
        "interpret_counts",
        detected=result.true_positives,
        missed=result.false_negatives,
        alarms=result.false_positives,
        negatives=result.true_negatives,
    )
    if result.precision < 0.50 or result.false_positives > result.true_positives:
        suitability = translate(language, "interpret_screening")
    elif result.precision >= 0.80 and result.recall >= 0.80:
        suitability = translate(language, "interpret_selective")
    else:
        suitability = translate(language, "interpret_review")
    return f"{counts}\n\n{suitability}"


def generate_markdown_report(data: ReportData, language: str) -> str:
    """Generate a localized Markdown account of the complete deterministic workflow."""
    t = lambda key, **values: translate(language, key, **values)
    result = data.final_result
    interpretation = build_final_interpretation(result, language)
    imbalance_text = t("report_severe") if data.severe_imbalance else t("report_not_severe")

    target_table = _dataframe_to_markdown(data.target_distribution, include_index=True)
    baseline_table = _dataframe_to_markdown(data.baseline_comparison)
    threshold_table = _dataframe_to_markdown(data.threshold_comparison)
    validation_sample_note = ""
    if data.validation_sample.limited_sample:
        validation_sample_note = "\n\n" + t(
            "report_validation_sample",
            positive=data.validation_sample.positive_count,
            negative=data.validation_sample.negative_count,
        )
        validation_sample_note += "\n\n" + t("report_sample_limitation")
        if data.validation_sample.recall_resolution is not None:
            validation_sample_note += "\n\n" + t(
                "validation_recall_resolution",
                positive=data.validation_sample.positive_count,
                resolution=data.validation_sample.recall_resolution,
            )
    final_sample_note = ""
    if data.final_test_sample.limited_sample:
        final_sample_note = "\n\n" + t(
            "report_final_sample",
            positive=data.final_test_sample.positive_count,
            negative=data.final_test_sample.negative_count,
            tp=result.true_positives,
            fn=result.false_negatives,
            fp=result.false_positives,
            tn=result.true_negatives,
        )
        final_sample_note += "\n\n" + t(
            "final_recall_basis",
            recall=result.recall,
            detected=result.true_positives,
            positive=data.final_test_sample.positive_count,
        )
        final_sample_note += "\n\n" + t("report_sample_limitation")
    return f"""# AutoAnalyst — {t('report_title')}

## {t('report_dataset')}

- {t('report_file')}: `{data.dataset_name}`
- {t('rows')}: {data.dataset_rows:,}
- {t('columns')}: {data.dataset_columns:,}
- {t('report_target')}: `{data.target_name}`

### {t('report_target_distribution')}

{target_table}

{t('minority_ratio')}: {data.minority_ratio:.4%}. {imbalance_text}

## {t('report_methodology')}

- {t('report_split', train=data.train_rows, validation=data.validation_rows, test=data.test_rows)}
- {t('report_preprocessing', numeric=data.numerical_features, categorical=data.categorical_features)}
- {t('report_test_separation')}

## {t('validation_comparison')}

{baseline_table}{validation_sample_note}

## {t('report_operational')}

- {t('minimum_recall')}: {data.minimum_recall:.2f}
- {t('report_recommended')}: {data.recommended_model}
- {t('selected_threshold')}: {data.selected_threshold:.6f}

{threshold_table}

## {t('report_final')}

{t('report_locked_note')}
{final_sample_note}

| {t('report_metric')} | {t('report_value')} |
|---|---:|
| Precision | {result.precision:.6f} |
| Recall | {result.recall:.6f} |
| F1 | {result.f1:.6f} |
| ROC-AUC | {result.roc_auc:.6f} |
| PR-AUC | {result.pr_auc:.6f} |
| Accuracy ({t('report_reference')}) | {result.accuracy:.6f} |

### {t('report_confusion')}

| | {t('pred_negative')} | {t('pred_positive')} |
|---|---:|---:|
| {t('actual_negative')} | {result.true_negatives} | {result.false_positives} |
| {t('actual_positive')} | {result.false_negatives} | {result.true_positives} |

## {t('report_interpretation')}

{interpretation}

## {t('report_limitations')}

- {t('limitation_scope')}
- {t('limitation_cv')}
- {t('limitation_tuning')}
- {t('limitation_causal')}
- {t('limitation_quality')}
- {t('report_no_revision')}
"""


def build_metrics_export(
    baseline_comparison: pd.DataFrame,
    threshold_comparison: pd.DataFrame,
    final_result: FinalTestResult,
) -> pd.DataFrame:
    """Combine validation and final-test metrics in a machine-readable table."""
    rows: list[dict[str, Any]] = []
    for _, row in baseline_comparison.iterrows():
        rows.append({"stage": "validation_default_0.5", **row.to_dict()})
    for _, row in threshold_comparison.iterrows():
        rows.append({"stage": "validation_selected_threshold", **row.to_dict()})
    rows.append(
        {
            "stage": "final_test_locked_threshold",
            "Model": final_result.model_name,
            "Selected threshold": final_result.threshold,
            "PR-AUC": final_result.pr_auc,
            "ROC-AUC": final_result.roc_auc,
            "Precision": final_result.precision,
            "Recall": final_result.recall,
            "F1": final_result.f1,
            "Accuracy": final_result.accuracy,
            "True positives": final_result.true_positives,
            "True negatives": final_result.true_negatives,
            "False positives": final_result.false_positives,
            "False negatives": final_result.false_negatives,
        }
    )
    return pd.DataFrame(rows)


def serialize_model_package(
    model: Pipeline,
    *,
    model_name: str,
    selected_threshold: float,
    target_column: str,
    positive_class: object,
    feature_columns: list[str],
) -> bytes:
    """Serialize a fitted pipeline and reuse metadata without including raw data."""
    package = {
        "format_version": 1,
        "model_name": model_name,
        "pipeline": model,
        "selected_threshold": float(selected_threshold),
        "target_column": target_column,
        "positive_class": positive_class,
        "feature_columns": feature_columns,
        "prediction_rule": "positive when predict_proba[:, 1] >= selected_threshold",
    }
    buffer = BytesIO()
    joblib.dump(package, buffer)
    return buffer.getvalue()
