"""Explicit join keys, cardinality declaration and output caps."""

import streamlit as st


def render_join_form(runtime, project_id, base_version, columns, t):
    with runtime.catalog.connection() as connection:
        rows = connection.execute(
            "SELECT v.version_id,d.name FROM dataset_versions v JOIN datasets d ON d.dataset_id=v.dataset_id WHERE d.project_id=? AND v.version_id<>? ORDER BY v.created_at,v.version_id",
            (project_id, base_version),
        ).fetchall()
    versions = {r["version_id"]: f"{r['name']} · {r['version_id'][:8]}" for r in rows}
    if not versions:
        st.info(t["join_no_source"])
        return {}, False
    right_id = st.selectbox(
        t["join_right_version"],
        tuple(versions),
        format_func=versions.__getitem__,
        key="join_right_version",
    )
    right = [c for c in runtime.catalog.list_columns(right_id) if not c["is_system"]]
    left_names = {str(c["column_id"]): str(c["display_name"]) for c in columns}
    right_names = {str(c["column_id"]): str(c["display_name"]) for c in right}
    left_keys = st.multiselect(
        t["join_left_keys"],
        tuple(left_names),
        format_func=left_names.__getitem__,
        max_selections=5,
        key="join_left_keys",
    )
    right_keys = st.multiselect(
        t["join_right_keys"],
        tuple(right_names),
        format_func=right_names.__getitem__,
        max_selections=5,
        key=f"join_right_keys_{right_id}",
    )
    how = st.selectbox(t["join_how"], ("inner", "left"), key="join_how")
    relationship = st.selectbox(
        t["join_relationship"],
        ("one_to_one", "many_to_one", "one_to_many", "many_to_many"),
        key="join_relationship",
    )
    cap = st.number_input(
        t["join_row_cap"],
        min_value=1,
        max_value=1_000_000,
        value=100_000,
        step=1000,
        key="join_row_cap",
    )
    expansion = st.number_input(
        t["join_expansion"],
        min_value=1.0,
        max_value=10.0,
        value=2.0,
        step=0.5,
        key="join_expansion",
    )
    st.caption(t["join_scope"])
    return {
        "right_version_id": right_id,
        "left_key_ids": left_keys,
        "right_key_ids": right_keys,
        "how": how,
        "relationship": relationship,
        "max_output_rows": cap,
        "max_expansion": expansion,
    }, bool(left_keys) and len(left_keys) == len(right_keys)
