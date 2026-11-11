from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from autoanalyst.domain.codec import FrozenDict
from autoanalyst.domain.datasets import ColumnRole, ColumnUsage, Project, SemanticType
from autoanalyst.domain.errors import DataError, ErrorCode
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.results import AnalysisResult, Metric, MetricValueState, ResultOutcome

NOW = datetime(2026, 2, 3, tzinfo=UTC)
SHA = "a" * 64


def _role(*usages: ColumnUsage) -> ColumnRole:
    return ColumnRole(
        column_id=str(uuid4()),
        semantic_type=SemanticType.CATEGORICAL,
        usages=usages,
        confirmed=True,
    )


def _budget() -> ResourceBudget:
    return ResourceBudget(1024, 2048, 30)


def test_invalid_column_role_combinations_are_rejected() -> None:
    with pytest.raises(ValueError, match="excluded"):
        _role(ColumnUsage.EXCLUDED, ColumnUsage.FEATURE)
    with pytest.raises(ValueError, match="cannot also be features"):
        _role(ColumnUsage.FEATURE, ColumnUsage.TARGET)


def test_invalid_binary_analysis_spec_without_exactly_one_target_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one target"):
        AnalysisSpec(
            spec_id=str(uuid4()),
            project_id=str(uuid4()),
            module_id=AnalysisModuleId.BINARY_CLASSIFICATION,
            module_version="2.0",
            operation="train_validate",
            input_version_id=str(uuid4()),
            column_roles=(_role(ColumnUsage.FEATURE),),
            parameters=FrozenDict(),
            seed=42,
            resource_budget=_budget(),
            spec_hash=SHA,
            created_at=NOW,
        )


def test_domain_models_are_frozen_and_json_parameters_are_deeply_immutable() -> None:
    project = Project(str(uuid4()), "Frozen", NOW, NOW)
    with pytest.raises(FrozenInstanceError):
        project.name = "Changed"  # type: ignore[misc]

    parameters = FrozenDict({"nested": {"items": [1, 2]}})
    with pytest.raises(TypeError):
        parameters["new"] = 1  # type: ignore[index]
    assert parameters["nested"]["items"] == (1, 2)


def test_error_model_has_stable_code_and_structured_context_only() -> None:
    error = DataError({"column_id": "amount", "reason": "invalid_type"})

    assert error.code is ErrorCode.DATA_ERROR
    assert str(error) == "data_error"
    assert error.context["reason"] == "invalid_type"
    assert not hasattr(error, "user_message")


def test_metric_requires_explicit_non_finite_state() -> None:
    undefined = Metric(
        metric_id=str(uuid4()),
        name="roc_auc",
        value_state=MetricValueState.NOT_DEFINED,
        reason_code="single_class",
    )
    infinity = Metric(
        metric_id=str(uuid4()),
        name="odds_ratio",
        value_state=MetricValueState.POSITIVE_INFINITY,
    )
    with pytest.raises(ValueError, match="finite numeric"):
        Metric(
            metric_id=str(uuid4()),
            name="bad",
            value_state=MetricValueState.FINITE,
            value=float("nan"),
        )

    assert undefined.value is None
    assert infinity.value is None


def test_result_model_is_ui_independent() -> None:
    result = AnalysisResult(
        result_id=str(uuid4()),
        run_id=str(uuid4()),
        module_id=AnalysisModuleId.PROFILING,
        outcome=ResultOutcome.SUCCEEDED,
        methodology={"method_code": "basic_profile"},
        sample_summary={"rows": 12},
        provenance={"input_fingerprint": SHA},
    )

    module_source = inspect.getsource(inspect.getmodule(AnalysisResult))
    assert "streamlit" not in module_source.lower()
    assert result.sample_summary["rows"] == 12
