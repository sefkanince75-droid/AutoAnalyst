from __future__ import annotations

from uuid import uuid4

from autoanalyst.analyses.binary.module import BinaryClassificationModule
from autoanalyst.application import DatasetService, ProjectService
from autoanalyst.data import CSVIngestor
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.datasets import ColumnRole, ColumnUsage, SemanticType
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage import ArtifactStore, SQLiteCatalog
from autoanalyst.storage.runs import RunStore


class _Event:
    def is_set(self):
        return False


def _workspace(tmp_path):
    catalog = SQLiteCatalog(tmp_path)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Binary")
    dataset = DatasetService(catalog).create(project.project_id, "Training")
    rows = ["x,cat,target"]
    for i in range(160):
        target = i % 2
        x = i + (80 if target else 0)
        rows.append(f"{x},{'a' if i % 3 else 'b'},{target}")
    imported = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=("\n".join(rows) + "\n").encode(),
        original_name="training.csv",
    )
    columns = {row["display_name"]: row for row in catalog.list_columns(imported.version.version_id) if not row["is_system"]}
    return catalog, store, project, imported, columns


def _spec(project, imported, columns, *, operation="train_validate", parameters=None):
    roles = (
        ColumnRole(str(columns["x"]["column_id"]), SemanticType.NUMERIC, (ColumnUsage.FEATURE,), True, True),
        ColumnRole(str(columns["cat"]["column_id"]), SemanticType.CATEGORICAL, (ColumnUsage.FEATURE,), True, True),
        ColumnRole(str(columns["target"]["column_id"]), SemanticType.NUMERIC, (ColumnUsage.TARGET,), True),
    )
    params = parameters or {"positive_label": 1, "minimum_recall": 0.70, "split_policy": "stratified", "class_weight_policy": "none"}
    payload = {"operation": operation, "version": imported.version.version_id, "parameters": params}
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project.project_id,
        module_id=AnalysisModuleId.BINARY_CLASSIFICATION,
        module_version="2.0",
        operation=operation,
        input_version_id=imported.version.version_id,
        column_roles=roles,
        parameters=params,
        seed=42,
        resource_budget=ResourceBudget(2_000_000_000, 2_000_000_000, 120, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def test_binary_train_and_persistent_final_holdout(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    run_store = RunStore(catalog)
    coordinator = ExecutionCoordinator(run_store)
    module = BinaryClassificationModule(catalog, store)

    training_spec = _spec(project, imported, columns)
    training_run = coordinator.create_run(training_spec, request_key="train")
    completed = coordinator.execute_inline(training_run.run_id, module, cancellation_event=_Event())
    assert completed.status is RunStatus.COMPLETED
    result = run_store.result_for_run(training_run.run_id)
    assert result is not None
    assert result.provenance["positive_label"]["value"] == 1
    assert result.provenance["split_artifact"]["sha256"]

    recommendation = result.provenance.get("recommended_model")
    assert recommendation is not None
    final_spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training_run.run_id},
    )
    final_run = coordinator.create_run(final_spec, request_key="final", parent_run_id=training_run.run_id)
    final_completed = coordinator.execute_inline(final_run.run_id, module, cancellation_event=_Event())
    assert final_completed.status is RunStatus.COMPLETED
    lock = module.binary_store.get_holdout_lock(training_run.run_id)
    assert lock is not None
    assert lock["final_run_id"] == final_run.run_id
