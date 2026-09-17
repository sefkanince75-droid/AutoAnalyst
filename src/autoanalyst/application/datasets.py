"""Dataset metadata and immutable version navigation operations."""

from __future__ import annotations

from uuid import uuid4

from ..domain.datasets import Dataset, DatasetVersion
from ..storage.sqlite import SQLiteCatalog


class DatasetService:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog

    def create(self, project_id: str, name: str) -> Dataset:
        self.catalog.get_project(project_id)
        dataset = Dataset(dataset_id=str(uuid4()), project_id=project_id, name=name)
        self.catalog.insert_dataset(dataset)
        return dataset

    def get(self, dataset_id: str) -> Dataset:
        return self.catalog.get_dataset(dataset_id)

    def list(self, project_id: str) -> tuple[Dataset, ...]:
        self.catalog.get_project(project_id)
        with self.catalog.connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT dataset_id FROM datasets WHERE project_id = ? ORDER BY name, dataset_id",
                    (project_id,),
                )
            ]
        return tuple(self.get(dataset_id) for dataset_id in ids)

    def get_version(self, version_id: str) -> DatasetVersion:
        return self.catalog.get_version(version_id)

    def current_version(self, dataset_id: str) -> DatasetVersion | None:
        dataset = self.get(dataset_id)
        return self.get_version(dataset.head_version_id) if dataset.head_version_id else None

    def version_history(self, dataset_id: str) -> tuple[DatasetVersion, ...]:
        self.get(dataset_id)
        return self.catalog.list_versions(dataset_id)

    def move_head(
        self, dataset_id: str, version_id: str, *, reason: str = "manual_head_move"
    ) -> Dataset:
        return self.catalog.move_head(dataset_id, version_id, reason=reason)
