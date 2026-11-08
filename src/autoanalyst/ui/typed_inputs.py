"""Type-preserving widgets for closed preparation parameters."""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime

import streamlit as st

from ..localization import I18N, label


def input_kind(column) -> str:
    physical = str(column["physical_type"]).lower()
    semantic = str(column.get("semantic_hint") or "")
    if semantic == "boolean" or "bool" in physical:
        return "boolean"
    if semantic == "datetime" or "date" in physical or "timestamp" in physical:
        return "datetime" if "time" in physical else "date"
    if "int" in physical:
        return "integer"
    if semantic == "numeric" or any(token in physical for token in ("float", "double", "decimal")):
        return "numeric"
    return "string"


def typed_input(column, title: str, key: str, language: str):
    kind = input_kind(column)
    field_key = f"{key}_{column['column_id']}_{kind}"
    t = I18N[language]
    if kind == "boolean":
        return st.selectbox(
            title, (True, False), format_func=lambda value: label(value, language), key=field_key
        )
    if kind == "integer":
        exact = st.checkbox(
            t["exact_integer"], key=field_key + "_exact", help=t["integer_precision"]
        )
        if exact:
            raw = st.text_input(title, value="0", key=field_key + "_text").strip()
            if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
                st.error(t["invalid_integer"])
                return None
            value = int(raw)
            physical = str(column["physical_type"]).lower()
            match = re.search(r"(?:u?int)(8|16|32|64)", physical)
            bits = int(match.group(1)) if match else 64
            lower, upper = (
                (0, 2**bits - 1)
                if "uint" in physical
                else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
            )
            if not lower <= value <= upper:
                st.error(t["integer_range"].format(lower=lower, upper=upper))
                return None
            return value
        return st.number_input(
            title, min_value=-(2**53 - 1), max_value=2**53 - 1, value=0, step=1, key=field_key
        )
    if kind == "numeric":
        value = st.number_input(title, value=0.0, step=0.1, key=field_key)
        if not math.isfinite(value):
            st.error(t["finite_required"])
            return None
        return value
    if kind in {"datetime", "date"}:
        day = st.date_input(
            title,
            value=date(2000, 1, 1),
            min_value=date(1, 1, 1),
            max_value=date(9999, 12, 31),
            key=field_key,
        )
        if kind == "date":
            return day.isoformat()
        time = st.time_input(t["time_value"], value=datetime.min.time(), key=field_key + "_time")
        value = datetime.combine(day, time)
        physical = str(column["physical_type"]).lower()
        if "tz" in physical or "utc" in physical:
            st.caption(t["utc_time_hint"])
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return st.text_input(title, key=field_key)


def filter_operators(column) -> tuple[str, ...]:
    kind = input_kind(column)
    operators = ("eq", "ne")
    if kind in {"integer", "numeric", "datetime", "date"}:
        operators += ("gt", "gte", "lt", "lte")
    elif kind == "string":
        operators += ("contains_literal",)
    return operators + ("is_null", "is_not_null")
