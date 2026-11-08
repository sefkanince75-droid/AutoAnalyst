from threading import Event
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from autoanalyst.application.examples import create_example
from autoanalyst.bootstrap import build_runtime
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.datasets import ColumnRole, ColumnUsage, SemanticType
from autoanalyst.domain.errors import MethodNotApplicableError, SchemaError
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.domain.runs import RunStatus


@pytest.mark.parametrize("policy", ["group", "time"])
def test_cv_folds_preserve_groups_and_timestamp_ties(policy):
    from types import SimpleNamespace

    from autoanalyst.analyses.binary.audit import fold_indices

    work = pd.DataFrame({"role": np.repeat(np.arange(30), 4)})
    y = pd.Series(np.tile([0, 1, 0, 1], 30))
    if policy == "time":
        work["role"] = pd.to_datetime(work.role, unit="D", origin="2020-01-01")
    original = SimpleNamespace(
        parameters={"split_policy": policy},
        column_roles=(
            SimpleNamespace(
                column_id="role",
                usages=(ColumnUsage.GROUP if policy == "group" else ColumnUsage.TIME,),
            ),
        ),
    )
    folds = fold_indices(original, work, y, 3, 42)
    assert len(folds) == 3
    for train, valid in folds:
        assert not set(train) & set(valid)
        assert not set(work.iloc[train].role) & set(work.iloc[valid].role)
        if policy == "time":
            assert work.iloc[train].role.max() < work.iloc[valid].role.min()


@pytest.mark.parametrize("classification", [False, True])
@pytest.mark.parametrize("policy", ["group", "time"])
def test_new_task_splits_keep_roles_separate(classification, policy):
    from autoanalyst.analyses.supervised import partitions

    frame = pd.DataFrame(
        {
            "group": np.repeat(np.arange(30), 6),
            "time": np.repeat(pd.date_range("2020-01-01", periods=30), 6),
        }
    )
    y = pd.Series(np.tile([0, 1, 2], 60))
    split = partitions(
        frame, y, policy, 42, group_id="group", time_id="time", classification=classification
    )
    a, b, c = (getattr(split, part) for part in ("train", "validation", "test"))
    role = "group" if policy == "group" else "time"
    assert not set(frame.iloc[a][role]) & set(frame.iloc[b][role])
    assert not set(frame.iloc[a][role]) & set(frame.iloc[c][role])
    assert not set(frame.iloc[b][role]) & set(frame.iloc[c][role])


def make_spec(runtime, project, version, module, operation, parameters, roles=()):
    return AnalysisSpec(
        str(uuid4()),
        project.project_id,
        module,
        "2.0",
        operation,
        version.version_id,
        roles,
        parameters,
        42,
        ResourceBudget(2_000_000_000, 2_000_000_000, 120),
        fingerprint(
            {
                "module": module,
                "operation": operation,
                "version": version.version_id,
                "parameters": parameters,
                "roles": roles,
            }
        ),
        utc_now(),
    )


def execute(runtime, spec):
    run = runtime.coordinator.create_run(spec, request_key=str(uuid4()))
    completed = runtime.coordinator.execute_inline(
        run.run_id, runtime.registry.get(spec.module_id), cancellation_event=Event()
    )
    return completed, runtime.run_store.get_result(completed.result_id)


def test_small_mean_interval_excludes_nonfinite_and_warns(phase3_workspace):
    ws = phase3_workspace
    runtime = build_runtime(ws.catalog.paths.root)
    _, result = execute(
        runtime,
        make_spec(
            runtime,
            ws.project,
            ws.imported.version,
            AnalysisModuleId.COMPARISON,
            "mean_interval",
            {"column_id": ws.column("num")["column_id"], "independence_confirmed": True},
        ),
    )
    assert result.metrics[0].sample_size == 3
    assert any(f.code == "comparison.small_interval_sample" for f in result.findings)


@pytest.mark.parametrize(
    "kind,module",
    [
        ("regression", AnalysisModuleId.REGRESSION),
        ("multiclass", AnalysisModuleId.MULTICLASS_CLASSIFICATION),
    ],
)
def test_supervised_train_locked_final_score_and_reopen(tmp_path, kind, module):
    runtime = build_runtime(tmp_path)
    project = create_example(runtime, kind, language="en")
    from autoanalyst.application import DatasetService

    dataset = DatasetService(runtime.catalog).list(project.project_id)[0]
    version = DatasetService(runtime.catalog).current_version(dataset.dataset_id)
    columns = {
        c["display_name"]: c
        for c in runtime.catalog.list_columns(version.version_id)
        if not c["is_system"]
    }
    roles = (
        ColumnRole(
            columns["value"]["column_id"], SemanticType.NUMERIC, (ColumnUsage.FEATURE,), True, True
        ),
        ColumnRole(
            columns["category"]["column_id"],
            SemanticType.CATEGORICAL,
            (ColumnUsage.FEATURE,),
            True,
            True,
        ),
        ColumnRole(
            columns["target"]["column_id"],
            SemanticType.NUMERIC if kind == "regression" else SemanticType.CATEGORICAL,
            (ColumnUsage.TARGET,),
            True,
        ),
    )
    training, saved = execute(
        runtime, make_spec(runtime, project, version, module, "train_validate", {}, roles)
    )
    assert (
        saved.provenance["recommended_model"]
        and saved.methodology["final_holdout_evaluated"] is False
    )
    metrics = {m.name for m in saved.metrics}
    assert (
        {"mae", "rmse", "r_squared"} if kind == "regression" else {"accuracy", "macro_f1"}
    ) <= metrics
    mapping = {
        columns[name]["column_id"]: columns[name]["column_id"] for name in ("value", "category")
    }
    with pytest.raises(MethodNotApplicableError):
        execute(
            runtime,
            make_spec(
                runtime,
                project,
                version,
                module,
                "score_new_data",
                {"training_run_id": training.run_id, "feature_mapping": mapping},
            ),
        )
    final, result = execute(
        runtime,
        make_spec(
            runtime,
            project,
            version,
            module,
            "final_evaluate",
            {"training_run_id": training.run_id},
        ),
    )
    assert final.status is RunStatus.COMPLETED and all(
        m.dimensions["partition"] == "final_test" for m in result.metrics
    )
    from autoanalyst.storage.binary import BinaryStore

    assert BinaryStore(runtime.catalog).get_holdout_lock(training.run_id)["status"] == "reported"
    _, scored = execute(
        build_runtime(tmp_path),
        make_spec(
            runtime,
            project,
            version,
            module,
            "score_new_data",
            {"training_run_id": training.run_id, "feature_mapping": mapping},
        ),
    )
    assert len(scored.tables[0].rows) == 400
    assert runtime.run_store.get_result(saved.result_id) == saved
    with pytest.raises(SchemaError):
        execute(
            runtime,
            make_spec(
                runtime,
                project,
                version,
                module,
                "final_evaluate",
                {"training_run_id": training.run_id},
            ),
        )


@pytest.mark.parametrize("operation", ["mean_interval", "proportion_interval"])
def test_intervals_match_reference_and_require_independence(tmp_path, operation):
    runtime = build_runtime(tmp_path)
    project = create_example(runtime, "binary", language="en")
    from autoanalyst.application import DatasetService
    from autoanalyst.data import TableAccess

    dataset = DatasetService(runtime.catalog).list(project.project_id)[0]
    version = DatasetService(runtime.catalog).current_version(dataset.dataset_id)
    name = "value" if operation == "mean_interval" else "target"
    column = next(
        c for c in runtime.catalog.list_columns(version.version_id) if c["display_name"] == name
    )
    params = {"column_id": column["column_id"], "confidence": 0.95, "independence_confirmed": True}
    if operation == "proportion_interval":
        params["success_label"] = 1
    _, result = execute(
        runtime,
        make_spec(runtime, project, version, AnalysisModuleId.COMPARISON, operation, params),
    )
    values = (
        TableAccess(runtime.catalog, runtime.artifact_store)
        .selected_columns(version.version_id, (column["column_id"],))
        .to_pandas()
        .iloc[:, 0]
    )
    expected = (
        stats.t.interval(0.95, len(values) - 1, loc=values.mean(), scale=stats.sem(values))
        if operation == "mean_interval"
        else stats.binomtest(int((values == 1).sum()), len(values)).proportion_ci(
            confidence_level=0.95, method="exact"
        )
    )
    np.testing.assert_allclose(result.metrics[0].interval, expected)
    invalid = make_spec(
        runtime,
        project,
        version,
        AnalysisModuleId.COMPARISON,
        operation,
        {**params, "independence_confirmed": False},
    )
    assert runtime.registry.get(AnalysisModuleId.COMPARISON).validate(invalid)
