from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from autoanalyst.application import DatasetService, ProjectService
from autoanalyst.data import CSVIngestor
from autoanalyst.storage import ArtifactStore, SQLiteCatalog

ROOT = Path(__file__).parents[2]


def test_v2_streamlit_reopens_persisted_project_without_backend_exception(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(workspace))

    catalog = SQLiteCatalog(workspace)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Journey project", default_language="en")
    dataset = DatasetService(catalog).create(project.project_id, "Journey data")
    CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=b"value,group\n1,a\n2,a\n3,b\n4,b\n",
        original_name="journey.csv",
    )

    app = AppTest.from_file(str(ROOT / "app.py"))
    app.run(timeout=30)

    assert len(app.exception) == 0
    assert any("AutoAnalyst V2" in title.value for title in app.title)
    assert any("Journey project" in str(item.value) for item in app.selectbox)

    # A second independent Streamlit session must reopen the same persisted workspace.
    reopened = AppTest.from_file(str(ROOT / "app.py"))
    reopened.run(timeout=30)
    assert len(reopened.exception) == 0
    assert any("Journey project" in str(item.value) for item in reopened.selectbox)
