"""CSV ingestion into immutable raw and canonical Parquet artifacts."""

from __future__ import annotations

import csv
import hashlib
import math
import os
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd

from ..application.datasets import DatasetService
from ..domain.codec import canonical_json, fingerprint, utc_now
from ..domain.datasets import DatasetSource, DatasetVersion, DatasetVersionKind, SourceFormat
from ..domain.errors import DataError, DependencyError, SchemaError
from ..domain.results import Artifact
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .limits import enforce_source_size
from .schema import DatasetColumn, build_columns, schema_fingerprint, validate_display_names


@dataclass(frozen=True, slots=True)
class IngestionResult:
    source: DatasetSource
    version: DatasetVersion
    raw_artifact: Artifact
    table_artifact: Artifact
    columns: tuple[DatasetColumn, ...]


class CSVIngestor:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.datasets = DatasetService(catalog)

    def import_csv(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source: bytes | bytearray | str | Path,
        original_name: str | None = None,
        delimiter: str = ",",
        encoding: str = "utf-8",
    ) -> IngestionResult:
        if len(delimiter) != 1:
            raise DataError({"reason": "invalid_csv_delimiter"})
        dataset = self.datasets.get(dataset_id)
        if dataset.project_id != project_id:
            raise SchemaError({"reason": "dataset_project_mismatch"})
        raw_bytes, inferred_name = _read_source(source)
        enforce_source_size(len(raw_bytes), source_format="csv")
        source_name = original_name if original_name is not None else inferred_name
        if not source_name:
            source_name = "upload.csv"

        import_id = str(uuid4())
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        raw_staged = self.artifact_store.stage_bytes(
            raw_bytes,
            project_id=project_id,
            owner_run_id=import_id,
            kind="raw_source",
            media_type="text/csv",
        )
        raw_artifact = self.artifact_store.finalize(raw_staged)

        headers = _read_headers(raw_bytes, delimiter=delimiter, encoding=encoding)
        dataframe = _read_dataframe(raw_bytes, delimiter=delimiter, encoding=encoding)
        if len(dataframe.columns) != len(headers):
            raise SchemaError({"reason": "parsed_column_count_mismatch"})
        dataframe.columns = list(headers)
        if dataframe.empty:
            raise DataError({"reason": "csv_has_no_rows"})

        parse_contract = {
            "format": "csv",
            "delimiter": delimiter,
            "encoding": encoding.lower(),
            "header": True,
        }
        parse_contract_json = canonical_json(parse_contract)
        version_id = str(uuid4())
        columns = build_columns(
            version_id, list(headers), [str(dtype) for dtype in dataframe.dtypes]
        )
        user_columns = tuple(column for column in columns if not column.is_system)
        canonical = dataframe.rename(
            columns={column.display_name: column.physical_name for column in user_columns}
        ).copy()
        canonical.insert(
            0,
            columns[0].physical_name,
            [
                _row_id(raw_sha256, parse_contract_json, index)
                for index in range(len(canonical))
            ],
        )
        canonical.insert(1, columns[1].physical_name, range(len(canonical)))
        canonical = canonical[[column.physical_name for column in columns]]

        content_fingerprint = _content_fingerprint(dataframe, headers, parse_contract)
        table_artifact = self._write_parquet(canonical, project_id, import_id)
        imported_at = utc_now()
        dataset_source = DatasetSource(
            source_id=str(uuid4()),
            project_id=project_id,
            original_name=source_name,
            format=SourceFormat.CSV,
            raw_artifact_id=raw_artifact.artifact_id,
            raw_sha256=raw_sha256,
            byte_size=len(raw_bytes),
            imported_at=imported_at,
        )
        version = DatasetVersion(
            version_id=version_id,
            dataset_id=dataset_id,
            kind=DatasetVersionKind.IMPORTED,
            created_at=imported_at,
            created_by_run_id=import_id,
            table_artifact_id=table_artifact.artifact_id,
            row_count=len(dataframe),
            column_count=len(headers),
            schema_hash=schema_fingerprint(columns),
            content_fingerprint=content_fingerprint,
            parse_contract=parse_contract_json,
        )
        self.catalog.publish_dataset_import(
            artifacts=(raw_artifact, table_artifact),
            source=dataset_source,
            version=version,
            columns=columns,
        )
        return IngestionResult(dataset_source, version, raw_artifact, table_artifact, columns)

    def _write_parquet(
        self,
        dataframe: pd.DataFrame,
        project_id: str,
        import_id: str,
    ) -> Artifact:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise DependencyError({"dependency": "pyarrow", "operation": "csv_ingestion"}) from exc
        staged = self.artifact_store.create_staging(
            project_id=project_id,
            owner_run_id=import_id,
            kind="canonical_table",
            media_type="application/vnd.apache.parquet",
            format_version="1",
        )
        try:
            table = pa.Table.from_pandas(dataframe, preserve_index=False)
            pq.write_table(table, staged.staging_path, compression="zstd")
            with staged.staging_path.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            return self.artifact_store.finalize(staged)
        except Exception:
            staged.staging_path.unlink(missing_ok=True)
            raise


def _row_id(raw_sha256: str, parse_contract_json: str, row_order: int) -> str:
    identity = f"autoanalyst:{raw_sha256}:{parse_contract_json}:{row_order}"
    return str(uuid5(NAMESPACE_URL, identity))


def _read_source(source: bytes | bytearray | str | Path) -> tuple[bytes, str | None]:
    if isinstance(source, bytes):
        return source, None
    if isinstance(source, bytearray):
        return bytes(source), None
    path = Path(source)
    try:
        return path.read_bytes(), path.name
    except OSError as exc:
        raise DataError({"reason": "source_read_failed"}) from exc


def _read_headers(raw_bytes: bytes, *, delimiter: str, encoding: str) -> tuple[str, ...]:
    try:
        text = raw_bytes.decode(encoding)
        reader = csv.reader(StringIO(text), delimiter=delimiter)
        header = next(reader)
    except (UnicodeDecodeError, LookupError, csv.Error, StopIteration) as exc:
        raise DataError({"reason": "csv_header_parse_failed"}) from exc
    return validate_display_names(header)


def _read_dataframe(raw_bytes: bytes, *, delimiter: str, encoding: str) -> pd.DataFrame:
    try:
        return pd.read_csv(BytesIO(raw_bytes), sep=delimiter, encoding=encoding)
    except (
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
        UnicodeDecodeError,
        LookupError,
    ) as exc:
        raise DataError(
            {"reason": "csv_parse_failed", "exception_type": type(exc).__name__}
        ) from exc


def _content_fingerprint(
    dataframe: pd.DataFrame,
    headers: tuple[str, ...],
    parse_contract: dict[str, object],
) -> str:
    rows = [
        [_canonical_scalar(value) for value in row]
        for row in dataframe.itertuples(index=False, name=None)
    ]
    descriptor = {
        "parse_contract": parse_contract,
        "columns": [
            {
                "display_name": name,
                "physical_type": str(dataframe.dtypes.iloc[index]),
                "ordinal": index,
            }
            for index, name in enumerate(headers)
        ],
        "rows": rows,
    }
    return fingerprint(descriptor)


def _canonical_scalar(value: Any) -> dict[str, object]:
    try:
        if pd.isna(value):
            return {"type": "null", "value": None}
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, float):
        if math.isinf(value):
            return {
                "type": "float",
                "state": "positive_infinity" if value > 0 else "negative_infinity",
            }
        return {"type": "float", "value": value}
    if isinstance(value, (datetime, date)):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    return {"type": "string", "value": str(value)}
