"""Explicit-sheet XLSX ingestion into immutable canonical Parquet data."""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pandas as pd

from ..application.datasets import DatasetService
from ..domain.codec import canonical_json, utc_now
from ..domain.datasets import DatasetSource, DatasetVersion, DatasetVersionKind, SourceFormat
from ..domain.errors import DataError, DependencyError, ResourceError, SchemaError
from ..domain.results import Artifact
from ..execution.data_control import JOB_ID, checkpoint
from ..storage.activity import artifact_activity
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog
from .ingest import (
    IngestionResult,
    _content_fingerprint,
    _import_publication,
    _read_source,
    _write_raw,
)
from .limits import MIB, enforce_free_disk, enforce_shape, enforce_source_size, enforce_xlsx_archive
from .preview import IMPORT_PREVIEW_ROWS, ImportPreview
from .schema import build_columns, schema_fingerprint, validate_display_names


class XLSXIngestor:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store
        self.datasets = DatasetService(catalog)

    def sheet_names(self, source: bytes | bytearray | str | Path) -> tuple[str, ...]:
        raw, _ = _read_source(source, source_format="xlsx")
        enforce_source_size(len(raw), source_format="xlsx")
        enforce_xlsx_archive(raw)
        try:
            with pd.ExcelFile(BytesIO(raw), engine="openpyxl") as book:
                return tuple(book.sheet_names)
        except Exception as exc:
            raise DataError(
                {"reason": "xlsx_open_failed", "exception_type": type(exc).__name__}
            ) from exc

    def preview_xlsx(
        self, source: bytes | bytearray | str | Path, *, sheet_name: str
    ) -> ImportPreview:
        raw, _ = _read_source(source, source_format="xlsx")
        enforce_source_size(len(raw), source_format="xlsx")
        frame = _read_worksheet(raw, sheet_name=sheet_name, row_limit=IMPORT_PREVIEW_ROWS + 1)
        return ImportPreview.from_frame(frame)

    @artifact_activity
    def import_xlsx(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source: bytes | bytearray | str | Path,
        sheet_name: str,
        original_name: str | None = None,
    ) -> IngestionResult:
        dataset = self.datasets.get(dataset_id)
        if dataset.project_id != project_id:
            raise SchemaError({"reason": "dataset_project_mismatch"})
        raw_bytes, inferred_name = _read_source(source, source_format="xlsx")
        enforce_source_size(len(raw_bytes), source_format="xlsx")
        source_name = original_name or inferred_name or "upload.xlsx"
        enforce_free_disk(self.catalog.paths.root, len(raw_bytes) * 3 + 64 * MIB)
        checkpoint()
        dataframe = _read_worksheet(raw_bytes, sheet_name=sheet_name)
        headers = tuple(dataframe.columns)

        import_id = JOB_ID.get() or str(uuid4())
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        parse_contract = {"format": "xlsx", "sheet_name": sheet_name, "header": True}
        parse_contract_json = canonical_json(parse_contract)
        version_id = str(uuid4())
        columns = build_columns(
            version_id,
            list(headers),
            [str(dtype) for dtype in dataframe.dtypes],
        )
        user_columns = tuple(column for column in columns if not column.is_system)
        canonical = dataframe.rename(
            columns={column.display_name: column.physical_name for column in user_columns}
        ).copy()
        canonical.insert(
            0,
            columns[0].physical_name,
            [
                str(
                    uuid5(
                        NAMESPACE_URL,
                        f"autoanalyst:{raw_sha256}:{parse_contract_json}:{index}",
                    )
                )
                for index in range(len(canonical))
            ],
        )
        canonical.insert(1, columns[1].physical_name, range(len(canonical)))
        canonical = canonical[[column.physical_name for column in columns]]
        with _import_publication(self.catalog, self.artifact_store) as artifacts:
            raw = _write_raw(
                self.artifact_store,
                raw_bytes,
                project_id,
                import_id,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            artifacts.append(raw)
            table = self._write_parquet(canonical, project_id, import_id)
            artifacts.append(table)
            created = utc_now()
            source_record = DatasetSource(
                str(uuid4()),
                project_id,
                source_name,
                SourceFormat.XLSX,
                raw.artifact_id,
                raw_sha256,
                len(raw_bytes),
                created,
            )
            version = DatasetVersion(
                version_id,
                dataset_id,
                DatasetVersionKind.IMPORTED,
                created,
                import_id,
                table.artifact_id,
                len(dataframe),
                len(headers),
                schema_fingerprint(columns),
                _content_fingerprint(dataframe, headers, parse_contract),
                parse_contract=parse_contract_json,
            )
            checkpoint(publication=True)
            self.catalog.publish_dataset_import(
                artifacts=(raw, table),
                source=source_record,
                version=version,
                columns=columns,
                reason="xlsx_import",
            )
        return IngestionResult(source_record, version, raw, table, columns)

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
            raise DependencyError({"dependency": "pyarrow", "operation": "xlsx_ingestion"}) from exc
        staged = self.artifact_store.create_staging(
            project_id=project_id,
            owner_run_id=import_id,
            kind="canonical_table",
            media_type="application/vnd.apache.parquet",
            format_version="1",
        )
        try:
            pq.write_table(
                pa.Table.from_pandas(dataframe, preserve_index=False),
                staged.staging_path,
                compression="zstd",
            )
            with staged.staging_path.open("r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            return self.artifact_store.finalize(staged)
        except Exception:
            staged.staging_path.unlink(missing_ok=True)
            raise


def _read_worksheet(
    raw_bytes: bytes, *, sheet_name: str, row_limit: int | None = None
) -> pd.DataFrame:
    if not sheet_name:
        raise SchemaError({"reason": "xlsx_sheet_required"})
    enforce_xlsx_archive(raw_bytes)
    try:
        with pd.ExcelFile(BytesIO(raw_bytes), engine="openpyxl") as book:
            if sheet_name not in book.sheet_names:
                raise SchemaError({"reason": "xlsx_sheet_not_found", "sheet_name": sheet_name})
            sheet = book.book[sheet_name]
            enforce_shape(
                min((sheet.max_row or 1) - 1, row_limit)
                if row_limit is not None
                else (sheet.max_row or 1) - 1,
                sheet.max_column or 0,
            )
            frame = pd.read_excel(book, sheet_name=sheet_name, nrows=row_limit)
    except (SchemaError, ResourceError):
        raise
    except Exception as exc:
        raise DataError(
            {"reason": "xlsx_parse_failed", "exception_type": type(exc).__name__}
        ) from exc
    headers = validate_display_names([str(value) for value in frame.columns])
    if frame.empty:
        raise DataError({"reason": "xlsx_has_no_rows"})
    frame.columns = list(headers)
    enforce_shape(len(frame), len(headers))
    return frame
