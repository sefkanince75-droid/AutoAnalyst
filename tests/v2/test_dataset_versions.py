from __future__ import annotations

from pathlib import Path

from autoanalyst.application.datasets import DatasetService
from autoanalyst.application.projects import ProjectService
from autoanalyst.data.ingest import CSVIngestor
from autoanalyst.storage.artifacts import ArtifactStore
from autoanalyst.storage.sqlite import SQLiteCatalog


def test_head_revision_history_and_events_advance_monotonically(tmp_path: Path) -> None:
    catalog = SQLiteCatalog(tmp_path)
    project = ProjectService(catalog).create("Versions")
    datasets = DatasetService(catalog)
    dataset = datasets.create(project.project_id, "Measurements")
    ingestor = CSVIngestor(catalog, ArtifactStore(catalog.paths.root))

    first = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"value\n1\n",
        original_name="v1.csv",
    )
    after_first = datasets.get(dataset.dataset_id)
    second = ingestor.import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"value\n2\n",
        original_name="v2.csv",
    )
    after_second = datasets.get(dataset.dataset_id)

    assert after_first.head_version_id == first.version.version_id
    assert after_first.head_revision == 1
    assert after_second.head_version_id == second.version.version_id
    assert after_second.head_revision == 2
    assert datasets.version_history(dataset.dataset_id) == (first.version, second.version)
    assert datasets.get_version(first.version.version_id) == first.version
    events = catalog.list_head_events(dataset.dataset_id)
    assert [(event["from_revision"], event["to_revision"]) for event in events] == [(0, 1), (1, 2)]

    moved = datasets.move_head(
        dataset.dataset_id, first.version.version_id, reason="restore_version"
    )
    assert moved.head_version_id == first.version.version_id
    assert moved.head_revision == 3
    assert catalog.list_head_events(dataset.dataset_id)[-1]["reason"] == "restore_version"
