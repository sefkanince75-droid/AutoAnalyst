"""Explicit independent-sample mean and exact binomial confidence intervals."""

import math
from uuid import uuid4

import numpy as np
import pandas as pd
from scipy import stats

from ..domain.codec import canonical_json, encode_typed_label
from ..domain.errors import MethodNotApplicableError
from ..domain.results import Finding, FindingSeverity, Metric, MetricValueState


def confidence_interval(module, spec):
    column = str(spec.parameters["column_id"])
    frame, _ = module._load(spec, (column,))
    confidence = float(spec.parameters.get("confidence", 0.95))
    values = frame[column]
    if spec.operation == "mean_interval":
        used = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(used) < 2:
            raise MethodNotApplicableError({"reason": "comparison.interval_insufficient"})
        value = float(used.mean())
        half = float(
            stats.t.ppf((1 + confidence) / 2, len(used) - 1)
            * used.std(ddof=1)
            / math.sqrt(len(used))
        )
        interval = (value - half, value + half)
        name, method = "mean", "student_t_independent_rows"
    else:
        used = values.dropna()
        if not len(used):
            raise MethodNotApplicableError({"reason": "comparison.interval_insufficient"})
        marker = canonical_json(encode_typed_label(spec.parameters["success_label"]))
        count = int(
            used.map(
                lambda v: canonical_json(encode_typed_label(v.item() if hasattr(v, "item") else v))
                == marker
            ).sum()
        )
        estimate = stats.binomtest(count, len(used))
        bounds = estimate.proportion_ci(confidence_level=confidence, method="exact")
        value, interval = count / len(used), (float(bounds.low), float(bounds.high))
        name, method = "proportion", "clopper_pearson_exact_independent_bernoulli"
    if not all(math.isfinite(number) for number in (value, *interval)):
        raise MethodNotApplicableError({"reason": "comparison.interval_insufficient"})
    metric = Metric(
        str(uuid4()), name, MetricValueState.FINITE, value, sample_size=len(used), interval=interval
    )
    draft = module._draft(
        spec,
        metrics=(metric,),
        findings=(
            Finding(
                str(uuid4()),
                "comparison.interval_scope",
                FindingSeverity.INFO,
                parameters={"confidence": confidence},
            ),
        ),
        sample={"rows_used": len(used), "rows_excluded": len(values) - len(used)},
    )
    from dataclasses import replace

    return replace(
        draft,
        findings=draft.findings
        + (
            (
                Finding(
                    str(uuid4()),
                    "comparison.small_interval_sample",
                    FindingSeverity.WARNING,
                    parameters={"n": len(used)},
                ),
            )
            if len(used) < 30
            else ()
        ),
        methodology={
            **dict(draft.methodology),
            "confidence_level": confidence,
            "interval_method": method,
            "independence_confirmed": True,
            "multiple_comparison_adjustment": "none",
            "interpretation": "conditional_on_sampling_and_model_assumptions_not_causal",
        },
    )
