from __future__ import annotations

import json
from io import BytesIO
from string import Formatter
from uuid import uuid4
from zipfile import ZipFile

from autoanalyst.domain.errors import SchemaError
from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.domain.results import (
    AnalysisResult,
    Finding,
    FindingSeverity,
    Metric,
    MetricValueState,
    ResultOutcome,
    ResultTable,
)
from autoanalyst.localization import I18N, error_text, finding_text
from autoanalyst.reporting.render import _render_bytes


def test_catalog_languages_have_identical_keys_and_placeholders():
    assert I18N["en"].keys() == I18N["tr"].keys()
    for key, value in I18N["en"].items():
        fields = {field for _, field, _, _ in Formatter().parse(value) if field}
        translated = {field for _, field, _, _ in Formatter().parse(I18N["tr"][key]) if field}
        assert fields == translated, key


def test_error_and_finding_messages_do_not_expose_internal_payloads():
    error = SchemaError({"reason": "private_unknown_code", "secret": "private-row-content"})
    assert error_text(error, "tr") == I18N["tr"]["schema_error"]
    assert "private" not in finding_text(
        "private_unknown_finding", "tr", {"secret": "private-row-content"}
    )
    assert "değişti" in error_text(SchemaError({"reason": "preparation_preview_stale"}), "tr")
    assert "7" in finding_text("profiling.non_finite", "tr", {"count": 7})


def _result():
    return AnalysisResult(
        result_id=str(uuid4()),
        run_id=str(uuid4()),
        module_id=AnalysisModuleId.PROFILING,
        outcome=ResultOutcome.SUCCEEDED,
        metrics=(Metric(str(uuid4()), "row_count", MetricValueState.FINITE, value=12),),
        findings=(
            Finding(
                str(uuid4()),
                "profiling.non_finite",
                FindingSeverity.WARNING,
                parameters={"count": 2},
            ),
        ),
        tables=(
            ResultTable(
                str(uuid4()),
                ("display_name", "is_constant"),
                (("<script>mean|user</script>", True),),
            ),
        ),
    )


def test_human_reports_are_localized_and_machine_exports_keep_stable_keys():
    result = _result()
    html = _render_bytes(result, "html", "tr")[0].decode()
    assert '<html lang="tr">' in html
    assert "<h2>Ölçütler</h2>" in html
    assert "<h2>Yöntem</h2>" in html
    assert "Satır sayısı" in html
    assert "Sütunda 2 sonsuz değer var." in html
    assert "Doğru" in html
    assert "&lt;script&gt;mean|user&lt;/script&gt;" in html
    assert "<script>" not in html
    markdown = _render_bytes(result, "markdown", "tr")[0].decode()
    assert "## Bulgular" in markdown and "## Veri ve çalışma kaynağı" in markdown
    assert "mean\\|user" in markdown
    payload = json.loads(_render_bytes(result, "json", "tr")[0])
    assert payload["result"]["metrics"][0]["name"] == "row_count"
    assert payload["result"]["metrics"][0]["value"] == 12
    with ZipFile(BytesIO(_render_bytes(result, "csv", "tr")[0])) as archive:
        assert "row_count" in archive.read("metrics.csv").decode("utf-8-sig")
        assert "display_name,is_constant" in archive.read("table_1.csv").decode("utf-8-sig")
