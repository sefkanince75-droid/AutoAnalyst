from __future__ import annotations

from uuid import uuid4

import pytest

from autoanalyst.analyses.binary.module import BinaryClassificationModule
from autoanalyst.application import DatasetService, ProjectService
from autoanalyst.data import CSVIngestor
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.datasets import ColumnRole, ColumnUsage, SemanticType
from autoanalyst.domain.errors import MethodNotApplicableError, SchemaError
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.results import AnalysisResult, ResultOutcome
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage import ArtifactStore, SQLiteCatalog
from autoanalyst.storage.binary import BinaryStore, holdout_selection_hash
from autoanalyst.storage.runs import RunStore


class _Event:
    def is_set(self):
        return False


def test_snapshot_restore_preserves_model_holdout_report_and_resumes_scoring(tmp_path):
    from autoanalyst.application.snapshots import WorkspaceSnapshotService
    from autoanalyst.reporting import ReportService

    catalog, store, project, imported, columns = _workspace(tmp_path / "original")
    runs, coordinator, module, _, training, result = _train(
        catalog, store, project, imported, columns
    )
    spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training.run_id},
    )
    final = coordinator.create_run(
        spec, request_key="snapshot-final", parent_run_id=training.run_id
    )
    final = coordinator.execute_inline(final.run_id, module, cancellation_event=_Event())
    lock = BinaryStore(catalog).get_holdout_lock(training.run_id)
    reports = ReportService(catalog, store)
    report = reports.render(final.result_id, format="html", language="tr")
    report_bytes = reports.artifact_bytes(report)
    ProjectService(catalog).set_archived(project.project_id, True, expected_revision=0)
    snapshot = WorkspaceSnapshotService(catalog, store).export_snapshot(
        tmp_path / "model.aasnapshot"
    )
    summary = WorkspaceSnapshotService.inspect_snapshot(snapshot)
    assert summary.models == 1 and summary.results >= 2
    target = WorkspaceSnapshotService.import_snapshot(snapshot, tmp_path / "restored", trusted=True)
    restored = SQLiteCatalog(target)
    restored_store = ArtifactStore(target)
    assert BinaryStore(restored).get_holdout_lock(training.run_id) == lock
    assert RunStore(restored).get_result(result.result_id) == result
    assert restored.get_project(project.project_id).archived_at is not None
    assert ReportService(restored, restored_store).artifact_bytes(report) == report_bytes
    scoring = _spec(
        project,
        imported,
        columns,
        operation="score_new_data",
        parameters={
            "training_run_id": training.run_id,
            "feature_mapping": _identity_mapping(columns),
        },
    )
    restored_coordinator = ExecutionCoordinator(RunStore(restored))
    run = restored_coordinator.create_run(
        scoring, request_key="restored-scoring", parent_run_id=training.run_id
    )
    completed = restored_coordinator.execute_inline(
        run.run_id,
        BinaryClassificationModule(restored, restored_store),
        cancellation_event=_Event(),
    )
    assert completed.status is RunStatus.COMPLETED


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
    columns = {
        row["display_name"]: row
        for row in catalog.list_columns(imported.version.version_id)
        if not row["is_system"]
    }
    return catalog, store, project, imported, columns


def _spec(project, imported, columns, *, operation="train_validate", parameters=None):
    roles = (
        ColumnRole(
            str(columns["x"]["column_id"]), SemanticType.NUMERIC, (ColumnUsage.FEATURE,), True, True
        ),
        ColumnRole(
            str(columns["cat"]["column_id"]),
            SemanticType.CATEGORICAL,
            (ColumnUsage.FEATURE,),
            True,
            True,
        ),
        ColumnRole(
            str(columns["target"]["column_id"]), SemanticType.NUMERIC, (ColumnUsage.TARGET,), True
        ),
    )
    params = parameters or {
        "positive_label": 1,
        "minimum_recall": 0.70,
        "split_policy": "stratified",
        "class_weight_policy": "none",
    }
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


def _train(catalog, store, project, imported, columns):
    run_store = RunStore(catalog)
    coordinator = ExecutionCoordinator(run_store)
    module = BinaryClassificationModule(catalog, store)
    training_spec = _spec(project, imported, columns)
    training_run = coordinator.create_run(training_spec, request_key=f"train-{uuid4()}")
    completed = coordinator.execute_inline(training_run.run_id, module, cancellation_event=_Event())
    assert completed.status is RunStatus.COMPLETED
    result = run_store.result_for_run(training_run.run_id)
    assert result is not None
    assert result.provenance.get("recommended_model") is not None
    return run_store, coordinator, module, training_spec, training_run, result


def _identity_mapping(columns):
    return {
        str(columns["x"]["column_id"]): str(columns["x"]["column_id"]),
        str(columns["cat"]["column_id"]): str(columns["cat"]["column_id"]),
    }


def test_binary_train_and_persistent_final_holdout(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    run_store, coordinator, module, training_spec, training_run, result = _train(
        catalog, store, project, imported, columns
    )

    assert result.provenance["positive_label"]["value"] == 1
    assert result.provenance["split_artifact"]["sha256"]
    assert set(result.provenance["feature_physical_families"].values()) == {
        "numeric",
        "categorical",
    }
    metric_names = {metric.name for metric in result.metrics}
    assert {"precision", "recall", "average_precision", "tn", "fp", "fn", "tp"} <= metric_names

    final_spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training_run.run_id},
    )
    final_run = coordinator.create_run(
        final_spec, request_key="final", parent_run_id=training_run.run_id
    )
    final_completed = coordinator.execute_inline(
        final_run.run_id, module, cancellation_event=_Event()
    )
    assert final_completed.status is RunStatus.COMPLETED
    lock = BinaryStore(catalog).get_holdout_lock(training_run.run_id)
    assert lock is not None
    assert lock["final_run_id"] == final_run.run_id
    assert lock["status"] == "reported"
    assert lock["selection_hash"] == holdout_selection_hash(training_run.run_id, result.provenance)

    final_result = run_store.result_for_run(final_run.run_id)
    assert final_result is not None
    final_metric_names = {metric.name for metric in final_result.metrics}
    assert {
        "precision",
        "recall",
        "average_precision",
        "tn",
        "fp",
        "fn",
        "tp",
    } <= final_metric_names


def test_scoring_requires_finalized_model_and_complete_mapping(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    run_store, coordinator, module, training_spec, training_run, _ = _train(
        catalog, store, project, imported, columns
    )
    mapping = _identity_mapping(columns)

    prefinal_spec = _spec(
        project,
        imported,
        columns,
        operation="score_new_data",
        parameters={"training_run_id": training_run.run_id, "feature_mapping": mapping},
    )
    prefinal_run = coordinator.create_run(prefinal_spec, request_key="score-before-final")
    with pytest.raises(MethodNotApplicableError) as exc_info:
        coordinator.execute_inline(prefinal_run.run_id, module, cancellation_event=_Event())
    assert exc_info.value.context["reason"] == "binary.model_not_finalized"

    final_spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training_run.run_id},
    )
    final_run = coordinator.create_run(
        final_spec, request_key="final-for-score", parent_run_id=training_run.run_id
    )
    coordinator.execute_inline(final_run.run_id, module, cancellation_event=_Event())

    incomplete_spec = _spec(
        project,
        imported,
        columns,
        operation="score_new_data",
        parameters={
            "training_run_id": training_run.run_id,
            "feature_mapping": {str(columns["x"]["column_id"]): str(columns["x"]["column_id"])},
        },
    )
    incomplete_run = coordinator.create_run(incomplete_spec, request_key="score-incomplete")
    with pytest.raises(SchemaError) as exc_info:
        coordinator.execute_inline(incomplete_run.run_id, module, cancellation_event=_Event())
    assert exc_info.value.context["reason"] == "binary.scoring_feature_mapping_incomplete"

    scoring_spec = _spec(
        project,
        imported,
        columns,
        operation="score_new_data",
        parameters={"training_run_id": training_run.run_id, "feature_mapping": mapping},
    )
    scoring_run = coordinator.create_run(scoring_spec, request_key="score-finalized")
    completed = coordinator.execute_inline(scoring_run.run_id, module, cancellation_event=_Event())
    assert completed.status is RunStatus.COMPLETED
    scoring_result = run_store.result_for_run(scoring_run.run_id)
    assert scoring_result is not None
    assert scoring_result.tables[0].columns == ("row_id", "score_positive", "predicted_label")
    assert len(scoring_result.tables[0].rows) == imported.version.row_count
    assert scoring_result.provenance["final_run_id"] == final_run.run_id


def test_scoring_rejects_incompatible_physical_type(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    _, coordinator, module, _, training_run, _ = _train(catalog, store, project, imported, columns)

    final_spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training_run.run_id},
    )
    final_run = coordinator.create_run(final_spec, request_key="final-schema")
    coordinator.execute_inline(final_run.run_id, module, cancellation_event=_Event())

    scoring_dataset = DatasetService(catalog).create(project.project_id, "Scoring incompatible")
    scoring_import = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=scoring_dataset.dataset_id,
        source=b"x,cat\nnot-a-number,a\nstill-text,b\n",
        original_name="scoring.csv",
    )
    scoring_columns = {
        row["display_name"]: row
        for row in catalog.list_columns(scoring_import.version.version_id)
        if not row["is_system"]
    }
    mapping = {
        str(columns["x"]["column_id"]): str(scoring_columns["x"]["column_id"]),
        str(columns["cat"]["column_id"]): str(scoring_columns["cat"]["column_id"]),
    }
    scoring_spec = AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project.project_id,
        module_id=AnalysisModuleId.BINARY_CLASSIFICATION,
        module_version="2.0",
        operation="score_new_data",
        input_version_id=scoring_import.version.version_id,
        column_roles=(),
        parameters={"training_run_id": training_run.run_id, "feature_mapping": mapping},
        seed=42,
        resource_budget=ResourceBudget(2_000_000_000, 2_000_000_000, 120, 1),
        spec_hash=fingerprint(
            {
                "operation": "score_new_data",
                "version": scoring_import.version.version_id,
                "parameters": {"training_run_id": training_run.run_id, "feature_mapping": mapping},
            }
        ),
        created_at=utc_now(),
    )
    scoring_run = coordinator.create_run(scoring_spec, request_key="score-bad-schema")
    with pytest.raises(MethodNotApplicableError) as exc_info:
        coordinator.execute_inline(scoring_run.run_id, module, cancellation_event=_Event())
    assert exc_info.value.context["reason"] == "binary.scoring_schema_mismatch"


def test_result_and_holdout_reported_state_publish_atomically(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    run_store, coordinator, _, _, training_run, training_result = _train(
        catalog, store, project, imported, columns
    )
    final_spec = _spec(
        project,
        imported,
        columns,
        operation="final_evaluate",
        parameters={"training_run_id": training_run.run_id},
    )
    final_run = coordinator.create_run(final_spec, request_key="atomic-final")
    run_store.transition(final_run.run_id, status=RunStatus.RUNNING)
    BinaryStore(catalog).start_holdout_access(
        training_run.run_id,
        final_run.run_id,
        holdout_selection_hash(training_run.run_id, training_result.provenance),
    )

    # Fault injection after publish_result has observed a RUNNING payload: the SQL row
    # no longer matches the guarded RUNNING -> COMPLETED update. The inserted result and
    # holdout status update must both roll back.
    with catalog.transaction() as connection:
        connection.execute(
            "UPDATE analysis_runs SET status = 'failed' WHERE run_id = ?", (final_run.run_id,)
        )
    synthetic = AnalysisResult(
        result_id=str(uuid4()),
        run_id=final_run.run_id,
        module_id=AnalysisModuleId.BINARY_CLASSIFICATION,
        outcome=ResultOutcome.SUCCEEDED,
    )
    with pytest.raises(SchemaError):
        run_store.publish_result(
            final_run.run_id,
            synthetic,
            holdout_training_run_id=training_run.run_id,
        )
    with catalog.connection() as connection:
        result_row = connection.execute(
            "SELECT 1 FROM analysis_results WHERE result_id = ?", (synthetic.result_id,)
        ).fetchone()
        lock_row = connection.execute(
            "SELECT status FROM holdout_locks WHERE training_run_id = ?", (training_run.run_id,)
        ).fetchone()
    assert result_row is None
    assert lock_row["status"] == "access_started"


def test_audit_folds_use_only_training_and_preserve_original_model(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    runs, coordinator, module, _, training, saved = _train(
        catalog, store, project, imported, columns
    )
    split = module._load_split(saved.provenance["split_artifact"])
    allowed = set(split.loc[split.partition == "train", "row_id"])
    protected = set(split.loc[split.partition != "train", "row_id"])
    model_before = store.resolve_relative_path(
        catalog.get_artifact(saved.provenance["model_artifact"]["artifact_id"]).relative_path
    ).read_bytes()
    spec = _spec(
        project,
        imported,
        columns,
        operation="audit_model",
        parameters={"training_run_id": training.run_id, "folds": 3},
    )
    run = coordinator.create_run(spec, request_key="audit")
    completed = coordinator.execute_inline(run.run_id, module, cancellation_event=_Event())
    result = runs.get_result(completed.result_id)
    membership = next(t for t in result.tables if t.columns == ("fold", "row_id", "partition"))
    observed = {r[1] for r in membership.rows}
    assert observed == allowed and not observed.intersection(protected)
    for fold in range(1, 4):
        a = {r[1] for r in membership.rows if r[0] == fold and r[2] == "train"}
        b = {r[1] for r in membership.rows if r[0] == fold and r[2] == "validation"}
        assert not a.intersection(b) and a | b == allowed
    assert result.provenance["model_card"]["environment"]
    assert module.binary_store.get_holdout_lock(training.run_id) is None
    assert (
        store.resolve_relative_path(
            catalog.get_artifact(saved.provenance["model_artifact"]["artifact_id"]).relative_path
        ).read_bytes()
        == model_before
    )


def test_calibration_uses_validation_and_does_not_replace_selection(tmp_path):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    runs, coordinator, module, _, training, saved = _train(
        catalog, store, project, imported, columns
    )
    spec = _spec(
        project,
        imported,
        columns,
        operation="calibration_report",
        parameters={"training_run_id": training.run_id},
    )
    run = coordinator.create_run(spec, request_key="calibration")
    completed = coordinator.execute_inline(run.run_id, module, cancellation_event=_Event())
    result = runs.get_result(completed.result_id)
    assert len(result.metrics) == 4 and all(
        m.dimensions["partition"] == "validation" for m in result.metrics
    )
    assert all(0 <= m.value <= 1 for m in result.metrics if m.name == "brier_score")
    assert result.methodology["final_holdout_used"] is False
    assert runs.result_for_run(training.run_id) == saved
    assert module.binary_store.get_holdout_lock(training.run_id) is None


def test_drift_compares_training_reference_without_loading_model(tmp_path, monkeypatch):
    catalog, store, project, imported, columns = _workspace(tmp_path)
    runs, coordinator, module, _, training, _ = _train(catalog, store, project, imported, columns)
    newer = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=imported.version.dataset_id,
        source=("x,cat,target\n" + "\n".join("10000,NEW,0" for _ in range(40)) + "\n").encode(),
    )
    newcols = {c.display_name: c for c in newer.columns if not c.is_system}
    mapping = {columns[name]["column_id"]: newcols[name].column_id for name in ("x", "cat")}
    monkeypatch.setattr(module, "_load_model", lambda *a: pytest.fail("drift loaded model"))
    spec = _spec(
        project,
        newer,
        columns,
        operation="drift_report",
        parameters={"training_run_id": training.run_id, "feature_mapping": mapping},
    )
    run = coordinator.create_run(spec, request_key="drift")
    completed = coordinator.execute_inline(run.run_id, module, cancellation_event=_Event())
    result = runs.get_result(completed.result_id)
    assert all(row[-1] for row in result.tables[0].rows)
    assert result.methodology["reference_partition"] == "train_only"
