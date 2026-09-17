from __future__ import annotations

from datetime import datetime, timezone
import math
from uuid import UUID

import pytest

from autoanalyst.domain.codec import (
    canonical_json,
    decode_typed_label,
    encode_typed_label,
    fingerprint,
    from_json,
)
from autoanalyst.domain.datasets import Project


PROJECT_ID = "2da3d7fe-48ce-4b6b-8657-dd7cb6f56fb6"


def test_domain_object_round_trip_serialization() -> None:
    project = Project(
        project_id=PROJECT_ID,
        name="Example",
        created_at=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, 3, 5, tzinfo=timezone.utc),
        default_language="tr",
        revision=2,
    )

    encoded = canonical_json(project)
    decoded = from_json(encoded)

    assert decoded == project
    assert '"schema_version":"2.0"' in encoded


def test_fingerprint_is_independent_of_dictionary_order() -> None:
    first = {"b": [2, 1], "a": {"y": True, "x": "value"}}
    second = {"a": {"x": "value", "y": True}, "b": [2, 1]}

    assert canonical_json(first) == canonical_json(second)
    assert fingerprint(first) == fingerprint(second)
    assert len(fingerprint(first)) == 64


def test_typed_category_labels_keep_string_integer_and_boolean_distinct() -> None:
    encoded = [encode_typed_label(value) for value in ("1", 1, True)]

    assert len({canonical_json(item) for item in encoded}) == 3
    assert [decode_typed_label(item) for item in encoded] == ["1", 1, True]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_values_never_enter_canonical_json(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": value})
    with pytest.raises(ValueError):
        encode_typed_label(value)
