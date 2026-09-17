from __future__ import annotations

import pytest

from autoanalyst.data import TableAccess, TableFilter
from autoanalyst.domain.errors import SchemaError


def test_duckdb_reads_only_catalog_owned_parquet_and_has_no_user_sql_api(phase3_workspace) -> None:
    access = TableAccess(phase3_workspace.catalog, phase3_workspace.store)
    version_id = phase3_workspace.imported.version.version_id

    assert access.row_count(version_id) == 5
    assert len(access.inspect_schema(version_id)) == 6
    assert not hasattr(access, "execute_sql")
    assert not hasattr(access, "query")


def test_display_name_sql_injection_is_never_used_as_identifier(tmp_path) -> None:
    from autoanalyst.application import DatasetService, ProjectService
    from autoanalyst.data import CSVIngestor
    from autoanalyst.storage import ArtifactStore, SQLiteCatalog

    catalog = SQLiteCatalog(tmp_path)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Injection")
    dataset = DatasetService(catalog).create(project.project_id, "Input")
    imported = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b'evil"; DROP TABLE projects; --\nvalue\n',
        original_name="injection.csv",
    )
    column = next(
        row for row in catalog.list_columns(imported.version.version_id) if not row["is_system"]
    )

    result = TableAccess(catalog, store).selected_columns(
        imported.version.version_id, (column["column_id"],)
    )

    assert result.num_rows == 1
    assert ProjectService(catalog).get(project.project_id) == project


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [
        ("eq", 2.0, 1),
        ("ne", 2.0, 3),
        ("gt", 2.0, 2),
        ("gte", 2.0, 3),
        ("lt", 2.0, 1),
        ("lte", 2.0, 2),
    ],
)
def test_comparison_filter_operators(phase3_workspace, operator, value, expected) -> None:
    access = TableAccess(phase3_workspace.catalog, phase3_workspace.store)
    num = phase3_workspace.column("num")
    table = access.filtered_projection(
        phase3_workspace.imported.version.version_id,
        (num["column_id"],),
        (TableFilter(num["column_id"], operator, value),),
    )
    assert table.num_rows == expected


def test_literal_contains_and_null_filter_semantics(phase3_workspace) -> None:
    access = TableAccess(phase3_workspace.catalog, phase3_workspace.store)
    text = phase3_workspace.column("text")
    version_id = phase3_workspace.imported.version.version_id

    contains = access.filtered_projection(
        version_id,
        (text["column_id"],),
        (TableFilter(text["column_id"], "contains_literal", "x"),),
    )
    is_null = access.filtered_projection(
        version_id,
        (text["column_id"],),
        (TableFilter(text["column_id"], "is_null"),),
    )
    eq_null = access.filtered_projection(
        version_id,
        (text["column_id"],),
        (TableFilter(text["column_id"], "eq", None),),
    )

    assert contains.num_rows == 2
    assert is_null.num_rows == eq_null.num_rows == 1
    with pytest.raises(SchemaError):
        access.filtered_projection(
            version_id,
            (text["column_id"],),
            (TableFilter(text["column_id"], "gt", None),),
        )


def test_aggregate_duplicates_and_ordered_export(phase3_workspace) -> None:
    access = TableAccess(phase3_workspace.catalog, phase3_workspace.store)
    group = phase3_workspace.column("group")
    num = phase3_workspace.column("num")
    version_id = phase3_workspace.imported.version.version_id

    assert access.aggregate_value(version_id, num["column_id"], "count") == 4
    assert access.duplicate_rows(version_id, (group["column_id"],)).num_rows == 4
    exported = access.ordered_export(version_id)
    assert exported.column("__aa_internal_row_order__").to_pylist() == [0, 1, 2, 3, 4]
