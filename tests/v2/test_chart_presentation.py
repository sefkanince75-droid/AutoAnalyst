from __future__ import annotations

from dataclasses import replace
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest

from autoanalyst.domain.plans import AnalysisModuleId
from autoanalyst.domain.results import (
    AnalysisResult,
    ChartSpec,
    Metric,
    MetricValueState,
    ResultOutcome,
    ResultTable,
)
from autoanalyst.localization import I18N
from autoanalyst.reporting.charts import chart_views, summary_metrics
from autoanalyst.reporting.render import _render_bytes


def histogram_result(rows=((0, 1, 2), (1, 3, 5), (3, 4, 0)), name="Revenue"):
    column_id = str(uuid4())
    table = ResultTable(str(uuid4()), ("bin_left", "bin_right", "count"), rows)
    profile = ResultTable(str(uuid4()), ("column_id", "display_name"), ((column_id, name),))
    chart = ChartSpec(
        str(uuid4()),
        "histogram",
        table.table_id,
        {"x": "bin_left", "x2": "bin_right", "y": "count", "column_id": column_id},
    )
    return AnalysisResult(
        str(uuid4()),
        str(uuid4()),
        AnalysisModuleId.PROFILING,
        ResultOutcome.SUCCEEDED,
        tables=(profile, table),
        charts=(chart,),
    )


@pytest.mark.parametrize("language", ["tr", "en"])
def test_histogram_preserves_saved_edges_counts_and_matches_html(language):
    result = histogram_result()
    (view,) = chart_views(result, language)
    assert view.notice is None
    root = ET.fromstring(view.svg)
    bars = root.findall(".//{http://www.w3.org/2000/svg}rect[@data-count]")
    assert [
        (float(b.attrib["data-left"]), float(b.attrib["data-right"]), int(b.attrib["data-count"]))
        for b in bars
    ] == list(result.tables[1].rows)
    assert float(bars[1].attrib["width"]) == pytest.approx(2 * float(bars[0].attrib["width"]))
    assert float(bars[2].attrib["height"]) == 0
    html = _render_bytes(result, "html", language)[0].decode()
    assert view.svg in html
    assert f'href="#table-{view.table_id}"' in html
    assert "<script" not in html and "<img" not in html
    assert chart_views(result, language) == (view,)


@pytest.mark.parametrize(
    "case,reason",
    [
        ("unsupported", "chart_unsupported"),
        ("missing", "chart_missing_table"),
        ("empty", "chart_empty"),
        ("too_many", "chart_too_large"),
        ("field", "chart_invalid"),
        ("repeated_field", "chart_invalid"),
        ("duplicate_column", "chart_invalid"),
        ("text", "chart_invalid"),
        ("boolean", "chart_invalid"),
        ("negative", "chart_invalid"),
        ("fractional", "chart_invalid"),
        ("reversed", "chart_invalid"),
        ("overlap", "chart_invalid"),
        ("unsorted", "chart_invalid"),
    ],
)
def test_unrenderable_charts_have_localized_fallback_without_losing_tables(case, reason):
    result = histogram_result()
    chart = result.charts[0]
    table = result.tables[1]
    if case == "unsupported":
        chart = replace(chart, chart_type="<script>unknown</script>")
    elif case == "missing":
        chart = replace(chart, data_table_id=str(uuid4()))
    elif case == "field":
        chart = replace(chart, encoding={"x": "absent", "x2": "bin_right", "y": "count"})
    elif case == "repeated_field":
        chart = replace(chart, encoding={"x": "count", "x2": "count", "y": "count"})
    elif case == "duplicate_column":
        table = replace(table, columns=("count", "count", "count"))
    else:
        rows = {
            "empty": (),
            "too_many": tuple((i, i + 1, 1) for i in range(1001)),
            "text": ((0, 1, "2"),),
            "boolean": ((0, 1, True),),
            "negative": ((0, 1, -1),),
            "fractional": ((0, 1, 0.5),),
            "reversed": ((1, 0, 1),),
            "overlap": ((0, 2, 1), (1, 3, 1)),
            "unsorted": ((2, 3, 1), (0, 1, 1)),
        }[case]
        table = replace(table, rows=rows)
    result = replace(result, tables=(result.tables[0], table), charts=(chart,))
    (view,) = chart_views(result, "tr")
    assert view.svg is None and view.notice == I18N["tr"][reason]
    html = _render_bytes(result, "html", "tr")[0].decode()
    assert view.notice in html
    assert f'id="table-{table.table_id}"' in html
    assert "<script>" not in html


def test_chart_labels_are_escaped_and_user_column_names_are_not_translated():
    result = histogram_result(name='<script>alert("count")</script> & Revenue')
    (view,) = chart_views(result, "tr")
    assert "Dağılım:" in view.title
    assert "<script>" not in view.svg and "&lt;script&gt;" in view.svg
    assert "Revenue" in view.svg
    ET.fromstring(view.svg)


@pytest.mark.parametrize("rows", [((1, 2, 0),), ((-1e308, 0, 2), (0, 1e308, 3))])
def test_zero_counts_and_extreme_finite_ranges_render_without_nonfinite_coordinates(rows):
    (view,) = chart_views(histogram_result(rows), "en")
    assert view.svg and view.notice is None
    assert "nan" not in view.svg.lower() and "inf" not in view.svg.lower()
    ET.fromstring(view.svg)


def test_summary_uses_only_recorded_overall_finite_metrics_and_handles_no_charts():
    finite = tuple(
        Metric(str(uuid4()), f"metric_{i}", MetricValueState.FINITE, value=i) for i in range(5)
    )
    undefined = Metric(str(uuid4()), "undefined", MetricValueState.NOT_DEFINED, reason_code="empty")
    dimensioned = replace(finite[0], dimensions={"model_id": "dummy"})
    result = replace(histogram_result(), metrics=(undefined, dimensioned, *finite), charts=())
    assert summary_metrics(result) == finite[:4]
    assert chart_views(result, "en") == ()
    html = _render_bytes(result, "html", "en")[0].decode()
    assert "<h2>Summary</h2>" in html and "<h2>Charts</h2>" not in html
    assert "not_defined" not in html
