"""Closed, deterministic SVG presentation of persisted histogram tables.

No dataset access, histogram calculation, JavaScript or remote resources are used.
Unsupported or malformed chart specs remain visible as a localized notice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

from ..domain.results import AnalysisResult, ChartSpec, Metric, MetricValueState, ResultTable
from ..localization import I18N, label

MAX_BINS = 1000


@dataclass(frozen=True)
class ChartView:
    chart_id: str
    table_id: str
    title: str
    svg: str | None
    notice: str | None = None


def summary_metrics(result: AnalysisResult) -> tuple[Metric, ...]:
    """Show up to four recorded overall metrics; never aggregate or re-score."""
    return tuple(
        metric
        for metric in result.metrics
        if not metric.dimensions and metric.value_state is MetricValueState.FINITE
    )[:4]


def table_title(table: ResultTable, index: int, language: str) -> str:
    t = I18N[language]
    if {"bin_left", "bin_right", "count"}.issubset(table.columns):
        return t["histogram_data"]
    if {"display_name", "null_count", "distinct_non_null"}.issubset(table.columns):
        return t["column_profile"]
    return t["result_table"].format(index=index)


def chart_views(result: AnalysisResult, language: str) -> tuple[ChartView, ...]:
    tables = {table.table_id: table for table in result.tables}
    views = []
    for chart in result.charts:
        title = _title(chart, result, language)
        table = tables.get(chart.data_table_id)
        reason = None
        svg = None
        if chart.chart_type != "histogram":
            reason = "chart_unsupported"
        elif table is None:
            reason = "chart_missing_table"
        elif not table.rows:
            reason = "chart_empty"
        elif len(table.rows) > MAX_BINS:
            reason = "chart_too_large"
        else:
            try:
                bins = _bins(chart, table)
                svg = _histogram_svg(chart.chart_id, bins, title, language)
            except (ValueError, TypeError, KeyError, OverflowError):
                reason = "chart_invalid"
        views.append(
            ChartView(
                chart.chart_id,
                chart.data_table_id,
                title,
                svg,
                I18N[language][reason] if reason else None,
            )
        )
    return tuple(views)


def _title(chart: ChartSpec, result: AnalysisResult, language: str) -> str:
    column_id = chart.encoding.get("column_id")
    for table in result.tables:
        if "column_id" in table.columns and "display_name" in table.columns:
            for row in table.rows:
                if row[table.columns.index("column_id")] == column_id:
                    return I18N[language]["histogram_title"].format(
                        column=str(row[table.columns.index("display_name")])
                    )
    return (
        I18N[language]["histogram"] if chart.chart_type == "histogram" else I18N[language]["chart"]
    )


def _bins(chart: ChartSpec, table: ResultTable) -> tuple[tuple[float, float, int], ...]:
    if len(set(table.columns)) != len(table.columns):
        raise ValueError("Ambiguous chart fields")
    fields = tuple(chart.encoding[key] for key in ("x", "x2", "y"))
    if any(not isinstance(field, str) for field in fields) or len(set(fields)) != 3:
        raise ValueError("Invalid chart fields")
    positions = tuple(table.columns.index(field) for field in fields)
    bins = []
    previous = None
    for row in table.rows:
        values = tuple(row[position] for position in positions)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
            raise ValueError("Non-numeric histogram")
        left, right, count = map(float, values)
        if not all(math.isfinite(v) for v in (left, right, count)):
            raise ValueError("Non-finite histogram")
        if left >= right or count < 0 or not count.is_integer():
            raise ValueError("Invalid bin")
        if previous is not None and left < previous:
            raise ValueError("Unordered or overlapping bins")
        bins.append((left, right, int(values[2])))
        previous = right
    return tuple(bins)


def _histogram_svg(
    chart_id: str, bins: tuple[tuple[float, float, int], ...], title: str, language: str
) -> str:
    t = I18N[language]
    # Normalize before subtracting so extreme finite endpoints do not overflow.
    low, high = bins[0][0], bins[-1][1]
    scale = max(abs(low), abs(high), 1.0)
    start, end = low / scale, high / scale
    span = end - start
    if span <= 0:
        raise ValueError("Unrepresentable histogram range")
    peak = max(1, max(count for _, _, count in bins))

    def x(value):
        return 76 + ((value / scale - start) / span) * 690

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 380" role="img" '
        f'aria-labelledby="title-{chart_id} desc-{chart_id}">',
        f'<title id="title-{chart_id}">{escape(title)}</title>',
        f'<desc id="desc-{chart_id}">{escape(t["histogram_description"])}</desc>',
        '<rect width="820" height="380" fill="#ffffff"/>',
        '<g font-family="system-ui,sans-serif" font-size="12" fill="#1e293b">',
        f'<text x="76" y="25" font-size="17" font-weight="600">{escape(title)}</text>',
    ]
    for tick in range(5):
        y = 300 - tick * 60
        parts += [
            f'<path d="M76 {y}H766" stroke="#e2e8f0"/>',
            f'<text x="65" y="{y + 4}" text-anchor="end">{peak * tick / 4:.6g}</text>',
        ]
    for left, right, count in bins:
        height = count / peak * 240
        tooltip = t["histogram_bin"].format(left=left, right=right, count=count)
        parts.append(
            f'<rect x="{x(left):.6f}" y="{300 - height:.6f}" '
            f'width="{x(right) - x(left):.6f}" height="{height:.6f}" '
            f'fill="#2563eb" stroke="#ffffff" stroke-width="0.5" '
            f'data-left="{left!r}" data-right="{right!r}" data-count="{count}">'
            f"<title>{escape(tooltip)}</title></rect>"
        )
    for tick in range(5):
        # Convex interpolation avoids overflowing high-low for extreme endpoints.
        value = low * (1 - tick / 4) + high * (tick / 4)
        parts.append(
            f'<text x="{76 + tick * 172.5}" y="323" text-anchor="middle">{value:.6g}</text>'
        )
    parts += [
        '<path d="M76 60V300H766" fill="none" stroke="#64748b"/>',
        f'<text x="420" y="358" text-anchor="middle">{escape(t["bin_range"])}</text>',
        f'<text transform="translate(20 180) rotate(-90)" text-anchor="middle">{escape(label("count", language))}</text>',
        "</g></svg>",
    ]
    return "".join(parts)
