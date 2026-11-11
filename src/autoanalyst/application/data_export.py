"""Immutable-version CSV/Parquet export with exclusive, atomic file publication."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.csv as csv
import pyarrow.parquet as pq

from ..data.table_access import TableAccess
from ..domain.errors import DataError, SchemaError
from ..storage.artifacts import ArtifactStore
from ..storage.sqlite import SQLiteCatalog


@dataclass(frozen=True, slots=True)
class DataExport:
    path: str
    project_id: str
    dataset_id: str
    version_id: str
    format: str
    rows: int
    columns: tuple[str, ...]
    size: int
    sha256: str


class DataExportService:
    def __init__(self, catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> None:
        self.catalog = catalog
        self.artifact_store = artifact_store

    def export(
        self,
        *,
        project_id: str,
        dataset_id: str,
        version_id: str,
        format: str,
        destination: str | Path,
    ) -> DataExport:
        if format not in {"csv", "parquet"}:
            raise SchemaError({"reason": "export_format"})
        self.catalog.get_project(project_id)
        dataset = self.catalog.get_dataset(dataset_id)
        version = self.catalog.get_version(version_id)
        if dataset.project_id != project_id or version.dataset_id != dataset_id:
            raise SchemaError({"reason": "export_context"})
        target = Path(destination).expanduser().absolute()
        if (
            not str(destination).strip()
            or target.suffix.lower() != "." + format
            or target.resolve().is_relative_to(self.catalog.paths.root)
            or not target.parent.is_dir()
            or ":" in target.name
            or target.name.endswith((" ", "."))
            or target.name.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10)),
            }
        ):
            raise SchemaError({"reason": "export_destination"})
        if target.exists() or target.is_symlink():
            raise DataError({"reason": "export_exists"})
        # Resolve the parent once so changes to a caller-supplied directory alias do
        # not redirect the temporary file or its final publication.
        target = target.parent.resolve() / target.name
        temporary = target.with_name(f".autoanalyst-export-{uuid4().hex}.tmp")
        owned = False
        try:
            with TableAccess(self.catalog, self.artifact_store).export_batches(version_id) as (
                columns,
                reader,
                source_schema,
            ):
                names = tuple(c.display_name for c in columns)
                if len(set(names)) != len(names):
                    raise SchemaError({"reason": "duplicate_column_names"})
                fields = (
                    reader.schema
                    if format == "csv"
                    else [source_schema.field(c.physical_name) for c in columns]
                )
                schema = pa.schema(
                    [field.with_name(name) for field, name in zip(fields, names, strict=True)]
                )
                count = 0
                with temporary.open("xb") as stream:
                    owned = True
                    if format == "csv":
                        stream.write(b"\xef\xbb\xbf")
                        writer = csv.CSVWriter(stream, schema)
                    else:
                        writer = pq.ParquetWriter(stream, schema, compression="snappy")
                    with writer:
                        for batch in reader:
                            renamed = batch.rename_columns(names).cast(schema)
                            writer.write_batch(renamed)
                            count += batch.num_rows
                    stream.flush()
                    os.fsync(stream.fileno())
                if count != version.row_count:
                    raise DataError({"reason": "export_row_count"})
            with temporary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            receipt = DataExport(
                str(target),
                project_id,
                dataset_id,
                version_id,
                format,
                count,
                names,
                temporary.stat().st_size,
                digest,
            )
            # Windows rename is exclusive and also works on removable FAT volumes.
            # POSIX rename replaces files, so use exclusive hard-link publication there.
            try:
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
            except FileExistsError as exc:
                raise DataError({"reason": "export_exists"}) from exc
            return receipt
        finally:
            if owned:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def download_bytes(receipt: DataExport, *, limit: int) -> bytes:
        path = Path(receipt.path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size != receipt.size:
            raise DataError({"reason": "export_changed"})
        if receipt.size > limit:
            raise DataError({"reason": "export_download_limit"})
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
        if len(content) != receipt.size or hashlib.sha256(content).hexdigest() != receipt.sha256:
            raise DataError({"reason": "export_changed"})
        return content
