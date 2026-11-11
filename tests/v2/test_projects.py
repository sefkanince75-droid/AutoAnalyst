from __future__ import annotations

from pathlib import Path

import pytest

from autoanalyst.application.projects import ProjectService
from autoanalyst.storage.sqlite import SQLiteCatalog


def test_project_create_get_list_and_rename(tmp_path: Path) -> None:
    service = ProjectService(SQLiteCatalog(tmp_path))
    first = service.create("First", default_language="tr")
    second = service.create("First")

    assert service.get(first.project_id) == first
    assert {project.project_id for project in service.list()} == {
        first.project_id,
        second.project_id,
    }
    renamed = service.rename(first.project_id, "Renamed")
    assert renamed.name == "Renamed"
    assert renamed.revision == 1
    assert renamed.updated_at >= first.updated_at


def test_invalid_project_name_is_rejected_before_storage(tmp_path: Path) -> None:
    service = ProjectService(SQLiteCatalog(tmp_path))
    with pytest.raises(ValueError):
        service.create("   ")
    assert service.list() == ()
