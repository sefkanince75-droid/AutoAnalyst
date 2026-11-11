from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from autoanalyst.application import DatasetService, PreparationService, ProjectService
from autoanalyst.data import CSVIngestor
from autoanalyst.storage import ArtifactStore, SQLiteCatalog


@dataclass
class Phase3Workspace:
    catalog: SQLiteCatalog
    store: ArtifactStore
    projects: ProjectService
    datasets: DatasetService
    preparation: PreparationService
    project: object
    dataset: object
    imported: object

    def column(self, display_name: str) -> dict[str, object]:
        return next(
            row
            for row in self.catalog.list_columns(self.imported.version.version_id)
            if not row["is_system"] and row["display_name"] == display_name
        )


@pytest.fixture
def phase3_workspace(tmp_path: Path) -> Phase3Workspace:
    catalog = SQLiteCatalog(tmp_path)
    store = ArtifactStore(catalog.paths.root)
    projects = ProjectService(catalog)
    project = projects.create("Preparation tests")
    datasets = DatasetService(catalog)
    dataset = datasets.create(project.project_id, "Input")
    imported = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=(
            b"num,text,flag,group\n1,1,true,a\n2,,false,a\n,x,true,b\n4,x,false,b\ninf,z,true,c\n"
        ),
        original_name="input.csv",
    )
    return Phase3Workspace(
        catalog,
        store,
        projects,
        datasets,
        PreparationService(catalog, store),
        project,
        dataset,
        imported,
    )
