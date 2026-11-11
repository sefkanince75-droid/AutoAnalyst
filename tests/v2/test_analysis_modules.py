from __future__ import annotations

from uuid import uuid4

from autoanalyst.analyses.comparisons import ComparisonModule
from autoanalyst.analyses.profiling import ProfilingModule
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget


class _NeverCancelled:
    def is_cancelled(self):
        return False


def _spec(ws, module_id, operation, parameters):
    payload = {
        "module": module_id.value,
        "operation": operation,
        "parameters": parameters,
        "version": ws.imported.version.version_id,
    }
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=ws.project.project_id,
        module_id=module_id,
        module_version="2.0",
        operation=operation,
        input_version_id=ws.imported.version.version_id,
        column_roles=(),
        parameters=parameters,
        seed=42,
        resource_budget=ResourceBudget(1_000_000_000, 1_000_000_000, 60, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _context(spec):
    from autoanalyst.analyses.contract import ExecutionContext

    return ExecutionContext(str(uuid4()), spec.input_version_id, 42, _NeverCancelled())


def test_profiling_module_profiles_full_dataset(phase3_workspace):
    module = ProfilingModule(phase3_workspace.catalog, phase3_workspace.store)
    spec = _spec(phase3_workspace, AnalysisModuleId.PROFILING, "profile", {"histogram_bins": 5})
    draft = module.run(spec, _context(spec))
    metrics = {metric.name: metric.value for metric in draft.metrics}
    assert metrics["row_count"] == 5
    assert metrics["column_count"] == 4
    assert any(f.code == "profiling.non_finite" for f in draft.findings)
    assert draft.tables


def test_comparison_group_summary_and_distribution(phase3_workspace):
    module = ComparisonModule(phase3_workspace.catalog, phase3_workspace.store)
    group_id = str(phase3_workspace.column("group")["column_id"])
    num_id = str(phase3_workspace.column("num")["column_id"])
    spec = _spec(
        phase3_workspace,
        AnalysisModuleId.COMPARISON,
        "group_summary",
        {"group_columns": [group_id], "measure_columns": [num_id]},
    )
    draft = module.run(spec, _context(spec))
    assert draft.tables[0].rows

    dist = _spec(
        phase3_workspace,
        AnalysisModuleId.COMPARISON,
        "distribution",
        {"column_id": group_id, "top_k": 10},
    )
    distribution = module.run(dist, _context(dist))
    assert distribution.tables[0].rows
