import json
from dataclasses import replace
from html import escape
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.domain.results import (
    AnalysisResult,
    Finding,
    Metric,
    MetricValueState,
    ResultOutcome,
)
from autoanalyst.localization import I18N
from autoanalyst.reporting.render import _render_bytes
from autoanalyst.reporting.summary import result_summary


@pytest.mark.parametrize("language", ["tr", "en"])
@pytest.mark.parametrize("outcome", list(ResultOutcome))
def test_all_report_formats_share_saved_summary(language, outcome):
    result = AnalysisResult(
        str(uuid4()),
        str(uuid4()),
        AnalysisModuleId.PROFILING,
        outcome,
        metrics=(
            Metric(
                str(uuid4()), "row_count", MetricValueState.FINITE, 17, unit="rows", sample_size=17
            ),
        ),
        sample_summary={"rows_used": 17, "rows_excluded": 2},
        provenance={"input_version_id": str(uuid4())},
    )
    before = result_summary(result, language)
    html = _render_bytes(result, "html", language)[0].decode()
    markdown = _render_bytes(result, "markdown", language)[0].decode()
    payload = json.loads(_render_bytes(result, "json", language)[0])
    with ZipFile(BytesIO(_render_bytes(result, "csv", language)[0])) as archive:
        text = archive.read("summary.txt").decode()
        assert "metrics.csv" in archive.namelist()
    for line in before.lines:
        assert escape(line) in html
        assert escape(line, quote=False) in markdown
        assert line in payload["summary"]["lines"] and line in text
    assert before.scope == payload["summary"]["scope"]
    assert before.scope in html and before.scope in markdown and before.scope in text
    assert result_summary(result, language) == before
    assert result.result_id in html and result.provenance["input_version_id"] in html


def test_summary_does_not_mix_splits_or_promote_undefined_metrics():
    metrics = tuple(
        Metric(str(uuid4()), f"metric_{i}", MetricValueState.FINITE, i) for i in range(6)
    )
    undefined = Metric(str(uuid4()), "undefined", MetricValueState.NOT_DEFINED, reason_code="empty")
    scoped = replace(metrics[0], name="held_out_metric", dimensions={"split": "test"})
    findings = tuple(Finding(str(uuid4()), f"warning_{i}", "warning") for i in range(5))
    result = AnalysisResult(
        str(uuid4()),
        str(uuid4()),
        AnalysisModuleId.BINARY_CLASSIFICATION,
        ResultOutcome.PARTIAL,
        metrics=(undefined, scoped, *metrics),
        findings=findings,
    )
    overview = result_summary(result, "en")
    combined = "\n".join(overview.lines)
    assert "held_out_metric" not in combined and "undefined" not in combined
    assert "metric_3: 3" in combined and "metric_4" not in combined
    assert combined.count(I18N["en"]["unknown_finding"]) == 3
    assert I18N["en"]["summary_more_findings"].format(count=2) in combined


def test_summary_escapes_untrusted_saved_text_in_reports():
    result = AnalysisResult(
        str(uuid4()),
        str(uuid4()),
        AnalysisModuleId.PROFILING,
        ResultOutcome.SUCCEEDED,
        provenance={"input_version_id": "<script>alert(1)</script>|x\ny"},
    )
    html = _render_bytes(result, "html", "en")[0].decode()
    md = _render_bytes(result, "markdown", "en")[0].decode()
    assert "<script>" not in html and "&lt;script&gt;" in html
    summary = md.split("## Metrics")[0]
    assert "<script>" not in summary and "\\|x<br>y" in summary
