import io
import zipfile
from dataclasses import replace

import pytest
from openpyxl import load_workbook

from autoanalyst.domain.errors import ResourceError, SchemaError
from autoanalyst.reporting.binary_formats import chart_figures
from autoanalyst.reporting.render import _render_bytes
from tests.v2.test_chart_presentation import histogram_result


@pytest.mark.parametrize("language", ["tr", "en"])
def test_real_pdf_xlsx_use_saved_chart_bins_and_literal_cells(language):
    result = histogram_result(name="Ölçüm = gelir")
    view, figure = next(chart_figures(result, language))
    assert [patch.get_height() for patch in figure.axes[0].patches] == [2, 5, 0]
    figure.clear()
    pdf, mime = _render_bytes(result, "pdf", language)
    assert mime == "application/pdf" and pdf.startswith(b"%PDF-") and b"%%EOF" in pdf[-100:]
    workbook, mime = _render_bytes(result, "xlsx", language)
    assert mime.endswith("spreadsheetml.sheet")
    saved = load_workbook(io.BytesIO(workbook))
    assert list(saved["Table_2"].values) == [result.tables[1].columns, *result.tables[1].rows]
    assert saved["Chart_1"]["A2"].value == f"Source: {view.table_id}"
    assert len(saved["Chart_1"]._images) == 1
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        assert "xl/media/image1.png" in archive.namelist()
    table = replace(result.tables[0], rows=(("=1+2", "+SUM(A1:A3)"),))
    literal = replace(result, tables=(table,))
    workbook, _ = _render_bytes(literal, "xlsx", language)
    saved = load_workbook(io.BytesIO(workbook))
    assert saved["Table_1"]["A2"].value == "=1+2"
    assert saved["Table_1"]["A2"].data_type == "s"


def test_spreadsheet_never_silently_truncates_cells():
    result = histogram_result()
    table = replace(result.tables[0], rows=(("x", "a" * 32768),))
    with pytest.raises(ResourceError):
        _render_bytes(replace(result, tables=(table,)), "xlsx", "tr")


def test_unsupported_charts_remain_readable_not_executed():
    result = histogram_result()
    result = replace(result, charts=(replace(result.charts[0], chart_type="unknown"),))
    for fmt in ("pdf", "xlsx"):
        assert _render_bytes(result, fmt, "tr")[0]


def test_pdf_treats_user_math_like_text_literally():
    result = histogram_result(name=r"$\invalidcommand{user input}$")
    assert _render_bytes(result, "pdf", "tr")[0].startswith(b"%PDF-")


def test_xlsx_rejects_xml_controls_with_domain_error():
    result = histogram_result()
    table = replace(result.tables[0], rows=(("x", "null\x00byte"),))
    with pytest.raises(SchemaError):
        _render_bytes(replace(result, tables=(table,)), "xlsx", "en")


def test_xlsx_checks_report_budget_before_allocating_workbook(monkeypatch):
    import autoanalyst.reporting.binary_formats as module

    result = histogram_result()
    table = replace(result.tables[0], rows=(("x", "value"),) * 250_000)
    monkeypatch.setattr(module, "Workbook", lambda: pytest.fail("must check before allocation"))
    with pytest.raises(ResourceError):
        _render_bytes(replace(result, tables=(table,)), "xlsx", "en")
