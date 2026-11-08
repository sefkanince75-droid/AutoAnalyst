"""Session recipe controls and provisional column metadata for step forms."""

from dataclasses import asdict
from datetime import datetime

import streamlit as st

from ..data.schema import INTERNAL_NAMES, DatasetColumn
from ..domain.codec import decode_typed_label
from ..domain.errors import SchemaError
from ..localization import I18N, label
from ..preparation.operations import validate_parameters
from .typed_inputs import input_kind

MAX_DRAFT_STEPS = 20


def projected_columns(columns, steps, catalog=None):
    projected = [dict(column) for column in columns]
    for position, step in enumerate(steps):
        operation, params = step["operation"], step["parameters"]
        validate_parameters(operation, params)
        ids = {column["column_id"] for column in projected}
        selected = [params["column_id"]] if "column_id" in params else params.get("column_ids", ())
        if not set(selected).issubset(ids):
            raise SchemaError({"reason": "column_not_found", "step_position": position})
        if operation == "drop_columns":
            projected = [column for column in projected if column["column_id"] not in selected]
            if not projected:
                raise SchemaError(
                    {"reason": "at_least_one_user_column_required", "step_position": position}
                )
        elif operation == "rename_column":
            name = str(params["new_name"]).strip()
            if name in INTERNAL_NAMES or any(
                column["display_name"] == name and column["column_id"] != selected[0]
                for column in projected
            ):
                raise SchemaError({"reason": "duplicate_column_names", "step_position": position})
            for column in projected:
                if column["column_id"] == selected[0]:
                    column["display_name"] = name
        elif operation == "cast_column":
            physical, semantic = {
                "numeric": ("DOUBLE", "numeric"),
                "categorical": ("VARCHAR", "categorical"),
                "string": ("VARCHAR", "categorical"),
                "boolean": ("BOOLEAN", "boolean"),
                "datetime": ("TIMESTAMP", "datetime"),
            }[params["target_type"]]
            for column in projected:
                if column["column_id"] == selected[0]:
                    column.update(physical_type=physical, semantic_hint=semantic)
        elif operation == "fill_missing" and params["strategy"] in {"mean", "median"}:
            for column in projected:
                if column["column_id"] in selected and input_kind(column) in {"integer", "numeric"}:
                    column.update(physical_type="DOUBLE", semantic_hint="numeric")
        elif operation == "join_rows" and catalog is not None:
            from ..preparation.joins import join_columns

            right = as_columns([dict(c) for c in catalog.list_columns(params["right_version_id"])])
            projected = [
                asdict(c)
                for c in join_columns(as_columns(projected), right, params["right_key_ids"])
                if not c.is_system
            ]
        elif operation == "append_rows" and catalog is not None:
            for version_id in params["input_version_ids"]:
                incoming = {
                    column["column_id"]: column for column in catalog.list_columns(version_id)
                }
                for column in projected:
                    source = incoming.get(column["column_id"])
                    if (
                        source
                        and input_kind(column) in {"integer", "numeric"}
                        and input_kind(source) == "numeric"
                    ):
                        column.update(physical_type="DOUBLE", semantic_hint="numeric")
    return projected


def as_columns(columns):
    return tuple(
        DatasetColumn(**{key: column[key] for key in DatasetColumn.__dataclass_fields__})
        for column in columns
    )


def clear_preview():
    st.session_state.pop("preview_id", None)
    st.session_state.pop("preview_context", None)
    st.session_state.pop("prep_sample_cache", None)


def clear_draft():
    for key in ("prep_draft", "prep_draft_context", "prep_edit_uid", "prep_load_edit"):
        st.session_state.pop(key, None)
    clear_preview()


def recipe_error(exc, language):
    from ..localization import error_text

    text = error_text(exc, language)
    position = getattr(exc, "context", {}).get("step_position")
    if type(position) is int:
        return I18N[language]["step_error"].format(step=position + 1, message=text)
    return text


def draft_controls(runtime, columns, language):
    t = I18N[language]
    draft = st.session_state.prep_draft
    st.caption(t["recipe_draft_hint"].format(limit=MAX_DRAFT_STEPS))
    for index, step in enumerate(draft):
        names = {column["column_id"]: column["display_name"] for column in columns}
        try:
            names.update(
                {
                    column["column_id"]: column["display_name"]
                    for column in projected_columns(columns, draft[:index])
                }
            )
        except SchemaError:
            pass
        with st.expander(
            t["recipe_step"].format(step=index + 1, operation=label(step["operation"], language))
        ):
            for key, value in step["parameters"].items():
                if key == "column_id":
                    value = names.get(value, t["unavailable_column"])
                elif key == "column_ids":
                    value = [names.get(item, t["unavailable_column"]) for item in value]
                elif key == "mapping":
                    value = [
                        (decode_typed_label(pair["from"]), decode_typed_label(pair["to"]))
                        for pair in value
                    ]
                elif key == "input_version_ids":
                    value = [
                        f"{runtime.catalog.get_dataset(runtime.catalog.get_version(item).dataset_id).name} · {item[:8]}"
                        for item in value
                    ]
                elif key == "value" and isinstance(value, dict):
                    value = decode_typed_label(value)
                elif key in {
                    "operator",
                    "target_type",
                    "failure_policy",
                    "strategy",
                    "keep",
                } and isinstance(value, str):
                    value = label(value, language)
                st.text(f"{label(key, language)}: {value}")
            up, down, edit, remove = st.columns(4)
            action = None
            if up.button(t["step_up"], key="step_up_" + step["uid"], disabled=index == 0):
                draft[index - 1], draft[index] = draft[index], draft[index - 1]
                action = "move"
            if down.button(
                t["step_down"], key="step_down_" + step["uid"], disabled=index == len(draft) - 1
            ):
                draft[index + 1], draft[index] = draft[index], draft[index + 1]
                action = "move"
            if edit.button(t["edit_step"], key="step_edit_" + step["uid"]):
                st.session_state.prep_edit_uid = step["uid"]
                st.session_state.prep_load_edit = True
                action = "edit"
            if remove.button(t["remove_step"], key="step_remove_" + step["uid"]):
                draft.pop(index)
                action = "remove"
            if action:
                if action != "edit":
                    st.session_state.pop("prep_edit_uid", None)
                clear_preview()
                st.rerun()
    if draft and st.button(t["clear_recipe"], key="clear_recipe"):
        clear_draft()
        st.rerun()
    return draft


def seed_form(step, columns):
    """Set widget values before rendering them; column identity survives renames."""
    params, operation = step["parameters"], step["operation"]
    names = {column["column_id"]: column["display_name"] for column in columns}
    st.session_state.prep_operation = operation
    single_keys = {
        "filter_rows": "prep_filter_col",
        "rename_column": "prep_rename_col",
        "cast_column": "prep_cast_col",
        "replace_values": "prep_replace_col",
    }
    if operation in single_keys:
        st.session_state[single_keys[operation]] = names.get(params["column_id"])
    if operation == "rename_column":
        st.session_state.prep_new_name = params["new_name"]
    elif operation == "cast_column":
        st.session_state.prep_cast_type = params["target_type"]
        st.session_state.prep_cast_failure = params["failure_policy"]
    elif operation == "filter_rows":
        st.session_state[f"prep_operator_{params['column_id']}"] = params["operator"]
        if "value" in params:
            seed_value(
                next(c for c in columns if c["column_id"] == params["column_id"]),
                "prep_filter_value",
                params["value"],
            )
    elif operation == "fill_missing":
        st.session_state.prep_fill_cols = [names[item] for item in params["column_ids"]]
        st.session_state.prep_fill_strategy = params["strategy"]
        if params["strategy"] == "constant":
            seed_value(
                next(c for c in columns if c["column_id"] == params["column_ids"][0]),
                "prep_constant",
                decode_typed_label(params["value"]),
            )
    elif operation == "append_rows":
        st.session_state.prep_append_inputs = list(params["input_version_ids"])
    elif operation == "join_rows":
        st.session_state.join_right_version = params["right_version_id"]
        st.session_state.join_left_keys = list(params["left_key_ids"])
        st.session_state[f"join_right_keys_{params['right_version_id']}"] = list(
            params["right_key_ids"]
        )
        for key in ("how", "relationship"):
            st.session_state[f"join_{key}"] = params[key]
        st.session_state.join_row_cap = params["max_output_rows"]
        st.session_state.join_expansion = float(params["max_expansion"])
    elif operation == "replace_values":
        st.session_state.prep_replace_count = len(params["mapping"])
        column = next(c for c in columns if c["column_id"] == params["column_id"])
        for index, pair in enumerate(params["mapping"]):
            for side in ("from", "to"):
                value = decode_typed_label(pair[side])
                st.session_state[f"replace_{side}_null_{index}"] = value is None
                if value is not None:
                    seed_value(column, f"replace_{side}_{index}", value)
    elif "column_ids" in params:
        st.session_state[f"prep_{operation}"] = [names[item] for item in params["column_ids"]]


def seed_value(column, prefix, value):
    kind = input_kind(column)
    key = f"{prefix}_{column['column_id']}_{kind}"
    if kind in {"date", "datetime"}:
        parsed = datetime.fromisoformat(str(value))
        st.session_state[key] = parsed.date()
        if kind == "datetime":
            st.session_state[key + "_time"] = parsed.time().replace(tzinfo=None)
    elif kind == "integer":
        exact = abs(int(value)) > 2**53 - 1
        st.session_state[key + "_exact"] = exact
        st.session_state[key + "_text" if exact else key] = str(value) if exact else int(value)
    else:
        st.session_state[key] = float(value) if kind == "numeric" else value
