"""Pure helpers for Streamlit workflow visibility and analysis reset."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


ANALYSIS_STATE_KEYS = {
    "analysis_key",
    "trained_models",
    "validation_results",
    "threshold_results",
    "recommendation",
    "recommended_model_name",
    "selected_threshold",
    "optimized_minimum_recall",
    "final_evaluation",
    "final_evaluation_key",
    "model_package_bytes",
}

DATASET_STATE_KEYS = {
    "dataset_identity",
    "prepared_dataset_identity",
    "prepared_dataframe",
    "prepared_source_metadata",
    "approved_combination_identity",
}


def workflow_stage(
    *,
    has_data: bool,
    has_target: bool,
    has_diagnostics: bool,
    has_models: bool,
    has_threshold: bool,
    has_final_result: bool,
) -> int:
    """Return the current zero-based stage in the seven-step V1 workflow."""
    flags = (has_data, has_target, has_diagnostics, has_models, has_threshold, has_final_result)
    for index, complete in enumerate(flags):
        if not complete:
            return index
    return 6


def reset_analysis_state(state: MutableMapping[str, Any]) -> None:
    """Clear analysis/widget state and rotate the uploader key for a fresh workflow."""
    next_nonce = int(state.get("analysis_nonce", 0)) + 1
    for key in list(state):
        if (
            key in ANALYSIS_STATE_KEYS
            or key in DATASET_STATE_KEYS
            or key.startswith("target_")
            or key.startswith("uploader_")
            or key.startswith("worksheet_")
            or key.startswith("mode_")
            or key.startswith("selected_file_")
            or key.startswith("source_column_")
            or key == "minimum_recall_control"
        ):
            state.pop(key, None)
    state["analysis_nonce"] = next_nonce


def invalidate_for_dataset_change(state: MutableMapping[str, Any], new_identity: str) -> bool:
    """Clear analysis results and target widgets when the active dataset changes."""
    if state.get("dataset_identity") == new_identity:
        return False
    for key in list(state):
        if key in ANALYSIS_STATE_KEYS or key in DATASET_STATE_KEYS or key.startswith("target_"):
            state.pop(key, None)
    state["dataset_identity"] = new_identity
    return True
