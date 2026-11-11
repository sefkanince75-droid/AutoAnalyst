"""Regressions from the installed Windows V2 acceptance journey."""

import re
from types import SimpleNamespace

import pandas as pd
import pytest

from autoanalyst.analyses.binary.module import _identifier_value
from autoanalyst.localization import I18N, failure_text, finding_text, result_finding_text
from autoanalyst.reporting.render import _render_bytes
from autoanalyst.ui.run_monitor import run_failure_text
from tests.v2.test_chart_presentation import histogram_result
from tests.v2.test_ui_apptest import ui_workspace as ui_workspace


def test_pdf_chart_pages_use_the_same_a4_media_box_as_text():
    pdf, _ = _render_bytes(histogram_result(), "pdf", "tr")
    boxes = re.findall(rb"/MediaBox\s*\[([^]]+)\]", pdf)
    assert len(boxes) >= 3
    sizes = [tuple(float(value) for value in box.split()) for box in boxes]
    assert all(size == sizes[0] for size in sizes)
    assert sizes[0][2:] == pytest.approx((595.44, 841.68))


def test_failure_reason_survives_event_presentation_without_private_context():
    reason = "binary.dataset_level_leakage"
    runtime = SimpleNamespace(
        run_store=SimpleNamespace(
            list_events=lambda *a, **k: [
                {"event_type": "failed", "payload": {"reason": reason, "secret": "private-data"}}
            ]
        )
    )
    run = SimpleNamespace(run_id="example", error_code="method_not_applicable")
    for language in ("en", "tr"):
        assert run_failure_text(runtime, run, language) == I18N[language]["error." + reason]
        assert "private" not in failure_text("unknown", "private-data", language)


@pytest.mark.parametrize(
    "value, expected",
    [
        (pd.NA, None),
        (float("nan"), None),
        (pd.Timestamp("2026-11-08"), "2026-11-08T00:00:00"),
        ("00101", "00101"),
        (9007199254740993, 9007199254740993),
    ],
)
def test_prediction_identifier_values_preserve_missing_dates_and_exact_ids(value, expected):
    assert _identifier_value(value) == expected


def test_profile_finding_names_the_affected_column():
    text = finding_text("profiling.high_cardinality", "tr", {"column_name": "musteri_id"})
    assert text.startswith("musteri_id:")


def test_existing_findings_resolve_column_names_from_saved_tables():
    result = histogram_result(name="Müşteri yaşı")
    column_id = result.tables[0].rows[0][0]
    finding = SimpleNamespace(
        parameters={}, affected_column_ids=(column_id,), code="profiling.high_cardinality"
    )
    assert result_finding_text(result, finding, "tr").startswith("Müşteri yaşı:")
