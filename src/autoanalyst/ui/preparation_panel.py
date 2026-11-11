"""Closed preparation forms, ordered recipe drafts and full-data preview/apply."""

from uuid import uuid4

import pandas as pd
import streamlit as st

from ..application.preparation import PreparationService
from ..domain.codec import encode_typed_label, fingerprint
from ..domain.errors import SchemaError
from ..execution.data_tasks import DataTaskService, active_data_task
from ..localization import I18N, label
from ..preparation.operations import validate_parameters
from .join_form import render_join_form
from .recipe_editor import (
    MAX_DRAFT_STEPS,
    as_columns,
    clear_draft,
    clear_preview,
    draft_controls,
    projected_columns,
    recipe_error,
    seed_form,
)
from .typed_inputs import filter_operators, input_kind, typed_input


def render_preparation_panel(
    runtime, project_id: str, version_id: str, columns: list[dict[str, object]], language: str
) -> None:
    t = I18N[language]
    st.subheader(t["prepare"])
    if st.session_state.pop("preparation_applied", False):
        st.success(t["preparation_applied_notice"])
    service = PreparationService(runtime.catalog, runtime.artifact_store)
    base = runtime.catalog.get_version(version_id)
    dataset = runtime.catalog.get_dataset(base.dataset_id)
    base_columns = columns
    multi = st.checkbox(t["multi_step_recipe"], key="prep_multi")
    draft_context = fingerprint(
        {"project_id": project_id, "version_id": version_id, "head_revision": dataset.head_revision}
    )
    if st.session_state.get("prep_draft_context") != draft_context:
        clear_draft()
        st.session_state.prep_draft_context = draft_context
        st.session_state.prep_draft = []
    draft = st.session_state.prep_draft
    edit_index = None
    if multi:
        draft_controls(runtime, base_columns, language)
        edit_index = next(
            (
                i
                for i, step in enumerate(draft)
                if step["uid"] == st.session_state.get("prep_edit_uid")
            ),
            None,
        )
        try:
            columns = projected_columns(
                base_columns,
                draft[:edit_index] if edit_index is not None else draft,
                runtime.catalog,
            )
        except Exception as exc:
            st.error(recipe_error(exc, language))
            st.info(t["fix_recipe_order"])
            return
        if edit_index is not None:
            st.info(t["editing_step"].format(step=edit_index + 1))
            if st.session_state.pop("prep_load_edit", False):
                seed_form(draft[edit_index], columns)
            if st.button(t["cancel_step_edit"], key="cancel_step_edit"):
                st.session_state.pop("prep_edit_uid", None)
                clear_preview()
                st.session_state.preparation_applied = True
                st.rerun()
    by_name = {str(row["display_name"]): str(row["column_id"]) for row in columns}
    operation = st.selectbox(
        t["operation"],
        (
            "filter_rows",
            "rename_column",
            "cast_column",
            "drop_columns",
            "fill_missing",
            "drop_missing_rows",
            "remove_duplicates",
            "replace_non_finite",
            "replace_values",
            "append_rows",
            "join_rows",
        ),
        key="prep_operation",
        format_func=lambda value: label(value, language),
    )
    params: dict[str, object] = {}
    valid = True
    if operation == "join_rows":
        params, valid = render_join_form(runtime, project_id, version_id, columns, t)
    elif operation == "filter_rows":
        name = st.selectbox(t["column"], tuple(by_name), key="prep_filter_col")
        column = next(row for row in columns if row["column_id"] == by_name[name])
        operator = st.selectbox(
            t["operator"],
            filter_operators(column),
            key=f"prep_operator_{column['column_id']}",
            format_func=lambda value: label(value, language),
        )
        params = {"column_id": by_name[name], "operator": operator}
        if operator not in {"is_null", "is_not_null"}:
            params["value"] = typed_input(column, t["value"], "prep_filter_value", language)
            valid = params["value"] is not None
    elif operation == "rename_column":
        name = st.selectbox(t["column"], tuple(by_name), key="prep_rename_col")
        params = {
            "column_id": by_name[name],
            "new_name": st.text_input(t["new_name"], key="prep_new_name"),
        }
    elif operation == "cast_column":
        name = st.selectbox(t["column"], tuple(by_name), key="prep_cast_col")
        params = {
            "column_id": by_name[name],
            "target_type": st.selectbox(
                t["type"],
                ("numeric", "categorical", "boolean", "datetime"),
                key="prep_cast_type",
                format_func=lambda value: label(value, language),
            ),
            "failure_policy": st.selectbox(
                t["failure_policy"],
                ("strict", "invalid_to_null"),
                key="prep_cast_failure",
                format_func=lambda value: label(value, language),
            ),
        }
    elif operation in {
        "drop_columns",
        "drop_missing_rows",
        "remove_duplicates",
        "replace_non_finite",
    }:
        names = st.multiselect(t["columns"], tuple(by_name), key=f"prep_{operation}")
        params = {"column_ids": [by_name[name] for name in names]}
        valid = bool(names)
        if operation == "remove_duplicates":
            params["keep"] = "first"
    elif operation == "fill_missing":
        names = st.multiselect(t["columns"], tuple(by_name), key="prep_fill_cols")
        strategy = st.selectbox(
            t["strategy"],
            ("median", "mean", "mode", "constant"),
            key="prep_fill_strategy",
            format_func=lambda value: label(value, language),
        )
        params = {"column_ids": [by_name[name] for name in names], "strategy": strategy}
        selected = [row for row in columns if row["column_id"] in params["column_ids"]]
        valid = bool(selected)
        if strategy == "constant":
            kinds = {input_kind(row) for row in selected}
            if len(kinds) > 1:
                st.info(t["mixed_fill"])
                valid = False
            elif selected:
                value = typed_input(selected[0], t["constant_value"], "prep_constant", language)
                valid = value is not None
                if valid:
                    params["value"] = encode_typed_label(value)
        elif strategy in {"mean", "median"} and any(
            input_kind(row) not in {"integer", "numeric"} for row in selected
        ):
            st.info(t["error.numeric_fill_required"])
            valid = False
    elif operation == "replace_values":
        name = st.selectbox(t["column"], tuple(by_name), key="prep_replace_col")
        column = next(row for row in columns if row["column_id"] == by_name[name])
        count = st.number_input(
            t["replacement_pairs"],
            min_value=1,
            max_value=20,
            value=1,
            step=1,
            key="prep_replace_count",
        )
        st.caption(t["replace_exact_hint"])
        pairs = []
        for index in range(count):
            left, right = st.columns(2)
            pair = {}
            for side, container in (("from", left), ("to", right)):
                with container:
                    is_null = st.checkbox(
                        t["replace_null_" + side].format(index=index + 1),
                        key=f"replace_{side}_null_{index}",
                    )
                    value = (
                        None
                        if is_null
                        else typed_input(
                            column,
                            t["replace_" + side].format(index=index + 1),
                            f"replace_{side}_{index}",
                            language,
                        )
                    )
                    if not is_null and value is None:
                        valid = False
                    pair[side] = encode_typed_label(value)
            pairs.append(pair)
        params = {"column_id": by_name[name], "mapping": pairs}
    elif operation == "append_rows":
        used = {
            value
            for i, step in enumerate(draft if multi else [])
            if i != edit_index and step["operation"] == "append_rows"
            for value in step["parameters"]["input_version_ids"]
        }
        candidates = [
            version
            for version in service.append_candidates(version_id, as_columns(columns))
            if version.version_id not in used
        ]
        names = {
            version.version_id: f"{runtime.catalog.get_dataset(version.dataset_id).name} · {version.version_id[:8]} · {version.row_count}"
            for version in candidates
        }
        chosen = st.multiselect(
            t["append_sources"],
            tuple(names),
            format_func=names.__getitem__,
            key="prep_append_inputs",
        )
        st.caption(t["append_compatibility_hint"])
        if not candidates:
            st.info(t["no_compatible_append"])
        if chosen:
            st.text(t["append_order"] + ": " + " → ".join(names[value] for value in chosen))
        params = {"input_version_ids": chosen}
        valid = bool(chosen)
    if multi:
        if st.button(
            t["save_step"] if edit_index is not None else t["add_step"],
            key="save_recipe_step",
            disabled=not valid or (len(draft) >= MAX_DRAFT_STEPS and edit_index is None),
        ):
            try:
                validate_parameters(operation, params)
                item = {
                    "uid": draft[edit_index]["uid"] if edit_index is not None else str(uuid4()),
                    "operation": operation,
                    "parameters": params,
                }
                proposed = list(draft)
                if edit_index is None:
                    proposed.append(item)
                else:
                    proposed[edit_index] = item
                projected_columns(base_columns, proposed, runtime.catalog)
                st.session_state.prep_draft = proposed
                st.session_state.pop("prep_edit_uid", None)
                clear_preview()
                st.rerun()
            except Exception as exc:
                st.error(recipe_error(exc, language))
        st.caption(t["preview_draft_only"])
    recipe_steps = (
        [{"operation": step["operation"], "parameters": step["parameters"]} for step in draft]
        if multi
        else [{"operation": operation, "parameters": params}]
    )
    can_preview = bool(recipe_steps) and edit_index is None if multi else valid
    if not valid and not multi:
        clear_preview()

    context = fingerprint(
        {
            "project_id": project_id,
            "dataset_id": dataset.dataset_id,
            "version_id": version_id,
            "head_revision": dataset.head_revision,
            "operation": operation,
            "parameters": params,
            "multi": multi,
            "steps": recipe_steps,
        }
    )
    if st.session_state.get("preview_context") != context:
        clear_preview()

    delivery = st.session_state.get("data_task_delivery")
    if (
        delivery
        and delivery.get("operation") == "preparation"
        and delivery.get("status") == "completed"
        and delivery.get("context") == context
    ):
        st.session_state.preview_id = delivery["result_id"]
        st.session_state.preview_context = context
        st.session_state.pop("data_task_delivery", None)
    total_rows = base.row_count + sum(
        runtime.catalog.get_version(v).row_count
        for step in recipe_steps
        if step["operation"] == "append_rows"
        for v in step["parameters"]["input_version_ids"]
    )
    total_rows += sum(
        runtime.catalog.get_version(step["parameters"]["right_version_id"]).row_count
        for step in recipe_steps
        if step["operation"] == "join_rows" and "right_version_id" in step["parameters"]
    )
    large = total_rows >= 100_000 or total_rows * base.column_count >= 1_000_000
    isolated = (
        st.checkbox(
            t["data_task_isolate"],
            value=large,
            disabled=large,
            key=f"isolate_preparation_{version_id}",
        )
        or large
    )

    if st.button(
        t["preview_recipe"] if multi else t["preview"],
        key="preview_preparation",
        disabled=not can_preview or bool(active_data_task(runtime.catalog.paths.root)),
    ):
        clear_preview()
        try:
            recipe = service.create_recipe(
                project_id=project_id,
                base_version_id=version_id,
                steps=recipe_steps,
            )
            if isolated:
                job_id = DataTaskService(runtime.catalog).start(
                    operation="preparation",
                    project_id=project_id,
                    dataset_id=dataset.dataset_id,
                    recipe_id=recipe.recipe_id,
                )
                st.session_state.data_task_id = job_id
                st.session_state.data_task_context = context
                st.rerun()
            else:
                preview = service.preview_recipe(recipe.recipe_id)
                st.session_state.preview_id = preview.preview_id
                st.session_state.preview_context = context
        except Exception as exc:
            st.error(recipe_error(exc, language))
    preview_id = st.session_state.get("preview_id")
    if preview_id:
        try:
            preview = service.get_preview(preview_id)
            recipe = service.get_recipe(preview.recipe_id)
            if preview.base_version_id != version_id or recipe.project_id != project_id:
                raise SchemaError({"reason": "preparation_preview_context_mismatch"})
            st.caption(t["preview_target"].format(name=dataset.name))
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            t["before_rows"]: preview.before_row_count,
                            t["after_rows"]: preview.after_row_count,
                            t["before_columns"]: preview.before_column_count,
                            t["after_columns"]: preview.after_column_count,
                        }
                    ]
                ),
                hide_index=True,
            )
            st.subheader(t["step_impacts"])
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            t["step_number"]: i + 1,
                            t["operation"]: label(step.operation, language),
                            t["affected_rows"]: result.affected_row_count,
                            t["affected_cells"]: result.affected_cell_count,
                        }
                        for i, (step, result) in enumerate(
                            zip(recipe.ordered_steps, preview.step_results, strict=True)
                        )
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            for i, (step, result) in enumerate(
                zip(recipe.ordered_steps, preview.step_results, strict=True)
            ):
                if step.operation == "join_rows":
                    st.dataframe(
                        pd.DataFrame(
                            [{label(k, language): v for k, v in result.resolved_values.items()}]
                        ),
                        hide_index=True,
                    )
                for warning in result.warnings:
                    st.warning(
                        t["step_error"].format(
                            step=i + 1,
                            message=t.get("warning." + warning, t["preparation_warning"]),
                        )
                    )
                if (
                    step.operation == "cast_column"
                    and step.parameters["failure_policy"] == "invalid_to_null"
                    and result.affected_row_count
                ):
                    st.warning(
                        t["cast_null_note"].format(step=i + 1, count=result.affected_row_count)
                    )
            if (
                "prep_sample_cache" not in st.session_state
                or st.session_state.prep_sample_cache[0] != preview_id
            ):
                before, after = service.preview_samples(preview_id)
                st.session_state.prep_sample_cache = (
                    preview_id,
                    before.to_pandas(),
                    after.to_pandas(),
                )
            st.caption(t["preparation_samples_hint"])
            left, right = st.columns(2)
            with left:
                st.caption(t["sample_before"])
                st.dataframe(
                    st.session_state.prep_sample_cache[1], hide_index=True, width="stretch"
                )
            with right:
                st.caption(t["sample_after"])
                st.dataframe(
                    st.session_state.prep_sample_cache[2], hide_index=True, width="stretch"
                )
            if st.button(t["apply"], key="apply_preparation"):
                service.apply_preview(
                    preview.preview_id,
                    expected_project_id=project_id,
                    expected_base_version_id=version_id,
                    expected_recipe_hash=recipe.recipe_hash,
                )
                clear_preview()
                if multi:
                    clear_draft()
                st.rerun()
        except Exception as exc:
            clear_preview()
            st.warning(recipe_error(exc, language))
