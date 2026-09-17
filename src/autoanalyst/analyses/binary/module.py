"""Leakage-aware binary classification module for AutoAnalyst V2."""

from __future__ import annotations

import hashlib
import math
import warnings
from pathlib import Path
from uuid import uuid4

import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sklearn.exceptions import ConvergenceWarning

from ...data.schema import INTERNAL_ROW_ID
from ...data.table_access import TableAccess
from ...domain.codec import canonical_json, encode_typed_label
from ...domain.datasets import ColumnUsage, SemanticType
from ...domain.errors import DataError, MethodNotApplicableError, SchemaError
from ...domain.plans import AnalysisModuleId, AnalysisSpec, LearningScope
from ...domain.results import (
    Artifact,
    Finding,
    FindingSeverity,
    Metric,
    MetricValueState,
    ResultOutcome,
    ResultTable,
)
from ...storage.artifacts import ArtifactStore
from ...storage.binary import BinaryStore, holdout_selection_hash
from ...storage.runs import RunStore
from ...storage.sqlite import SQLiteCatalog
from ..contract import (
    AnalysisDescription,
    ApplicabilityReport,
    ExecutionContext,
    ResourceEstimate,
    ResultDraft,
    ValidationIssue,
)
from .evaluate import BinaryMetrics, evaluate_scores, positive_scores
from .models import build_models
from .split import Partitions, make_partitions
from .threshold import select_threshold

_MAX_ENCODED_FEATURES = 10_000
_SPLIT_WARNING_CLASS_COUNT = 30


class BinaryClassificationModule:
    MODULE_VERSION = "2.0"
    OPERATIONS = ("train_validate", "final_evaluate", "score_new_data")

    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.table_access = TableAccess(catalog, artifact_store)
        self.run_store = RunStore(catalog)
        self.binary_store = BinaryStore(catalog)

    def describe(self) -> AnalysisDescription:
        return AnalysisDescription(
            AnalysisModuleId.BINARY_CLASSIFICATION, self.MODULE_VERSION, self.OPERATIONS
        )

    def validate(self, spec: AnalysisSpec) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if spec.module_id is not AnalysisModuleId.BINARY_CLASSIFICATION:
            issues.append(ValidationIssue("binary.module_mismatch"))
        if spec.operation not in self.OPERATIONS:
            issues.append(ValidationIssue("binary.unsupported_operation"))
        if spec.operation == "train_validate":
            if "positive_label" not in spec.parameters:
                issues.append(ValidationIssue("binary.positive_label_required"))
            minimum_recall = spec.parameters.get("minimum_recall", 0.85)
            if (
                isinstance(minimum_recall, bool)
                or not isinstance(minimum_recall, (int, float))
                or not 0 < float(minimum_recall) <= 1
            ):
                issues.append(ValidationIssue("binary.invalid_minimum_recall"))
            split_policy = str(spec.parameters.get("split_policy", "stratified"))
            if split_policy not in {"stratified", "group", "time", "group_time"}:
                issues.append(ValidationIssue("binary.invalid_split_policy"))
        if (
            spec.operation in {"final_evaluate", "score_new_data"}
            and "training_run_id" not in spec.parameters
        ):
            issues.append(ValidationIssue("binary.training_run_required"))
        return tuple(issues)

    def check_applicability(self, spec: AnalysisSpec) -> ApplicabilityReport:
        issues = self.validate(spec)
        if issues:
            return ApplicabilityReport(blocking_issues=issues)
        try:
            version = self.catalog.get_version(spec.input_version_id)
        except Exception:
            return ApplicabilityReport(blocking_issues=(ValidationIssue("binary.dataset_missing"),))
        if spec.operation == "train_validate" and version.row_count < 40:
            return ApplicabilityReport(
                blocking_issues=(ValidationIssue("binary.dataset_too_small"),),
                sample_summary={"row_count": version.row_count},
            )
        return ApplicabilityReport(sample_summary={"row_count": version.row_count})

    def estimate_resources(self, spec: AnalysisSpec) -> ResourceEstimate:
        version = self.catalog.get_version(spec.input_version_id)
        cells = max(1, version.row_count * max(1, version.column_count))
        multiplier = 96 if spec.operation == "train_validate" else 40
        duration = max(0.2, cells / 750_000)
        return ResourceEstimate(
            max(16_000_000, min(4_000_000_000, cells * multiplier)),
            max(4_000_000, min(1_000_000_000, cells * 12)),
            duration,
            1,
            output_bytes=max(1_000_000, min(1_000_000_000, cells * 8)),
            seconds_range=(duration, duration * 3),
            estimate_basis={"cells": cells, "operation": spec.operation},
        )

    def run(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        issues = self.validate(spec)
        if issues:
            raise SchemaError(
                {"reason": "invalid_binary_spec", "codes": tuple(i.code for i in issues)}
            )
        context.raise_if_cancelled()
        if spec.operation == "train_validate":
            return self._train_validate(spec, context)
        if spec.operation == "final_evaluate":
            return self._final_evaluate(spec, context)
        return self._score(spec, context)

    def _train_validate(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        target_role, feature_roles, group_role, time_role = _roles(spec)
        protected = {target_role.column_id, *(role.column_id for role in feature_roles)}
        self._assert_no_dataset_leakage(spec.input_version_id, protected)
        target_id = target_role.column_id
        feature_ids = [role.column_id for role in feature_roles]
        if not feature_ids:
            raise MethodNotApplicableError({"reason": "binary.no_features"})
        if any(role.available_at_prediction is False for role in feature_roles):
            raise MethodNotApplicableError({"reason": "binary.feature_unavailable_at_prediction"})
        unsupported = [
            role.column_id
            for role in feature_roles
            if role.semantic_type
            in {SemanticType.DATETIME, SemanticType.IDENTIFIER, SemanticType.TEXT}
        ]
        if unsupported:
            raise MethodNotApplicableError(
                {"reason": "binary.unsupported_feature_type", "column_ids": tuple(unsupported)}
            )

        column_metadata = {
            str(row["column_id"]): row for row in self.catalog.list_columns(spec.input_version_id)
        }
        system_row = next(
            row for row in column_metadata.values() if row["physical_name"] == INTERNAL_ROW_ID
        )
        load_ids = [str(system_row["column_id"]), target_id, *feature_ids]
        split_policy = str(spec.parameters.get("split_policy", "stratified"))
        if split_policy == "group_time":
            raise MethodNotApplicableError({"reason": "combined_group_time_not_supported"})
        group_id = group_role.column_id if split_policy == "group" and group_role else None
        time_id = time_role.column_id if split_policy == "time" and time_role else None
        if split_policy == "group" and group_id is None:
            raise SchemaError({"reason": "group_split_requires_group_role"})
        if split_policy == "time" and time_id is None:
            raise SchemaError({"reason": "time_split_requires_time_role"})
        for extra in (group_id, time_id):
            if extra and extra not in load_ids:
                load_ids.append(extra)
        frame = self._load_as_ids(spec.input_version_id, tuple(load_ids))
        if frame[target_id].isna().any():
            raise MethodNotApplicableError({"reason": "binary.target_contains_missing"})
        labels = _typed_distinct(frame[target_id])
        if len(labels) != 2:
            raise MethodNotApplicableError(
                {"reason": "binary.target_not_binary", "distinct": len(labels)}
            )
        positive = _py(spec.parameters["positive_label"])
        positive_key = _typed_key(positive)
        by_key = {_typed_key(value): value for value in labels}
        if positive_key not in by_key:
            raise MethodNotApplicableError({"reason": "binary.positive_label_not_found"})
        negative = next(value for key, value in by_key.items() if key != positive_key)
        y = frame[target_id].map(lambda value: 1 if _typed_key(_py(value)) == positive_key else 0)
        work = frame.copy()
        work["__aa_target_binary__"] = y

        numeric = [
            role.column_id for role in feature_roles if role.semantic_type is SemanticType.NUMERIC
        ]
        categorical = [
            role.column_id
            for role in feature_roles
            if role.semantic_type in {SemanticType.CATEGORICAL, SemanticType.BOOLEAN}
        ]
        if len(numeric) + 100 * len(categorical) > _MAX_ENCODED_FEATURES:
            raise MethodNotApplicableError(
                {
                    "reason": "binary.encoded_feature_limit_estimate_exceeded",
                    "max_encoded_features": _MAX_ENCODED_FEATURES,
                }
            )
        X = _prepare_features(work[feature_ids], numeric, categorical)
        if (
            numeric
            and not np.isfinite(
                X[numeric].to_numpy(dtype=float, na_value=np.nan)[~pd.isna(X[numeric]).to_numpy()]
            ).all()
        ):
            raise MethodNotApplicableError({"reason": "binary.non_finite_numeric_feature"})
        split_frame = pd.concat([X, work[["__aa_target_binary__"]]], axis=1)
        if group_id:
            split_frame[group_id] = work[group_id]
        if time_id:
            split_frame[time_id] = work[time_id]
        partitions = make_partitions(
            split_frame,
            target="__aa_target_binary__",
            strategy=split_policy,
            seed=spec.seed,
            group=group_id,
            time=time_id,
        )

        class_weight = str(spec.parameters.get("class_weight_policy", "none"))
        models = build_models(
            numeric, categorical, seed=spec.seed, class_weight_policy=class_weight
        )
        minimum_recall = float(spec.parameters.get("minimum_recall", 0.85))
        summaries: list[dict[str, object]] = []
        fitted: dict[str, object] = {}
        model_count = len(models)
        for index, (model_id, model) in enumerate(models.items(), start=1):
            context.emit_progress("binary.fit", (index - 1) / model_count, {"model_id": model_id})
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ConvergenceWarning)
                model.fit(X.iloc[partitions.train], y.iloc[partitions.train])
                if any(issubclass(item.category, ConvergenceWarning) for item in caught):
                    raise MethodNotApplicableError(
                        {"reason": "binary.model_convergence_failure", "model_id": model_id}
                    )
            transformed = model.named_steps["preprocess"].transform(
                X.iloc[partitions.train[: min(len(partitions.train), 1)]]
            )
            if transformed.shape[1] > _MAX_ENCODED_FEATURES:
                raise MethodNotApplicableError(
                    {
                        "reason": "binary.encoded_feature_limit_exceeded",
                        "model_id": model_id,
                        "encoded_features": int(transformed.shape[1]),
                        "max_encoded_features": _MAX_ENCODED_FEATURES,
                    }
                )
            scores = positive_scores(model, X.iloc[partitions.validation], 1)
            selected = select_threshold(
                y.iloc[partitions.validation].to_numpy(),
                scores,
                positive_label=1,
                minimum_recall=minimum_recall,
            )
            if selected is None:
                summaries.append({"model_id": model_id, "feasible": False})
                fitted[model_id] = model
                continue
            evaluated = evaluate_scores(
                y.iloc[partitions.validation].to_numpy(),
                scores,
                positive_label=1,
                negative_label=0,
                threshold=selected.threshold,
            )
            summary = {
                "model_id": model_id,
                "feasible": True,
                "threshold": selected.threshold,
                **_evaluation_values(evaluated),
            }
            summaries.append(summary)
            fitted[model_id] = model

        recommendation = _recommend(summaries)
        artifacts: list[Artifact] = []
        model_artifact = None
        if recommendation is not None:
            model_artifact = self._save_model(
                fitted[str(recommendation["model_id"])], spec, context
            )
            artifacts.append(model_artifact)
        split_artifact = self._save_split(
            frame[str(system_row["column_id"])], partitions, spec, context
        )
        artifacts.append(split_artifact)

        table_columns = (
            "model_id",
            "feasible",
            "threshold",
            "precision",
            "recall",
            "average_precision",
            "roc_auc",
            "f1",
            "tn",
            "fp",
            "fn",
            "tp",
            "predicted_positive_rate",
        )
        rows = tuple(tuple(item.get(column) for column in table_columns) for item in summaries)
        table = ResultTable(str(uuid4()), table_columns, rows)
        result_metrics: list[Metric] = []
        for item in summaries:
            if not item.get("feasible"):
                continue
            dimensions = {"model_id": str(item["model_id"]), "partition": "validation"}
            for name in (
                "precision",
                "recall",
                "average_precision",
                "roc_auc",
                "f1",
                "tn",
                "fp",
                "fn",
                "tp",
                "predicted_positive_rate",
            ):
                result_metrics.append(_metric(name, float(item[name]), dimensions))

        findings = list(_split_findings(y, partitions))
        outcome = ResultOutcome.SUCCEEDED
        if recommendation is None:
            outcome = ResultOutcome.PARTIAL
            findings.append(
                Finding(str(uuid4()), "binary.no_baseline_improvement", FindingSeverity.WARNING)
            )
        provenance = {
            "input_version_id": spec.input_version_id,
            "spec_hash": spec.spec_hash,
            "target_column_id": target_id,
            "positive_label": encode_typed_label(positive),
            "negative_label": encode_typed_label(_py(negative)),
            "feature_column_ids": tuple(feature_ids),
            "feature_semantic_types": {
                role.column_id: role.semantic_type.value for role in feature_roles
            },
            "feature_physical_families": {
                feature_id: _physical_family(str(column_metadata[feature_id]["physical_type"]))
                for feature_id in feature_ids
            },
            "split_policy": split_policy,
            "split_artifact": _artifact_payload(split_artifact),
            "recommended_model": recommendation,
            "model_artifact": _artifact_payload(model_artifact) if model_artifact else None,
        }
        context.emit_progress("binary.fit", 1.0, {"models": model_count})
        return ResultDraft(
            outcome=outcome,
            metrics=tuple(result_metrics),
            findings=tuple(findings),
            tables=(table,),
            artifacts=tuple(artifacts),
            methodology={
                "module": "binary_classification",
                "module_version": self.MODULE_VERSION,
                "operation": "train_validate",
                "models": ("dummy", "logistic_regression", "random_forest"),
                "minimum_recall": minimum_recall,
                "class_weight_policy": class_weight,
                "split_policy": split_policy,
                "encoded_feature_cap": _MAX_ENCODED_FEATURES,
            },
            sample_summary={
                "train": len(partitions.train),
                "validation": len(partitions.validation),
                "test_locked": len(partitions.test),
            },
            provenance=provenance,
        )

    def _final_evaluate(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        training_run_id = str(spec.parameters["training_run_id"])
        training_result = self.run_store.result_for_run(training_run_id)
        if training_result is None:
            raise DataError({"reason": "binary.training_result_missing"})
        provenance = training_result.provenance
        if provenance.get("input_version_id") != spec.input_version_id:
            raise SchemaError({"reason": "binary.final_input_version_mismatch"})
        recommendation = provenance.get("recommended_model")
        model_meta = provenance.get("model_artifact")
        split_meta = provenance.get("split_artifact")
        if not recommendation or not model_meta or not split_meta:
            raise MethodNotApplicableError({"reason": "binary.no_model_to_finalize"})
        selection_hash = holdout_selection_hash(training_run_id, provenance)
        lock = self.binary_store.get_holdout_lock(training_run_id)
        if (
            lock is None
            or lock["final_run_id"] != context.run_id
            or lock["selection_hash"] != selection_hash
            or lock["status"] != "access_started"
        ):
            raise SchemaError({"reason": "binary.final_holdout_not_locked"})
        # The coordinator persists ACCESS_STARTED before module.run. The module
        # only reads the already-locked test membership and performs computation.
        context.raise_if_cancelled()
        model = self._load_model(model_meta)
        split = self._load_split(split_meta)
        target_id = str(provenance["target_column_id"])
        feature_ids = tuple(str(x) for x in provenance["feature_column_ids"])
        system_row = next(
            row
            for row in self.catalog.list_columns(spec.input_version_id)
            if row["physical_name"] == INTERNAL_ROW_ID
        )
        row_id_col = str(system_row["column_id"])
        frame = self._load_as_ids(spec.input_version_id, (row_id_col, target_id, *feature_ids))
        test_ids = set(split.loc[split["partition"] == "test", "row_id"].astype(str))
        test = frame[frame[row_id_col].astype(str).isin(test_ids)]
        positive = _decode_label(provenance["positive_label"])
        y = test[target_id].map(
            lambda value: 1 if _typed_key(_py(value)) == _typed_key(positive) else 0
        )
        semantic = dict(provenance["feature_semantic_types"])
        numeric = [column for column in feature_ids if semantic.get(column) == "numeric"]
        categorical = [
            column for column in feature_ids if semantic.get(column) in {"categorical", "boolean"}
        ]
        X = _prepare_features(test[list(feature_ids)], numeric, categorical)
        scores = positive_scores(model, X, 1)
        threshold = float(recommendation["threshold"])
        evaluated = evaluate_scores(
            y.to_numpy(), scores, positive_label=1, negative_label=0, threshold=threshold
        )
        dimensions = {"partition": "final_test", "model_id": recommendation["model_id"]}
        metrics = tuple(
            _metric(name, float(value), dimensions)
            for name, value in _evaluation_values(evaluated).items()
        )
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            metrics=metrics,
            methodology={
                "module": "binary_classification",
                "module_version": self.MODULE_VERSION,
                "operation": "final_evaluate",
                "holdout_access": "locked_before_read",
                "threshold_reoptimized": False,
                "model_refit": False,
            },
            sample_summary={"test": len(test)},
            provenance={
                "training_run_id": training_run_id,
                "selection_hash": selection_hash,
                "threshold": threshold,
                "model_id": recommendation["model_id"],
            },
        )

    def _score(self, spec: AnalysisSpec, context: ExecutionContext) -> ResultDraft:
        training_run_id = str(spec.parameters["training_run_id"])
        training_result = self.run_store.result_for_run(training_run_id)
        if training_result is None:
            raise DataError({"reason": "binary.training_result_missing"})
        provenance = training_result.provenance
        recommendation = provenance.get("recommended_model")
        model_meta = provenance.get("model_artifact")
        if not recommendation or not model_meta:
            raise MethodNotApplicableError({"reason": "binary.no_model_to_score"})

        lock = self.binary_store.get_holdout_lock(training_run_id)
        if lock is None or lock["status"] != "reported":
            raise MethodNotApplicableError({"reason": "binary.model_not_finalized"})
        final_run_id = str(lock["final_run_id"])
        if self.run_store.result_for_run(final_run_id) is None:
            raise DataError({"reason": "binary.finalization_result_missing"})
        expected_selection = holdout_selection_hash(training_run_id, provenance)
        if lock["selection_hash"] != expected_selection:
            raise DataError({"reason": "binary.finalized_selection_mismatch"})

        training_features = tuple(str(x) for x in provenance["feature_column_ids"])
        mapping = {
            str(key): str(value)
            for key, value in dict(spec.parameters.get("feature_mapping", {})).items()
        }
        expected_keys = set(training_features)
        actual_keys = set(mapping)
        if actual_keys != expected_keys:
            raise SchemaError(
                {
                    "reason": "binary.scoring_feature_mapping_incomplete",
                    "missing": tuple(sorted(expected_keys - actual_keys)),
                    "extra": tuple(sorted(actual_keys - expected_keys)),
                }
            )
        scoring_ids = tuple(mapping[feature] for feature in training_features)
        if len(set(scoring_ids)) != len(scoring_ids):
            raise SchemaError({"reason": "binary.scoring_feature_mapping_not_one_to_one"})

        expected_families = {
            str(key): str(value)
            for key, value in dict(provenance.get("feature_physical_families", {})).items()
        }
        if set(expected_families) != expected_keys:
            raise DataError({"reason": "binary.training_feature_contract_missing"})
        scoring_metadata = {
            str(row["column_id"]): row for row in self.catalog.list_columns(spec.input_version_id)
        }
        for training_feature, scoring_id in zip(training_features, scoring_ids, strict=True):
            scoring_column = scoring_metadata.get(scoring_id)
            if scoring_column is None:
                raise SchemaError({"reason": "binary.column_missing", "column_ids": (scoring_id,)})
            actual_family = _physical_family(str(scoring_column["physical_type"]))
            expected_family = expected_families[training_feature]
            if actual_family != expected_family:
                raise MethodNotApplicableError(
                    {
                        "reason": "binary.scoring_schema_mismatch",
                        "training_feature_id": training_feature,
                        "scoring_column_id": scoring_id,
                        "expected_family": expected_family,
                        "actual_family": actual_family,
                    }
                )

        system_row = next(
            row for row in scoring_metadata.values() if row["physical_name"] == INTERNAL_ROW_ID
        )
        row_id_col = str(system_row["column_id"])
        frame = self._load_as_ids(spec.input_version_id, (row_id_col, *scoring_ids))
        remapped = pd.DataFrame(
            {
                training: frame[scoring]
                for training, scoring in zip(training_features, scoring_ids, strict=True)
            }
        )
        semantic = dict(provenance["feature_semantic_types"])
        numeric = [column for column in training_features if semantic.get(column) == "numeric"]
        categorical = [
            column
            for column in training_features
            if semantic.get(column) in {"categorical", "boolean"}
        ]
        X = _prepare_features(remapped, numeric, categorical)
        context.raise_if_cancelled()
        model = self._load_model(model_meta)
        scores = positive_scores(model, X, 1)
        threshold = float(recommendation["threshold"])
        positive = _decode_label(provenance["positive_label"])
        negative = _decode_label(provenance["negative_label"])
        labels = [positive if score >= threshold else negative for score in scores]
        table = ResultTable(
            str(uuid4()),
            ("row_id", "score_positive", "predicted_label"),
            tuple(
                (str(row_id), float(score), _py(label))
                for row_id, score, label in zip(frame[row_id_col], scores, labels, strict=True)
            ),
        )
        return ResultDraft(
            outcome=ResultOutcome.SUCCEEDED,
            tables=(table,),
            methodology={
                "module": "binary_classification",
                "module_version": self.MODULE_VERSION,
                "operation": "score_new_data",
                "score_semantics": "uncalibrated_model_score",
                "threshold_locked": True,
                "refit": False,
                "rethreshold": False,
            },
            sample_summary={"rows_scored": len(frame)},
            provenance={
                "training_run_id": training_run_id,
                "final_run_id": final_run_id,
                "model_id": recommendation["model_id"],
                "threshold": threshold,
                "feature_mapping": mapping,
            },
        )

    def _load_as_ids(self, version_id: str, column_ids: tuple[str, ...]) -> pd.DataFrame:
        metadata = {str(row["column_id"]): row for row in self.catalog.list_columns(version_id)}
        missing = [item for item in column_ids if item not in metadata]
        if missing:
            raise SchemaError({"reason": "binary.column_missing", "column_ids": tuple(missing)})
        frame = self.table_access.selected_columns(version_id, column_ids).to_pandas()
        return frame.rename(
            columns={str(metadata[column]["physical_name"]): column for column in column_ids}
        )

    def _assert_no_dataset_leakage(self, version_id: str, protected: set[str]) -> None:
        visited: set[str] = set()
        stack = [version_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            version = self.catalog.get_version(current)
            if version.recipe_id:
                recipe = self.catalog.get_preparation_recipe(version.recipe_id)
                for step in recipe.ordered_steps:
                    if step.learning_scope is LearningScope.DATASET and protected.intersection(
                        step.affected_column_ids
                    ):
                        raise MethodNotApplicableError(
                            {
                                "reason": "binary.dataset_level_leakage",
                                "step_id": step.step_id,
                                "operation": step.operation,
                            }
                        )
            for edge in self.catalog.get_version_lineage(current):
                stack.append(str(edge["input_version_id"]))

    def _save_model(self, model, spec: AnalysisSpec, context: ExecutionContext) -> Artifact:
        staged = self.artifact_store.create_staging(
            project_id=spec.project_id,
            owner_run_id=context.run_id,
            kind="binary_model",
            media_type="application/x-joblib",
            format_version="2.0",
        )
        joblib.dump(model, staged.staging_path)
        return self.artifact_store.finalize(staged)

    def _save_split(
        self,
        row_ids: pd.Series,
        partitions: Partitions,
        spec: AnalysisSpec,
        context: ExecutionContext,
    ) -> Artifact:
        labels = np.empty(len(row_ids), dtype=object)
        labels[partitions.train] = "train"
        labels[partitions.validation] = "validation"
        labels[partitions.test] = "test"
        frame = pd.DataFrame({"row_id": row_ids.astype(str).to_numpy(), "partition": labels})
        staged = self.artifact_store.create_staging(
            project_id=spec.project_id,
            owner_run_id=context.run_id,
            kind="split_manifest",
            media_type="application/vnd.apache.parquet",
            format_version="2.0",
        )
        pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), staged.staging_path)
        return self.artifact_store.finalize(staged)

    def _load_model(self, metadata):
        path = self._verified_path(metadata)
        return joblib.load(path)

    def _load_split(self, metadata) -> pd.DataFrame:
        return pq.read_table(self._verified_path(metadata)).to_pandas()

    def _verified_path(self, metadata) -> Path:
        path = self.artifact_store.resolve_relative_path(str(metadata["relative_path"]))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != metadata["sha256"]:
            raise DataError({"reason": "binary_artifact_checksum_mismatch"})
        return path


def _roles(spec: AnalysisSpec):
    targets = [role for role in spec.column_roles if ColumnUsage.TARGET in role.usages]
    features = [role for role in spec.column_roles if ColumnUsage.FEATURE in role.usages]
    groups = [role for role in spec.column_roles if ColumnUsage.GROUP in role.usages]
    times = [role for role in spec.column_roles if ColumnUsage.TIME in role.usages]
    if len(targets) != 1:
        raise SchemaError({"reason": "binary.exactly_one_target_required"})
    if len(groups) > 1:
        raise SchemaError({"reason": "binary.at_most_one_group_role"})
    if len(times) > 1:
        raise SchemaError({"reason": "binary.at_most_one_time_role"})
    return targets[0], features, groups[0] if groups else None, times[0] if times else None


def _prepare_features(
    frame: pd.DataFrame, numeric: list[str], categorical: list[str]
) -> pd.DataFrame:
    result = frame.copy()
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in categorical:
        result[column] = result[column].map(
            lambda value: np.nan
            if pd.isna(value)
            else canonical_json(encode_typed_label(_py(value)))
        )
    return result


def _typed_distinct(series: pd.Series) -> list[object]:
    result: dict[str, object] = {}
    for value in series:
        item = _py(value)
        result.setdefault(_typed_key(item), item)
    return list(result.values())


def _typed_key(value: object) -> str:
    return canonical_json(encode_typed_label(_py(value)))


def _py(value):
    if isinstance(value, np.generic):
        return value.item()
    return value


def _decode_label(payload):
    kind = payload["type"]
    value = payload.get("value")
    if kind == "null":
        return None
    return value


def _physical_family(physical_type: str) -> str:
    lowered = physical_type.strip().lower()
    if "bool" in lowered:
        return "boolean"
    if any(token in lowered for token in ("datetime", "timestamp", "date")):
        return "datetime"
    if any(
        token in lowered
        for token in (
            "int",
            "uint",
            "float",
            "double",
            "decimal",
            "numeric",
            "number",
        )
    ):
        return "numeric"
    return "categorical"


def _recommend(items: list[dict[str, object]]) -> dict[str, object] | None:
    feasible = {str(item["model_id"]): item for item in items if item.get("feasible")}
    dummy = feasible.get("dummy")
    candidates = [
        feasible[name] for name in ("logistic_regression", "random_forest") if name in feasible
    ]
    if not candidates or dummy is None:
        return None
    best = sorted(
        candidates,
        key=lambda item: (
            float(item["precision"]),
            float(item["average_precision"]),
            float(item["recall"]),
            1 if item["model_id"] == "logistic_regression" else 0,
        ),
        reverse=True,
    )[0]
    if (
        float(best["precision"]) <= float(dummy["precision"]) + 1e-12
        and float(best["average_precision"]) <= float(dummy["average_precision"]) + 1e-12
    ):
        return None
    return {
        "model_id": best["model_id"],
        "threshold": best["threshold"],
        "precision": best["precision"],
        "recall": best["recall"],
        "average_precision": best["average_precision"],
    }


def _evaluation_values(metrics: BinaryMetrics) -> dict[str, float | int]:
    return {
        "precision": metrics.precision,
        "recall": metrics.recall,
        "average_precision": metrics.average_precision,
        "roc_auc": metrics.roc_auc,
        "f1": metrics.f1,
        "tn": metrics.tn,
        "fp": metrics.fp,
        "fn": metrics.fn,
        "tp": metrics.tp,
        "predicted_positive_rate": metrics.predicted_positive_rate,
    }


def _split_findings(y: pd.Series, partitions: Partitions) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for name, indices in (("validation", partitions.validation), ("test", partitions.test)):
        counts = y.iloc[indices].value_counts(dropna=False)
        minimum = int(counts.min()) if not counts.empty else 0
        if minimum < _SPLIT_WARNING_CLASS_COUNT:
            findings.append(
                Finding(
                    str(uuid4()),
                    "binary.small_partition_class_count",
                    FindingSeverity.WARNING,
                    parameters={
                        "partition": name,
                        "minimum_class_count": minimum,
                        "warning_below": _SPLIT_WARNING_CLASS_COUNT,
                    },
                )
            )
    return tuple(findings)


def _metric(name: str, value: float, dimensions: dict[str, object]) -> Metric:
    if not math.isfinite(value):
        state = (
            MetricValueState.POSITIVE_INFINITY if value > 0 else MetricValueState.NEGATIVE_INFINITY
        )
        return Metric(str(uuid4()), name, state, dimensions=dimensions)
    return Metric(str(uuid4()), name, MetricValueState.FINITE, value=value, dimensions=dimensions)


def _artifact_payload(artifact: Artifact | None):
    if artifact is None:
        return None
    return {
        "artifact_id": artifact.artifact_id,
        "relative_path": artifact.relative_path,
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "media_type": artifact.media_type,
        "kind": artifact.kind,
    }
