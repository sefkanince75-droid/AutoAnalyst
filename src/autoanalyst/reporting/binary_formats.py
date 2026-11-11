"""Offline paginated reports and spreadsheets from saved results only."""

import io
import textwrap

from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Font, PatternFill
from openpyxl.utils.exceptions import IllegalCharacterError

from ..domain.codec import canonical_json, to_primitive
from ..domain.errors import ResourceError, SchemaError
from ..localization import I18N, cell_text, label, result_finding_text
from .charts import _bins, chart_views, table_title
from .summary import result_summary


def chart_figures(result, language):
    """Reuse the validated persisted bins, without calculating a new histogram."""
    tables = {table.table_id: table for table in result.tables}
    specs = {chart.chart_id: chart for chart in result.charts}
    for view in chart_views(result, language):
        if view.svg is None:
            yield view, None
            continue
        bins = _bins(specs[view.chart_id], tables[view.table_id])
        scale = max(1.0, *(abs(v) for left, right, _ in bins for v in (left, right)))
        figure = Figure(figsize=(9, 4.8), layout="constrained")
        axis = figure.subplots()
        axis.bar(
            [left / scale for left, _, _ in bins],
            [count for _, _, count in bins],
            width=[right / scale - left / scale for left, right, _ in bins],
            align="edge",
            color="#2563eb",
            edgecolor="white",
        )
        axis.set_title(textwrap.fill(view.title, 70), parse_math=False)
        axis.set_ylabel(label("count", language))
        axis.set_xlabel(I18N[language]["bin_range"] + (f" (× {scale:g})" if scale != 1 else ""))
        axis.grid(axis="y", alpha=0.2)
        yield view, figure


def _overview(result, language):
    from .render import _metric_text

    t = I18N[language]
    overview = result_summary(result, language)
    return [
        t["report_title"],
        f"{t['module']}: {label(result.module_id.value, language)}",
        f"{t['outcome']}: {label(result.outcome.value, language)}",
        f"{label('result_id', language)}: {result.result_id}",
        "",
        *overview.lines,
        overview.scope,
        "",
        t["metrics"],
        *(
            f"{label(m.name, language)}: {_metric_text(m, language)} | {canonical_json(dict(m.dimensions))}"
            for m in result.metrics
        ),
        "",
        t["findings"],
        *(result_finding_text(result, f, language) for f in result.findings),
    ]


def _pdf(result, language):
    output = io.BytesIO()
    with PdfPages(
        output, metadata={"Title": I18N[language]["report_title"], "Creator": "AutoAnalyst"}
    ) as pdf:
        page_number = 0

        def page(lines):
            nonlocal page_number
            # Fixed line and column budgets prevent clipping and unbounded pagination.
            if sum(len(str(line)) for line in lines) > 1_000_000:
                raise ResourceError({"reason": "report_page_limit"})
            wrapped = [
                part
                for line in lines
                for logical in (str(line).splitlines() or [""])
                for part in (textwrap.wrap(logical.expandtabs(4), 90) or [""])
            ]
            for start in range(0, len(wrapped), 47):
                page_number += 1
                if page_number > 250:
                    raise ResourceError({"reason": "report_page_limit"})
                figure = Figure(figsize=(8.27, 11.69))
                figure.text(0.08, 0.94, "AutoAnalyst", size=16, weight="bold", color="#1e3a8a")
                figure.text(
                    0.08,
                    0.89,
                    "\n".join(wrapped[start : start + 47]),
                    va="top",
                    fontsize=9,
                    linespacing=1.5,
                    family="DejaVu Sans Mono",
                    parse_math=False,
                )
                figure.text(0.9, 0.035, str(page_number), ha="right", size=9)
                pdf.savefig(figure)
                figure.clear()

        page(_overview(result, language))
        # Two charts per A4 page, preserving each plot's original aspect ratio.
        from matplotlib.image import imread

        canvas = None
        slot = 0
        for view, figure in chart_figures(result, language):
            if figure is None:
                page([view.title, view.notice])
                continue
            if canvas is None:
                canvas = Figure(figsize=(8.27, 11.69))
                canvas.text(0.08, 0.95, I18N[language]["charts"], size=16)
            image = io.BytesIO()
            figure.savefig(image, format="png", dpi=140)
            figure.clear()
            image.seek(0)
            axis = canvas.add_axes((0.07, 0.53 if slot == 0 else 0.11, 0.86, 0.35))
            axis.imshow(imread(image, format="png"))
            axis.axis("off")
            slot += 1
            if slot == 2:
                page_number += 1
                if page_number > 250:
                    raise ResourceError({"reason": "report_page_limit"})
                canvas.text(0.9, 0.035, str(page_number), ha="right", size=9)
                pdf.savefig(canvas)
                canvas.clear()
                canvas, slot = None, 0
        if canvas is not None:
            page_number += 1
            if page_number > 250:
                raise ResourceError({"reason": "report_page_limit"})
            canvas.text(0.9, 0.035, str(page_number), ha="right", size=9)
            pdf.savefig(canvas)
            canvas.clear()
        lines = []
        for index, table in enumerate(result.tables, 1):
            # The plotted bin values remain available in full machine/table exports.
            if {"bin_left", "bin_right", "count"}.issubset(table.columns):
                continue
            lines.extend(["", table_title(table, index, language)])
            selected = [i for i, column in enumerate(table.columns) if column not in {"column_id"}]
            for row_index, row in enumerate(table.rows[:100], 1):
                values = []
                for i in selected:
                    column, value = table.columns[i], row[i]
                    text = (
                        format(value, ".6g")
                        if isinstance(value, float)
                        else str(cell_text(value, column, language))
                    )
                    values.append(
                        f"{label(column, language)}: {text[:120]}"
                        + (" […]" if len(text) > 120 else "")
                    )
                lines.append(f"[{row_index}] " + " | ".join(values))
            if len(table.rows) > 100:
                lines.append(I18N[language]["first_rows"].format(limit=100))
        lines.extend(["", I18N[language]["technical_appendix_hint"]])
        page(lines)
    return output.getvalue()


def _xlsx(result, language):
    cells = sum(len(table.columns) * (1 + len(table.rows)) for table in result.tables)
    if cells + len(result.metrics) * 6 > 500_000 or len(result.charts) > 50:
        raise ResourceError({"reason": "report_excel_shape_limit"})
    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet(name, rows):
        ws = workbook.create_sheet(name)
        for row_index, row in enumerate(rows, 1):
            if row_index > 1_048_576 or len(row) > 16_384:
                raise ResourceError({"reason": "report_excel_shape_limit"})
            for column_index, value in enumerate(row, 1):
                if isinstance(value, (dict, list, tuple)):
                    value = canonical_json(to_primitive(value))
                if isinstance(value, str) and len(value) > 32767:
                    raise ResourceError({"reason": "report_excel_cell_limit"})
                try:
                    cell = ws.cell(row_index, column_index, value)
                except IllegalCharacterError as exc:
                    raise SchemaError({"reason": "report_excel_invalid_character"}) from exc
                if isinstance(value, str):
                    cell.data_type = "s"  # Formula-like source values stay literal text.
                if row_index == 1:
                    cell.font = Font(color="FFFFFF", bold=True)
                    cell.fill = PatternFill("solid", fgColor="1E3A8A")
        ws.freeze_panes = "A2"
        for column in ws.columns:
            ws.column_dimensions[column[0].column_letter].width = min(
                65, max(18, max(len(str(c.value or "")) for c in column[:100]) + 2)
            )
        return ws

    sheet(I18N[language]["sheet_overview"], ((line,) for line in _overview(result, language)))
    sheet(
        I18N[language]["sheet_metrics"],
        [
            ("name", "value", "state", "sample_size", "interval", "dimensions"),
            *(
                (
                    m.name,
                    m.value,
                    m.value_state.value,
                    m.sample_size,
                    m.interval,
                    dict(m.dimensions),
                )
                for m in result.metrics
            ),
        ],
    )
    sheet(
        I18N[language]["sheet_metadata"],
        [
            ("key", "value"),
            ("result_id", result.result_id),
            ("methodology", to_primitive(result.methodology)),
            ("provenance", to_primitive(result.provenance)),
        ],
    )
    for index, table in enumerate(result.tables, 1):
        sheet(
            f"{I18N[language]['sheet_table']}_{index}",
            (row for row in (table.columns, *table.rows)),
        )
    images = []
    for index, (view, figure) in enumerate(chart_figures(result, language), 1):
        source_index = next(
            (i for i, table in enumerate(result.tables, 1) if table.table_id == view.table_id), None
        )
        source_text = (
            I18N[language]["chart_source"].format(index=source_index)
            if source_index is not None
            else I18N[language]["chart_missing_table"]
        )
        ws = sheet(f"{I18N[language]['sheet_chart']}_{index}", [(view.title,), (source_text,)])
        if figure is None:
            ws.cell(3, 1, view.notice)
        else:
            image = io.BytesIO()
            figure.savefig(image, format="png", dpi=120)
            figure.clear()
            image.seek(0)
            images.append(image)
            ws.add_image(Image(image), "A4")
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def render_binary(result, fmt, language):
    if fmt == "pdf":
        return _pdf(result, language), "application/pdf"
    return _xlsx(
        result, language
    ), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
