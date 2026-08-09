from src.ui_state import reset_analysis_state, workflow_stage


def test_workflow_stage_supports_progressive_disclosure() -> None:
    assert workflow_stage(has_data=False, has_target=False, has_diagnostics=False, has_models=False, has_threshold=False, has_final_result=False) == 0
    assert workflow_stage(has_data=True, has_target=False, has_diagnostics=False, has_models=False, has_threshold=False, has_final_result=False) == 1
    assert workflow_stage(has_data=True, has_target=True, has_diagnostics=True, has_models=False, has_threshold=False, has_final_result=False) == 3
    assert workflow_stage(has_data=True, has_target=True, has_diagnostics=True, has_models=True, has_threshold=True, has_final_result=False) == 5
    assert workflow_stage(has_data=True, has_target=True, has_diagnostics=True, has_models=True, has_threshold=True, has_final_result=True) == 6


def test_reset_analysis_state_clears_analysis_and_widget_state() -> None:
    state = {
        "analysis_nonce": 3,
        "language": "tr",
        "analysis_key": ("file.csv", 10, "target"),
        "trained_models": {"model": object()},
        "selected_threshold": 0.7,
        "final_evaluation": object(),
        "uploader_3": object(),
        "target_3": "target",
    }
    reset_analysis_state(state)
    assert state == {"analysis_nonce": 4, "language": "tr"}
