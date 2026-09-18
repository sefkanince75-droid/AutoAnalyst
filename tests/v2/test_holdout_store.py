from __future__ import annotations

from uuid import uuid4

import pytest

from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.errors import SchemaError
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.results import AnalysisResult, ResultOutcome
from autoanalyst.domain.runs import RunStatus
from autoanalyst.execution.coordinator import ExecutionCoordinator
from autoanalyst.storage.binary import BinaryStore, holdout_selection_hash
from autoanalyst.storage.runs import RunStore


def _profiling_spec(workspace) -> AnalysisSpec:
    version_id = workspace.imported.version.version_id
    payload = {"module": "profiling", "operation": "profile", "version": version_id}
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=workspace.project.project_id,
        module_id=AnalysisModuleId.PROFILING,
        module_version="2.0",
        operation="profile",
        input_version_id=version_id,
        column_roles=(),
        parameters={},
        seed=42,
        resource_budget=ResourceBudget(1_000_000_000, 1_000_000_000, 30, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _new_run(workspace, coordinator: ExecutionCoordinator, key: str):
    return coordinator.create_run(_profiling_spec(workspace), request_key=key)


def _selection_provenance() -> dict[str, object]:
    return {
        "spec_hash": "1" * 64,
        "input_version_id": str(uuid4()),
        "target_column_id": str(uuid4()),
        "positive_label": {"type": "int", "value": 1},
        "negative_label": {"type": "int", "value": 0},
        "feature_column_ids": [str(uuid4()), str(uuid4())],
        "feature_semantic_types": {"x": "numeric", "cat": "categorical"},
        "feature_physical_families": {"x": "numeric", "cat": "categorical"},
        "split_policy": {"kind": "stratified", "seed": 42},
        "recommended_model": {"model_id": "logistic_regression", "threshold": 0.37},
        "model_artifact": {"sha256": "a" * 64},
        "split_artifact": {"sha256": "b" * 64},
    }


def test_holdout_selection_hash_freezes_recommendation_and_artifacts() -> None:
    training_run_id = str(uuid4())
    provenance = _selection_provenance()

    first = holdout_selection_hash(training_run_id, provenance)
    assert first == holdout_selection_hash(training_run_id, provenance)

    changed = dict(provenance)
    changed["recommended_model"] = {
        "model_id": "logistic_regression",
        "threshold": 0.38,
    }
    assert holdout_selection_hash(training_run_id, changed) != first


@pytest.mark.parametrize(
    ("provenance", "reason"),
    (
        ({}, "holdout_selection_metadata_missing"),
        (
            {
                "recommended_model": {"model_id": "lr"},
                "model_artifact": {"sha256": "a" * 64},
                "split_artifact": {"sha256": "b" * 64},
            },
            "holdout_recommendation_incomplete",
        ),
        (
            {
                "recommended_model": {"model_id": "lr", "threshold": 0.5},
                "model_artifact": {},
                "split_artifact": {"sha256": "b" * 64},
            },
            "holdout_artifact_checksum_missing",
        ),
        (
            {
                "recommended_model": {"model_id": "lr", "threshold": 0.5},
                "model_artifact": {"sha256": "a" * 64},
                "split_artifact": {},
            },
            "holdout_artifact_checksum_missing",
        ),
    ),
)
def test_holdout_selection_hash_rejects_incomplete_freeze_metadata(provenance, reason) -> None:
    with pytest.raises(SchemaError) as exc_info:
        holdout_selection_hash(str(uuid4()), provenance)
    assert exc_info.value.context["reason"] == reason


def test_holdout_lock_is_idempotent_but_selection_is_immutable(phase3_workspace) -> None:
    workspace = phase3_workspace
    runs = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(runs)
    binary = BinaryStore(workspace.catalog)
    training = _new_run(workspace, coordinator, "holdout-training-idempotent")
    final = _new_run(workspace, coordinator, "holdout-final-idempotent")
    selection_hash = "c" * 64

    created = binary.start_holdout_access(training.run_id, final.run_id, selection_hash)
    repeated = binary.start_holdout_access(training.run_id, final.run_id, selection_hash)
    assert repeated["lock_id"] == created["lock_id"]
    assert repeated["final_run_id"] == final.run_id
    assert binary.get_holdout_lock(training.run_id)["status"] == "access_started"

    with pytest.raises(SchemaError) as changed:
        binary.start_holdout_access(training.run_id, final.run_id, "d" * 64)
    assert changed.value.context["reason"] == "holdout_selection_changed"

    binary.mark_reported(training.run_id, final.run_id)
    assert binary.get_holdout_lock(training.run_id)["status"] == "reported"
    binary.mark_reported(training.run_id, final.run_id)

    with pytest.raises(SchemaError) as reported:
        binary.start_holdout_access(training.run_id, final.run_id, selection_hash)
    assert reported.value.context["reason"] == "holdout_already_reported"


def test_holdout_rejects_second_active_final_run(phase3_workspace) -> None:
    workspace = phase3_workspace
    runs = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(runs)
    binary = BinaryStore(workspace.catalog)
    training = _new_run(workspace, coordinator, "holdout-training-active")
    first_final = _new_run(workspace, coordinator, "holdout-final-active-1")
    second_final = _new_run(workspace, coordinator, "holdout-final-active-2")

    binary.start_holdout_access(training.run_id, first_final.run_id, "e" * 64)

    with pytest.raises(SchemaError) as exc_info:
        binary.start_holdout_access(training.run_id, second_final.run_id, "e" * 64)
    assert exc_info.value.context["reason"] == "holdout_final_already_active"
    assert exc_info.value.context["final_run_id"] == first_final.run_id


@pytest.mark.parametrize("terminal_status", (RunStatus.FAILED, RunStatus.CANCELLED))
def test_holdout_retry_reuses_exact_selection_after_retryable_terminal_state(
    phase3_workspace,
    terminal_status,
) -> None:
    workspace = phase3_workspace
    runs = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(runs)
    binary = BinaryStore(workspace.catalog)
    training = _new_run(workspace, coordinator, f"holdout-training-retry-{terminal_status.value}")
    first_final = _new_run(workspace, coordinator, f"holdout-final-retry-1-{terminal_status.value}")
    second_final = _new_run(
        workspace, coordinator, f"holdout-final-retry-2-{terminal_status.value}"
    )
    selection_hash = "f" * 64

    binary.start_holdout_access(training.run_id, first_final.run_id, selection_hash)
    if terminal_status is RunStatus.FAILED:
        runs.transition(first_final.run_id, status=terminal_status, error_code="resource_error")
    else:
        runs.transition(first_final.run_id, status=terminal_status)

    retried = binary.start_holdout_access(training.run_id, second_final.run_id, selection_hash)
    assert retried["final_run_id"] == second_final.run_id
    assert retried["selection_hash"] == selection_hash
    assert retried["status"] == "access_started"


def test_holdout_rejects_retry_after_completed_final_result(phase3_workspace) -> None:
    workspace = phase3_workspace
    runs = RunStore(workspace.catalog)
    coordinator = ExecutionCoordinator(runs)
    binary = BinaryStore(workspace.catalog)
    training = _new_run(workspace, coordinator, "holdout-training-completed")
    first_final = _new_run(workspace, coordinator, "holdout-final-completed-1")
    second_final = _new_run(workspace, coordinator, "holdout-final-completed-2")
    selection_hash = "0" * 64

    binary.start_holdout_access(training.run_id, first_final.run_id, selection_hash)
    runs.transition(first_final.run_id, status=RunStatus.RUNNING)
    runs.publish_result(
        first_final.run_id,
        AnalysisResult(
            result_id=str(uuid4()),
            run_id=first_final.run_id,
            module_id=AnalysisModuleId.PROFILING,
            outcome=ResultOutcome.SUCCEEDED,
        ),
    )

    with pytest.raises(SchemaError) as exc_info:
        binary.start_holdout_access(training.run_id, second_final.run_id, selection_hash)
    assert exc_info.value.context["reason"] == "holdout_final_result_exists"


def test_mark_reported_rejects_missing_mismatched_and_conflicting_lock(phase3_workspace) -> None:
    workspace = phase3_workspace
    coordinator = ExecutionCoordinator(RunStore(workspace.catalog))
    binary = BinaryStore(workspace.catalog)
    training = _new_run(workspace, coordinator, "holdout-training-mark")
    final = _new_run(workspace, coordinator, "holdout-final-mark")
    other_final = _new_run(workspace, coordinator, "holdout-final-mark-other")

    assert binary.get_holdout_lock(training.run_id) is None
    with pytest.raises(SchemaError) as missing:
        binary.mark_reported(training.run_id, final.run_id)
    assert missing.value.context["reason"] == "holdout_lock_missing"

    binary.start_holdout_access(training.run_id, final.run_id, "1" * 64)
    with pytest.raises(SchemaError) as mismatch:
        binary.mark_reported(training.run_id, other_final.run_id)
    assert mismatch.value.context["reason"] == "holdout_final_run_mismatch"

    with workspace.catalog.transaction() as connection:
        connection.execute(
            "UPDATE holdout_locks SET status = 'unexpected' WHERE training_run_id = ?",
            (training.run_id,),
        )
    with pytest.raises(SchemaError) as conflict:
        binary.mark_reported(training.run_id, final.run_id)
    assert conflict.value.context["reason"] == "holdout_status_conflict"
