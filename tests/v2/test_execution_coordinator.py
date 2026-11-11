from __future__ import annotations

from uuid import uuid4

from autoanalyst.analyses.contract import (
    AnalysisDescription,
    ApplicabilityReport,
    ResourceEstimate,
    ResultDraft,
)
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.results import Metric, MetricValueState, ResultOutcome
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage.runs import RunStore


class _Event:
    def __init__(self):
        self.value = False

    def is_set(self):
        return self.value


class _Module:
    def describe(self):
        return AnalysisDescription(AnalysisModuleId.PROFILING, "1.0", ("profile",))

    def validate(self, spec):
        return ()

    def check_applicability(self, spec):
        return ApplicabilityReport()

    def estimate_resources(self, spec):
        return ResourceEstimate(1024, 1024, 0.1, 1)

    def run(self, spec, context):
        context.raise_if_cancelled()
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            metrics=(
                Metric(
                    metric_id=str(uuid4()),
                    name="row_count",
                    value_state=MetricValueState.FINITE,
                    value=5,
                ),
            ),
            methodology={"method": "fixture"},
            provenance={"input_version_id": spec.input_version_id},
        )


def _spec(ws):
    payload = {"version": ws.imported.version.version_id, "module": "profiling"}
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=ws.project.project_id,
        module_id=AnalysisModuleId.PROFILING,
        module_version="1.0",
        operation="profile",
        input_version_id=ws.imported.version.version_id,
        column_roles=(),
        parameters={},
        seed=42,
        resource_budget=ResourceBudget(10_000, 10_000, 10, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def test_coordinator_publishes_result_and_events(phase3_workspace):
    store = RunStore(phase3_workspace.catalog)
    coordinator = ExecutionCoordinator(store)
    spec = _spec(phase3_workspace)
    run = coordinator.create_run(spec, request_key="run-profile")
    completed = coordinator.execute_inline(run.run_id, _Module(), cancellation_event=_Event())
    assert completed.status is RunStatus.COMPLETED
    result = store.result_for_run(run.run_id)
    assert result is not None
    assert result.metrics[0].name == "row_count"
    assert result.metrics[0].value == 5
    assert [e["event_type"] for e in store.list_events(run.run_id)] == [
        "started",
        "progress",
        "completed",
    ]
