"""Training-only cross validation and validation-only model explanation."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold, TimeSeriesSplit

from ...domain.datasets import ColumnUsage
from ...domain.errors import MethodNotApplicableError, SchemaError
from ...domain.plans import AnalysisModuleId
from ...domain.results import Metric, MetricValueState, ResultOutcome, ResultTable
from ..contract import ResultDraft
from .evaluate import positive_scores
from .models import build_models


def training_data(module, spec):
    from .module import _decode_label, _prepare_features, _typed_key

    run_id = str(spec.parameters["training_run_id"])
    run = module.run_store.get_run(run_id)
    original = module.run_store.get_spec(run.spec_id)
    result = module.run_store.result_for_run(run_id)
    if original.module_id is not AnalysisModuleId.BINARY_CLASSIFICATION or (
        run.project_id != spec.project_id
        or original.operation != "train_validate"
        or result is None
        or original.input_version_id != spec.input_version_id
    ):
        raise SchemaError({"reason": "binary.audit_context"})
    p = result.provenance
    features = tuple(p["feature_column_ids"])
    roles = original.column_roles
    extras = [
        r.column_id for r in roles if ColumnUsage.GROUP in r.usages or ColumnUsage.TIME in r.usages
    ]
    metadata = module.catalog.list_columns(spec.input_version_id)
    row_id = next(
        str(c["column_id"]) for c in metadata if c["physical_name"] == "__aa_internal_row_id__"
    )
    target = str(p["target_column_id"])
    ids = tuple(dict.fromkeys((row_id, target, *features, *extras)))
    frame = module._load_as_ids(spec.input_version_id, ids)
    split = module._load_split(p["split_artifact"])
    memberships = dict(zip(split.row_id.astype(str), split.partition, strict=True))
    frame["__partition__"] = frame[row_id].astype(str).map(memberships)
    if frame["__partition__"].isna().any():
        raise SchemaError({"reason": "binary.audit_context"})
    positive = _typed_key(_decode_label(p["positive_label"]))
    semantic = p["feature_semantic_types"]
    numeric = [f for f in features if semantic[f] == "numeric"]
    categorical = [f for f in features if semantic[f] in {"categorical", "boolean"}]
    # No holdout values reach fit, calibration, permutation or drift reference.
    work = frame[frame["__partition__"] != "test"].copy().reset_index(drop=True)
    y = work[target].map(lambda v: int(_typed_key(v) == positive))
    X = _prepare_features(work[list(features)], numeric, categorical)
    return original, result, work, X, y, numeric, categorical, row_id


def fold_indices(original, work, y, folds, seed):
    policy = str(original.parameters.get("split_policy", "stratified"))
    if policy == "group":
        role = next(r for r in original.column_roles if ColumnUsage.GROUP in r.usages)
        iterator = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed).split(
            work, y, groups=work[role.column_id]
        )
    elif policy == "time":
        role = next(r for r in original.column_roles if ColumnUsage.TIME in r.usages)
        timestamps = pd.to_datetime(work[role.column_id], utc=True, errors="coerce")
        if timestamps.isna().any():
            raise MethodNotApplicableError({"reason": "binary.audit_folds"})
        unique = np.sort(timestamps.unique())
        iterator = (
            (np.flatnonzero(timestamps.isin(unique[a])), np.flatnonzero(timestamps.isin(unique[b])))
            for a, b in TimeSeriesSplit(n_splits=folds).split(unique)
        )
    else:
        iterator = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(work, y)
    result = []
    try:
        for train, valid in iterator:
            if (
                y.iloc[train].nunique() != 2
                or y.iloc[valid].nunique() != 2
                or min(len(train), len(valid)) < 4
            ):
                raise MethodNotApplicableError({"reason": "binary.audit_folds"})
            result.append((train, valid))
    except ValueError as exc:
        raise MethodNotApplicableError({"reason": "binary.audit_folds"}) from exc
    return result


def audit_model(module, spec, context):
    original, saved, work, X, y, numeric, categorical, row_id = training_data(module, spec)
    folds = int(spec.parameters.get("folds", 3))
    if not 2 <= folds <= 5:
        raise SchemaError({"reason": "binary.audit_folds"})
    mask = work["__partition__"] == "train"
    train_work = work[mask].reset_index(drop=True)
    train_X, train_y = X[mask].reset_index(drop=True), y[mask].reset_index(drop=True)
    splits = fold_indices(original, train_work, train_y, folds, spec.seed)
    rows, membership = [], []
    for fold, (a, b) in enumerate(splits, 1):
        for partition, indices in (("train", a), ("validation", b)):
            membership.extend(
                (fold, str(train_work.iloc[int(i)][row_id]), partition) for i in indices
            )
        for model_id, model in build_models(
            numeric,
            categorical,
            seed=spec.seed,
            class_weight_policy=str(original.parameters.get("class_weight_policy", "none")),
        ).items():
            context.raise_if_cancelled()
            model.fit(train_X.iloc[a], train_y.iloc[a])
            scores = positive_scores(model, train_X.iloc[b], 1)
            rows.append(
                (
                    fold,
                    model_id,
                    len(a),
                    len(b),
                    float(average_precision_score(train_y.iloc[b], scores)),
                    float(roc_auc_score(train_y.iloc[b], scores)),
                )
            )
        context.emit_progress("binary.audit", fold / folds)
    metrics = []
    for model_id in ("dummy", "logistic_regression", "random_forest"):
        for position, name in ((4, "cv_average_precision"), (5, "cv_roc_auc")):
            values = [r[position] for r in rows if r[1] == model_id]
            metrics.append(
                Metric(
                    str(uuid4()),
                    name,
                    MetricValueState.FINITE,
                    float(np.mean(values)),
                    dimensions={"model_id": model_id, "partition": "training_cv"},
                    sample_size=len(train_work),
                )
            )
    tables = [
        ResultTable(
            str(uuid4()),
            ("fold", "model_id", "train_rows", "validation_rows", "average_precision", "roc_auc"),
            tuple(rows),
        ),
        ResultTable(str(uuid4()), ("fold", "row_id", "partition"), tuple(membership)),
    ]
    p = saved.provenance
    if p.get("model_artifact") and len(X.columns) <= 100:
        valid = work["__partition__"] == "validation"
        model = module._load_model(p["model_artifact"])
        importance = permutation_importance(
            model,
            X[valid],
            y[valid],
            scoring="average_precision",
            n_repeats=5,
            random_state=spec.seed,
            n_jobs=1,
        )
        tables.append(
            ResultTable(
                str(uuid4()),
                ("feature_column_id", "validation_permutation_ap_decrease", "std"),
                tuple(
                    (str(c), float(m), float(s))
                    for c, m, s in zip(
                        X.columns,
                        importance.importances_mean,
                        importance.importances_std,
                        strict=True,
                    )
                ),
            )
        )
    run = module.run_store.get_run(str(spec.parameters["training_run_id"]))
    card = {
        "training_run_id": run.run_id,
        "input_version_id": original.input_version_id,
        "seed": original.seed,
        "target_column_id": p["target_column_id"],
        "positive_label": p["positive_label"],
        "features": p["feature_column_ids"],
        "split_policy": p["split_policy"],
        "recommended_model": p.get("recommended_model"),
        "environment": run.environment_manifest,
        "holdout_status": (module.binary_store.get_holdout_lock(run.run_id) or {}).get(
            "status", "locked"
        ),
        "limitations": (
            "training_partition_cross_validation_only",
            "validation_permutation_not_causal",
            "scores_uncalibrated",
            "final_holdout_not_evaluated",
        ),
    }
    return ResultDraft(
        outcome=ResultOutcome.SUCCEEDED,
        metrics=tuple(metrics),
        tables=tuple(tables),
        methodology={
            "operation": "audit_model",
            "folds": folds,
            "preprocessing": "fresh_pipeline_fit_inside_each_fold",
            "cv_partition": "train_only",
            "importance_partition": "validation_only",
            "threshold": "fixed_0.5_not_optimized",
            "model_replaced": False,
        },
        sample_summary={"train": len(train_work)},
        provenance={
            "input_version_id": spec.input_version_id,
            "training_run_id": run.run_id,
            "model_card": card,
        },
    )


def calibration_report(module, spec, context):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import brier_score_loss, log_loss

    original, saved, work, X, y, numeric, categorical, _ = training_data(module, spec)
    recommendation = saved.provenance.get("recommended_model")
    if not recommendation or not saved.provenance.get("model_artifact"):
        raise MethodNotApplicableError({"reason": "binary.no_model_to_score"})
    train = work["__partition__"] == "train"
    valid = work["__partition__"] == "validation"
    train_work, train_X, train_y = (
        work[train].reset_index(drop=True),
        X[train].reset_index(drop=True),
        y[train].reset_index(drop=True),
    )
    splits = fold_indices(original, train_work, train_y, 3, spec.seed)
    estimator = build_models(
        numeric,
        categorical,
        seed=spec.seed,
        class_weight_policy=str(original.parameters.get("class_weight_policy", "none")),
    )[str(recommendation["model_id"])]
    calibrated = CalibratedClassifierCV(
        estimator=estimator, method="sigmoid", cv=splits, ensemble=True, n_jobs=1
    )
    context.raise_if_cancelled()
    calibrated.fit(train_X, train_y)
    original_model = module._load_model(saved.provenance["model_artifact"])
    rows, metrics = [], []
    for model_id, model in (("uncalibrated", original_model), ("sigmoid_cv", calibrated)):
        context.raise_if_cancelled()
        scores = positive_scores(model, X[valid], 1)
        observed = y[valid].to_numpy()
        for name, value in (
            ("brier_score", brier_score_loss(observed, scores, pos_label=1)),
            ("log_loss", log_loss(observed, np.column_stack((1 - scores, scores)), labels=[0, 1])),
        ):
            metrics.append(
                Metric(
                    str(uuid4()),
                    name,
                    MetricValueState.FINITE,
                    float(value),
                    dimensions={"model_id": model_id, "partition": "validation"},
                    sample_size=len(observed),
                )
            )
        bins = np.minimum((scores * 10).astype(int), 9)
        for index in range(10):
            mask = bins == index
            if mask.any():
                rows.append(
                    (
                        model_id,
                        index,
                        int(mask.sum()),
                        float(scores[mask].mean()),
                        float(observed[mask].mean()),
                    )
                )
    return ResultDraft(
        outcome=ResultOutcome.SUCCEEDED,
        metrics=tuple(metrics),
        tables=(
            ResultTable(
                str(uuid4()),
                (
                    "model_id",
                    "probability_bin",
                    "n",
                    "mean_predicted_probability",
                    "observed_fraction",
                ),
                tuple(rows),
            ),
        ),
        methodology={
            "operation": "calibration_report",
            "calibration_fit_partition": "training_cross_validation_only",
            "evaluation_partition": "validation_only",
            "method": "sigmoid_cv_ensemble",
            "final_holdout_used": False,
            "production_model_replaced": False,
            "brier_is_not_calibration_only": True,
            "deployment": "diagnostic_only_requires_separate_finalization_for_new_model",
        },
        sample_summary={"train": int(train.sum()), "validation": int(valid.sum())},
        provenance={
            "input_version_id": spec.input_version_id,
            "training_run_id": str(spec.parameters["training_run_id"]),
        },
    )


def drift_report(module, spec, context):
    from dataclasses import replace

    from scipy.stats import ks_2samp

    from .module import _physical_family, _prepare_features

    training_run = module.run_store.get_run(str(spec.parameters["training_run_id"]))
    if training_run.project_id != spec.project_id:
        raise SchemaError({"reason": "binary.audit_context"})
    original = module.run_store.get_spec(training_run.spec_id)
    _, saved, work, X, _, numeric, categorical, _ = training_data(
        module, replace(spec, input_version_id=original.input_version_id)
    )
    features = tuple(saved.provenance["feature_column_ids"])
    mapping = dict(spec.parameters.get("feature_mapping", {}))
    if set(mapping) != set(features) or len(set(mapping.values())) != len(features):
        raise SchemaError({"reason": "binary.scoring_feature_mapping_incomplete"})
    new_columns = {
        str(c["column_id"]): c for c in module.catalog.list_columns(spec.input_version_id)
    }
    for feature in features:
        if (
            mapping[feature] not in new_columns
            or _physical_family(str(new_columns[mapping[feature]]["physical_type"]))
            != saved.provenance["feature_physical_families"][feature]
        ):
            raise SchemaError({"reason": "binary.scoring_schema_mismatch"})
    new_raw = module._load_as_ids(spec.input_version_id, tuple(mapping[f] for f in features))
    new = _prepare_features(
        new_raw.rename(columns={v: k for k, v in mapping.items()}), numeric, categorical
    )
    reference = X[work["__partition__"] == "train"]
    if not len(new) or not len(reference):
        raise MethodNotApplicableError({"reason": "binary.audit_folds"})
    rows = []
    for feature in features:
        context.raise_if_cancelled()
        a, b = reference[feature], new[feature]
        if feature in numeric:
            av = (
                pd.to_numeric(a, errors="coerce")
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
                .to_numpy()
            )
            bv = (
                pd.to_numeric(b, errors="coerce")
                .replace([np.inf, -np.inf], np.nan)
                .dropna()
                .to_numpy()
            )
            if len(av) and len(bv):
                internal = np.unique(np.quantile(av, np.linspace(0, 1, 11)))[1:-1]
                edges = np.concatenate(([-np.inf], internal, [np.inf]))
                pa = np.histogram(av, edges)[0] / len(av)
                pb = np.histogram(bv, edges)[0] / len(bv)
                pa, pb = np.maximum(pa, 1e-6), np.maximum(pb, 1e-6)
                score = float(np.sum((pb - pa) * np.log(pb / pa)))
                distance = float(ks_2samp(av, bv).statistic)
            else:
                score, distance = None, None
            novel = None
            flagged = (score is not None and score > 0.2) or (
                distance is not None and distance > 0.2
            )
        else:
            ac, bc = (
                a.fillna("<NULL>").value_counts(normalize=True),
                b.fillna("<NULL>").value_counts(normalize=True),
            )
            categories = ac.index.union(bc.index)
            distance = float(sum(abs(ac.get(v, 0) - bc.get(v, 0)) for v in categories) / 2)
            novel = float((~b.fillna("<NULL>").isin(ac.index)).mean())
            score = None
            flagged = distance > 0.2 or novel > 0.1
        missing_change = float(b.isna().mean() - a.isna().mean())
        flagged = bool(flagged or abs(missing_change) > 0.1)
        rows.append((feature, len(a), len(b), score, distance, novel, missing_change, flagged))
    return ResultDraft(
        outcome=ResultOutcome.SUCCEEDED,
        tables=(
            ResultTable(
                str(uuid4()),
                (
                    "feature_column_id",
                    "reference_rows",
                    "new_rows",
                    "psi",
                    "distribution_distance",
                    "novel_fraction",
                    "missing_fraction_change",
                    "heuristic_drift_warning",
                ),
                tuple(rows),
            ),
        ),
        methodology={
            "operation": "drift_report",
            "reference_partition": "train_only",
            "numeric_bins": "reference_quantiles",
            "distance": "numeric_ks_statistic_or_categorical_total_variation",
            "thresholds": {"psi": 0.2, "distance": 0.2, "novel": 0.1, "missing_change": 0.1},
            "thresholds_are_heuristic": True,
            "performance_or_concept_drift_estimated": False,
            "model_refit": False,
        },
        sample_summary={"reference_rows": len(reference), "new_rows": len(new)},
        provenance={
            "input_version_id": spec.input_version_id,
            "training_run_id": training_run.run_id,
            "feature_mapping": mapping,
        },
    )
