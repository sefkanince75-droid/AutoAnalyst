import pandas as pd

from src.data_quality import build_data_quality_findings
from src.diagnostics import diagnose_dataset


def test_data_quality_findings_report_concrete_issues() -> None:
    dataframe = pd.DataFrame(
        {
            "customer_id": [f"c-{index}" for index in range(20)],
            "constant": ["fixed"] * 20,
            "value": [1.0, None] + list(range(18)),
            "target": [0] * 19 + [1],
        }
    )
    diagnostics = diagnose_dataset(dataframe, "target")
    findings = build_data_quality_findings(dataframe, diagnostics)
    keys = {finding.message_key for finding in findings}
    assert "quality_missing" in keys
    assert "quality_constants" in keys
    assert "quality_ids" in keys
    assert "quality_imbalance" in keys
    assert all(finding.severity in {"ok", "warning", "issue"} for finding in findings)


def test_data_quality_summary_does_not_modify_data() -> None:
    dataframe = pd.DataFrame({"feature": [1, 2, 3, 4, 5, 6], "target": [0, 0, 0, 1, 1, 1]})
    original = dataframe.copy(deep=True)
    diagnostics = diagnose_dataset(dataframe, "target")
    build_data_quality_findings(dataframe, diagnostics)
    pd.testing.assert_frame_equal(dataframe, original)
