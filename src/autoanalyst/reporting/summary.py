"""Deterministic, localized executive summary of the saved result only."""

from dataclasses import dataclass

from ..domain.results import AnalysisResult
from ..localization import I18N, label, result_finding_text
from .charts import summary_metrics


@dataclass(frozen=True, slots=True)
class ResultSummary:
    lines: tuple[str, ...]
    scope: str


def result_summary(result: AnalysisResult, language: str) -> ResultSummary:
    t = I18N[language]
    lines = [
        t["summary_status"].format(
            module=label(result.module_id.value, language),
            outcome=label(result.outcome.value, language),
        ),
        t["summary_identity"].format(result=result.result_id, run=result.run_id),
    ]
    if version := result.provenance.get("input_version_id"):
        lines.append(t["summary_version"].format(version=version))
    for key, value in result.sample_summary.items():
        if type(value) in {int, float}:
            lines.append(f"{label(key, language)}: {value}")
    for metric in summary_metrics(result):
        text = f"{label(metric.name, language)}: {metric.value}"
        if metric.unit:
            text += f" {label(metric.unit, language)}"
        if metric.sample_size is not None:
            text += f" (n={metric.sample_size})"
        lines.append(text)
    cautions = [f for f in result.findings if f.severity.value in {"warning", "error"}]
    lines.extend(result_finding_text(result, f, language) for f in cautions[:3])
    if len(cautions) > 3:
        lines.append(t["summary_more_findings"].format(count=len(cautions) - 3))
    return ResultSummary(tuple(lines), t["summary_scope"])
