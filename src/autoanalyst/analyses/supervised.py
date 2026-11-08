"""Closed regression/multiclass tasks with persisted, locked final evaluation."""

from __future__ import annotations

import math
import os
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
)
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline

from ..domain.codec import encode_typed_label
from ..domain.datasets import SemanticType
from ..domain.errors import MethodNotApplicableError, SchemaError
from ..domain.plans import AnalysisModuleId
from ..domain.results import Metric, MetricValueState, ResultOutcome, ResultTable
from ..storage.binary import holdout_selection_hash
from .binary.module import (
    BinaryClassificationModule,
    _artifact_payload,
    _physical_family,
    _prepare_features,
    _roles,
    _typed_distinct,
    _typed_key,
)
from .binary.preprocess import build_preprocessor
from .binary.split import Partitions
from .contract import (
    AnalysisDescription,
    ApplicabilityReport,
    ResourceEstimate,
    ResultDraft,
    ValidationIssue,
)


def partitions(frame, y, policy, seed, group_id=None, time_id=None, *, classification=False):
    indices = np.arange(len(frame))
    if policy in {"random", "stratified"}:
        a, temp = train_test_split(
            indices, test_size=0.3, random_state=seed, stratify=y if classification else None
        )
        b, c = train_test_split(
            temp,
            test_size=0.5,
            random_state=seed,
            stratify=y.iloc[temp] if classification else None,
        )
    elif policy == "group" and group_id:
        if frame[group_id].isna().any():
            raise MethodNotApplicableError({"reason": "group_split_null_group"})
        a, temp = next(
            GroupShuffleSplit(n_splits=1, test_size=0.3, random_state=seed).split(
                frame, groups=frame[group_id]
            )
        )
        b_rel, c_rel = next(
            GroupShuffleSplit(n_splits=1, test_size=0.5, random_state=seed).split(
                frame.iloc[temp], groups=frame.iloc[temp][group_id]
            )
        )
        b, c = temp[b_rel], temp[c_rel]
    elif policy == "time" and time_id:
        parsed = pd.to_datetime(frame[time_id], utc=True, errors="coerce")
        if parsed.isna().any():
            raise MethodNotApplicableError({"reason": "time_split_invalid_or_null_time"})
        ordered = np.argsort(parsed.to_numpy(), kind="stable")
        sorted_time = parsed.iloc[ordered].reset_index(drop=True)
        from .binary.split import _advance_equal_timestamp

        left = _advance_equal_timestamp(sorted_time, int(len(frame) * 0.7))
        right = _advance_equal_timestamp(sorted_time, max(left + 1, int(len(frame) * 0.85)))
        a, b, c = ordered[:left], ordered[left:right], ordered[right:]
    else:
        raise SchemaError({"reason": "supervised.split_policy"})
    if min(len(a), len(b), len(c)) < 5 or len(np.unique(np.concatenate((a, b, c)))) != len(frame):
        raise MethodNotApplicableError({"reason": "split_empty_partition"})
    if classification and any(
        y.iloc[index].nunique() != y.nunique() or y.iloc[index].value_counts().min() < 2
        for index in (a, b, c)
    ):
        raise MethodNotApplicableError({"reason": "supervised.class_partition"})
    return Partitions(np.sort(a), np.sort(b), np.sort(c), policy)


def models(numeric, categorical, seed, classification):
    estimators = (
        {
            "dummy": DummyClassifier(strategy="prior", random_state=seed),
            "logistic_regression": LogisticRegression(max_iter=1000, random_state=seed),
            "random_forest": RandomForestClassifier(
                n_estimators=100, max_depth=12, min_samples_leaf=2, n_jobs=1, random_state=seed
            ),
        }
        if classification
        else {
            "dummy": DummyRegressor(strategy="median"),
            "ridge": Ridge(alpha=1.0, solver="lsqr"),
            "random_forest": RandomForestRegressor(
                n_estimators=100, max_depth=12, min_samples_leaf=2, n_jobs=1, random_state=seed
            ),
        }
    )
    return {
        name: Pipeline(
            [
                (
                    "preprocess",
                    build_preprocessor(
                        numeric, categorical, scale_numeric=name in {"ridge", "logistic_regression"}
                    ),
                ),
                ("model", estimator),
            ]
        )
        for name, estimator in estimators.items()
    }


def measured(y, predicted, classification):
    if classification:
        return {
            "accuracy": float(accuracy_score(y, predicted)),
            "macro_f1": float(f1_score(y, predicted, average="macro", zero_division=0)),
            "weighted_f1": float(f1_score(y, predicted, average="weighted", zero_division=0)),
        }
    return {
        "mae": float(mean_absolute_error(y, predicted)),
        "rmse": float(np.sqrt(mean_squared_error(y, predicted))),
        "r_squared": float(r2_score(y, predicted, force_finite=False)),
    }


def metric(name, value, dimensions, n):
    if math.isfinite(value):
        return Metric(
            str(uuid4()), name, MetricValueState.FINITE, value, dimensions=dimensions, sample_size=n
        )
    return Metric(
        str(uuid4()),
        name,
        MetricValueState.NOT_DEFINED,
        dimensions=dimensions,
        sample_size=n,
        reason_code="constant_target_or_nonfinite",
    )


class SupervisedModule:
    MODULE_VERSION = "2.0"
    OPERATIONS = ("train_validate", "final_evaluate", "score_new_data")

    def __init__(self, catalog, artifact_store, *, classification=False):
        self.catalog, self.artifact_store = catalog, artifact_store
        self.classification = classification
        self.module_id = (
            AnalysisModuleId.MULTICLASS_CLASSIFICATION
            if classification
            else AnalysisModuleId.REGRESSION
        )
        self.helper = BinaryClassificationModule(catalog, artifact_store)

    def describe(self):
        return AnalysisDescription(self.module_id, self.MODULE_VERSION, self.OPERATIONS)

    def validate(self, spec):
        issues = []
        allowed = (
            {"split_policy"}
            if spec.operation == "train_validate"
            else {"training_run_id", "feature_mapping"}
            if spec.operation == "score_new_data"
            else {"training_run_id"}
        )
        if set(spec.parameters) - allowed:
            issues.append(ValidationIssue("supervised.invalid_spec"))
        if spec.module_id is not self.module_id or spec.operation not in self.OPERATIONS:
            issues.append(ValidationIssue("supervised.invalid_spec"))
        if spec.operation != "train_validate" and "training_run_id" not in spec.parameters:
            issues.append(ValidationIssue("binary.training_run_required"))
        return tuple(issues)

    def check_applicability(self, spec):
        issues = self.validate(spec)
        if (
            spec.operation == "train_validate"
            and self.catalog.get_version(spec.input_version_id).row_count < 60
        ):
            issues += (ValidationIssue("supervised.too_small"),)
        return ApplicabilityReport(blocking_issues=issues)

    def estimate_resources(self, spec):
        v = self.catalog.get_version(spec.input_version_id)
        cells = v.row_count * max(1, v.column_count)
        return ResourceEstimate(
            max(32_000_000, cells * 128), max(8_000_000, cells * 16), max(0.5, cells / 100_000), 1
        )

    def run(self, spec, context):
        if self.validate(spec):
            raise SchemaError({"reason": "supervised.invalid_spec"})
        context.raise_if_cancelled()
        if spec.operation == "train_validate":
            return self.train(spec, context)
        return self.evaluate_or_score(spec, context)

    def train(self, spec, context):
        target, features, group, time = _roles(spec)
        feature_ids = [r.column_id for r in features]
        if (
            not feature_ids
            or target.column_id in feature_ids
            or any(
                r.available_at_prediction is False
                or r.semantic_type
                not in {SemanticType.NUMERIC, SemanticType.CATEGORICAL, SemanticType.BOOLEAN}
                for r in features
            )
        ):
            raise MethodNotApplicableError({"reason": "supervised.feature_contract"})
        self.helper._assert_no_dataset_leakage(
            spec.input_version_id, {target.column_id, *feature_ids}
        )
        columns = {str(c["column_id"]): c for c in self.catalog.list_columns(spec.input_version_id)}
        row_id = next(
            k for k, c in columns.items() if c["physical_name"] == "__aa_internal_row_id__"
        )
        ids = tuple(
            dict.fromkeys(
                (
                    row_id,
                    target.column_id,
                    *feature_ids,
                    *([group.column_id] if group else []),
                    *([time.column_id] if time else []),
                )
            )
        )
        frame = self.helper._load_as_ids(spec.input_version_id, ids)
        if frame[target.column_id].isna().any():
            raise MethodNotApplicableError({"reason": "binary.target_contains_missing"})
        labels = _typed_distinct(frame[target.column_id]) if self.classification else []
        if self.classification:
            if not 3 <= len(labels) <= 20:
                raise MethodNotApplicableError({"reason": "supervised.multiclass_target"})
            keys = {_typed_key(v): i for i, v in enumerate(labels)}
            y = frame[target.column_id].map(lambda v: keys[_typed_key(v)])
        else:
            y = pd.to_numeric(frame[target.column_id], errors="coerce")
            if not np.isfinite(y).all() or y.nunique() < 2:
                raise MethodNotApplicableError({"reason": "supervised.regression_target"})
        numeric = [r.column_id for r in features if r.semantic_type is SemanticType.NUMERIC]
        categorical = [r.column_id for r in features if r.semantic_type is not SemanticType.NUMERIC]
        if len(numeric) + 100 * len(categorical) > 10_000:
            raise MethodNotApplicableError(
                {"reason": "binary.encoded_feature_limit_estimate_exceeded"}
            )
        X = _prepare_features(frame[feature_ids], numeric, categorical)
        if numeric and np.isinf(X[numeric].to_numpy(dtype=float)).any():
            raise MethodNotApplicableError({"reason": "binary.non_finite_numeric_feature"})
        policy = str(
            spec.parameters.get("split_policy", "stratified" if self.classification else "random")
        )
        try:
            split = partitions(
                frame,
                y,
                policy,
                spec.seed,
                group.column_id if group else None,
                time.column_id if time else None,
                classification=self.classification,
            )
        except ValueError as exc:
            raise MethodNotApplicableError({"reason": "supervised.class_partition"}) from exc
        fitted, values, rows, metrics = {}, {}, [], []
        for i, (name, model) in enumerate(
            models(numeric, categorical, spec.seed, self.classification).items()
        ):
            context.emit_progress("supervised.fit", i / 3, {"model_id": name})
            model.fit(X.iloc[split.train], y.iloc[split.train])
            prediction = model.predict(X.iloc[split.validation])
            scores = measured(y.iloc[split.validation], prediction, self.classification)
            fitted[name], values[name] = model, scores
            for key, value in scores.items():
                metrics.append(
                    metric(
                        key,
                        value,
                        {"model_id": name, "partition": "validation"},
                        len(split.validation),
                    )
                )
                rows.append((name, key, value if math.isfinite(value) else None))
        score_name = "macro_f1" if self.classification else "rmse"
        ranked = sorted(
            (n for n in fitted if n != "dummy"),
            key=lambda n: values[n][score_name],
            reverse=self.classification,
        )
        best = ranked[0]
        improves = (
            values[best][score_name] > values["dummy"][score_name] + 1e-12
            if self.classification
            else values[best][score_name] < values["dummy"][score_name] - 1e-12
        )
        artifacts = []
        model_artifact = None
        if improves:
            staged = self.artifact_store.create_staging(
                project_id=spec.project_id,
                owner_run_id=context.run_id,
                kind=f"{self.module_id.value}_model",
                media_type="application/x-joblib",
                format_version="2.0",
            )
            try:
                joblib.dump(fitted[best], staged.staging_path)
                with staged.staging_path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                model_artifact = self.artifact_store.finalize(staged)
            finally:
                staged.staging_path.unlink(missing_ok=True)
            artifacts.append(model_artifact)
        split_artifact = self.helper._save_split(frame[row_id], split, spec, context)
        artifacts.append(split_artifact)
        p = {
            "input_version_id": spec.input_version_id,
            "spec_hash": spec.spec_hash,
            "model_task": self.module_id.value,
            "target_column_id": target.column_id,
            "feature_column_ids": feature_ids,
            "feature_semantic_types": {r.column_id: r.semantic_type.value for r in features},
            "feature_physical_families": {
                c: _physical_family(str(columns[c]["physical_type"])) for c in feature_ids
            },
            "class_labels": [encode_typed_label(v) for v in labels],
            "split_policy": policy,
            "split_artifact": _artifact_payload(split_artifact),
            "model_artifact": _artifact_payload(model_artifact),
            "recommended_model": {"model_id": best, "threshold": None} if improves else None,
            "model_card": {
                "task": self.module_id.value,
                "seed": spec.seed,
                "environment": context.environment,
                "feature_ids": feature_ids,
                "target_id": target.column_id,
                "target_name": columns[target.column_id]["display_name"],
                "feature_names": [columns[c]["display_name"] for c in feature_ids],
                "final_holdout": "locked",
                "limitations": (
                    "validation_selection_only",
                    "not_causal",
                    "no_probability_calibration_claim",
                ),
            },
        }
        tables = [ResultTable(str(uuid4()), ("model_id", "metric", "value"), tuple(rows))]
        if self.classification:
            predicted = fitted[best].predict(X.iloc[split.validation])
            precision, recall, fscore, support = precision_recall_fscore_support(
                y.iloc[split.validation],
                predicted,
                labels=list(range(len(labels))),
                zero_division=0,
            )
            tables.append(
                ResultTable(
                    str(uuid4()),
                    ("class_index", "class_label", "precision", "recall", "f1", "support"),
                    tuple(
                        (
                            i,
                            str(v),
                            float(precision[i]),
                            float(recall[i]),
                            float(fscore[i]),
                            int(support[i]),
                        )
                        for i, v in enumerate(labels)
                    ),
                )
            )
            matrix = confusion_matrix(
                y.iloc[split.validation], predicted, labels=list(range(len(labels)))
            )
            tables.append(
                ResultTable(
                    str(uuid4()),
                    ("actual_class", "predicted_class", "count"),
                    tuple(
                        (str(actual), str(prediction), int(matrix[i, j]))
                        for i, actual in enumerate(labels)
                        for j, prediction in enumerate(labels)
                    ),
                )
            )
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED if improves else ResultOutcome.PARTIAL,
            metrics=tuple(metrics),
            tables=tuple(tables),
            artifacts=tuple(artifacts),
            methodology={
                "operation": "train_validate",
                "module": self.module_id.value,
                "preprocessing": "fit_train_only",
                "selection_partition": "validation",
                "final_holdout_evaluated": False,
                "baseline_improved": improves,
            },
            sample_summary={
                "train": len(split.train),
                "validation": len(split.validation),
                "test_locked": len(split.test),
            },
            provenance=p,
        )

    def evaluate_or_score(self, spec, context):
        training_id = str(spec.parameters["training_run_id"])
        run = self.helper.run_store.get_run(training_id)
        original = self.helper.run_store.get_spec(run.spec_id)
        saved = self.helper.run_store.result_for_run(training_id)
        if (
            run.project_id != spec.project_id
            or original.module_id is not self.module_id
            or saved is None
        ):
            raise SchemaError({"reason": "binary.audit_context"})
        p = saved.provenance
        if not p.get("model_artifact") or not p.get("recommended_model"):
            raise MethodNotApplicableError({"reason": "binary.no_model_to_score"})
        lock = self.helper.binary_store.get_holdout_lock(training_id)
        selection = holdout_selection_hash(training_id, p)
        final = spec.operation == "final_evaluate"
        if (
            not lock
            or lock["selection_hash"] != selection
            or (
                final
                and (
                    lock["status"] != "access_started"
                    or lock["final_run_id"] != context.run_id
                    or spec.input_version_id != original.input_version_id
                )
            )
            or (not final and lock["status"] != "reported")
        ):
            raise MethodNotApplicableError({"reason": "binary.model_not_finalized"})
        features = tuple(p["feature_column_ids"])
        columns = {str(c["column_id"]): c for c in self.catalog.list_columns(spec.input_version_id)}
        row_id = next(
            c for c, v in columns.items() if v["physical_name"] == "__aa_internal_row_id__"
        )
        mapping = (
            {f: f for f in features} if final else dict(spec.parameters.get("feature_mapping", {}))
        )
        if set(mapping) != set(features) or len(set(mapping.values())) != len(features):
            raise SchemaError({"reason": "binary.scoring_feature_mapping_incomplete"})
        for f in features:
            if (
                mapping[f] not in columns
                or _physical_family(str(columns[mapping[f]]["physical_type"]))
                != p["feature_physical_families"][f]
            ):
                raise SchemaError({"reason": "binary.scoring_schema_mismatch"})
        target = str(p["target_column_id"])
        ids = tuple(dict.fromkeys((row_id, *mapping.values(), *([target] if final else []))))
        frame = self.helper._load_as_ids(spec.input_version_id, ids)
        if final:
            split = self.helper._load_split(p["split_artifact"])
            test_ids = set(split.loc[split.partition == "test", "row_id"].astype(str))
            frame = frame[frame[row_id].astype(str).isin(test_ids)]
        X = frame[list(mapping.values())].rename(columns={v: k for k, v in mapping.items()})
        numeric = [f for f in features if p["feature_semantic_types"][f] == "numeric"]
        categorical = [f for f in features if f not in numeric]
        X = _prepare_features(X, numeric, categorical)
        model = self.helper._load_model(p["model_artifact"])
        context.raise_if_cancelled()
        predicted = model.predict(X)
        if final:
            keys = {_typed_key(v["value"]): i for i, v in enumerate(p["class_labels"])}
            y = (
                frame[target].map(lambda v: keys[_typed_key(v)])
                if self.classification
                else pd.to_numeric(frame[target])
            )
            scores = measured(y, predicted, self.classification)
            return ResultDraft(
                outcome=ResultOutcome.SUCCEEDED,
                metrics=tuple(
                    metric(
                        k,
                        v,
                        {"partition": "final_test", "model_id": p["recommended_model"]["model_id"]},
                        len(y),
                    )
                    for k, v in scores.items()
                ),
                methodology={
                    "operation": "final_evaluate",
                    "model_refit": False,
                    "holdout_access": "locked_before_read",
                },
                sample_summary={"test": len(y)},
                provenance={
                    "training_run_id": training_id,
                    "selection_hash": selection,
                    "input_version_id": spec.input_version_id,
                },
            )
        labels = (
            [p["class_labels"][int(v)]["value"] for v in predicted]
            if self.classification
            else [float(v) for v in predicted]
        )
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            tables=(
                ResultTable(
                    str(uuid4()),
                    ("row_id", "prediction"),
                    tuple(
                        (str(row), label) for row, label in zip(frame[row_id], labels, strict=True)
                    ),
                ),
            ),
            methodology={
                "operation": "score_new_data",
                "refit": False,
                "score_semantics": "class_prediction"
                if self.classification
                else "regression_point_prediction",
            },
            sample_summary={"rows_scored": len(frame)},
            provenance={
                "input_version_id": spec.input_version_id,
                "training_run_id": training_id,
                "feature_mapping": mapping,
            },
        )


class RegressionModule(SupervisedModule):
    def __init__(self, catalog, artifact_store):
        super().__init__(catalog, artifact_store, classification=False)


class MulticlassModule(SupervisedModule):
    def __init__(self, catalog, artifact_store):
        super().__init__(catalog, artifact_store, classification=True)
