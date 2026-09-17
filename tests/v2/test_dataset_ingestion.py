from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq
import pytest

import autoanalyst.data.ingest as ingest_module
from autoanalyst.application.datasets import DatasetService
from autoanalyst.application.projects import ProjectService
from autoanalyst.data.ingest import CSVIngestor
from autoanalyst.data.schema import INTERNAL_ROW_ID, INTERNAL_ROW_ORDER
from autoanalyst.domain.errors import SchemaError
from autoanalyst.storage.artifacts import ArtifactStore
from autoanalyst.storage.sqlite import SQLiteCatalog


def _workspace(tmp_path: Path):
    catalog = SQLiteCatalog(tmp_path)
    project = ProjectService(catalog).create("Imports")
    datasets = DatasetService(catalog)
    dataset = datasets.create(project.project_id, "Orders")
    ingestor = CSVIngestor(catalog, ArtifactStore(catalog.paths.root))
    return catalog, project, datasets, dataset, ingestor


def test_csv_import_preserves_raw_and_creates_canonical_parquet(tmp_path: Path) -> None:
    catalog, project, datasets, dataset, ingestor = _workspace(tmp_path)
    content = b"customer,amount\nAda,10\nLinus,20\n"

    result = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=content,
        original_name="../../orders.csv",
    )

    raw_path = ingestor.artifact_store.resolve_relative_path(result.raw_artifact.relative_path)
    parquet_path = ingestor.artifact_store.resolve_relative_path(result.table_artifact.relative_path)
    assert raw_path.read_bytes() == content
    assert result.source.raw_sha256 == hashlib.sha256(content).hexdigest()
    assert result.raw_artifact.relative_path != result.source.original_name
    assert parquet_path.suffix == ".parquet"
    table = pq.read_table(parquet_path)
    assert table.num_rows == 2
    assert INTERNAL_ROW_ID in table.column_names
    assert INTERNAL_ROW_ORDER in table.column_names
    assert len(set(table[INTERNAL_ROW_ID].to_pylist())) == 2
    assert table[INTERNAL_ROW_ORDER].to_pylist() == [0, 1]
    assert ingestor.artifact_store.verify(result.raw_artifact)
    assert ingestor.artifact_store.verify(result.table_artifact)
    assert datasets.current_version(dataset.dataset_id) == result.version
    assert catalog.get_source(result.source.source_id) == result.source


def test_column_ids_are_unique_and_reserved_display_names_are_preserved(tmp_path: Path) -> None:
    catalog, project, _, dataset, ingestor = _workspace(tmp_path)
    result = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"row_id,value\nuser-1,4\n",
        original_name="reserved.csv",
    )

    assert len({column.column_id for column in result.columns}) == len(result.columns)
    user_row_id = next(column for column in result.columns if not column.is_system and column.display_name == "row_id")
    assert user_row_id.physical_name != INTERNAL_ROW_ID
    assert "row_id" not in user_row_id.column_id
    table = pq.read_table(ingestor.artifact_store.resolve_relative_path(result.table_artifact.relative_path))
    assert table[user_row_id.physical_name].to_pylist() == ["user-1"]
    assert len(catalog.list_columns(result.version.version_id)) == 4


def test_duplicate_headers_are_rejected_without_publishing_head(tmp_path: Path) -> None:
    catalog, project, datasets, dataset, ingestor = _workspace(tmp_path)

    with pytest.raises(SchemaError) as duplicate:
        ingestor.import_csv(
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            source=b"name,name\na,b\n",
            original_name="duplicate.csv",
        )

    assert duplicate.value.context["reason"] == "duplicate_column_names"
    assert datasets.get(dataset.dataset_id).head_version_id is None
    assert datasets.version_history(dataset.dataset_id) == ()
    assert catalog.list_head_events(dataset.dataset_id) == ()


def test_failed_metadata_transaction_never_publishes_version_or_head(tmp_path: Path, monkeypatch) -> None:
    catalog, project, datasets, dataset, ingestor = _workspace(tmp_path)
    real_build_columns = ingest_module.build_columns

    def columns_with_duplicate_identity(*args, **kwargs):
        columns = list(real_build_columns(*args, **kwargs))
        columns[1] = replace(columns[1], column_id=columns[0].column_id)
        return tuple(columns)

    monkeypatch.setattr(ingest_module, "build_columns", columns_with_duplicate_identity)
    with pytest.raises(sqlite3.IntegrityError):
        ingestor.import_csv(
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            source=b"value\n1\n",
            original_name="failure.csv",
        )

    assert datasets.get(dataset.dataset_id).head_version_id is None
    assert datasets.version_history(dataset.dataset_id) == ()
    with catalog.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dataset_sources").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dataset_columns").fetchone()[0] == 0


def test_same_csv_and_parse_contract_have_deterministic_content_fingerprint(tmp_path: Path) -> None:
    _, project, datasets, first_dataset, ingestor = _workspace(tmp_path)
    second_dataset = datasets.create(project.project_id, "Orders copy")
    content = b"a,b\n1,x\n2,y\n"

    first = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=first_dataset.dataset_id,
        source=content,
        original_name="first.csv",
    )
    second = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=second_dataset.dataset_id,
        source=content,
        original_name="second.csv",
    )

    assert first.version.content_fingerprint == second.version.content_fingerprint
    assert first.version.version_id != second.version.version_id
    assert first.version.schema_hash == second.version.schema_hash
    first_user_ids = {column.column_id for column in first.columns if not column.is_system}
    second_user_ids = {column.column_id for column in second.columns if not column.is_system}
    assert first_user_ids.isdisjoint(second_user_ids)
    first_rows = pq.read_table(
        ingestor.artifact_store.resolve_relative_path(first.table_artifact.relative_path),
        columns=[INTERNAL_ROW_ID, INTERNAL_ROW_ORDER],
    )
    second_rows = pq.read_table(
        ingestor.artifact_store.resolve_relative_path(second.table_artifact.relative_path),
        columns=[INTERNAL_ROW_ID, INTERNAL_ROW_ORDER],
    )
    assert first_rows.to_pylist() == second_rows.to_pylist()
    with pytest.raises(FrozenInstanceError):
        first.version.row_count = 9  # type: ignore[misc]
