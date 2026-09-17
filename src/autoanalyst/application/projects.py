"""Project lifecycle operations backed by the V2 SQLite catalog."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from ..domain.codec import utc_now
from ..domain.datasets import Project
from ..storage.sqlite import SQLiteCatalog


class ProjectService:
    def __init__(self, catalog: SQLiteCatalog) -> None:
        self.catalog = catalog

    def create(self, name: str, *, default_language: str = "en") -> Project:
        now = utc_now()
        project = Project(
            project_id=str(uuid4()),
            name=name,
            created_at=now,
            updated_at=now,
            default_language=default_language,
        )
        self.catalog.insert_project(project)
        return project

    def get(self, project_id: str) -> Project:
        return self.catalog.get_project(project_id)

    def list(self) -> tuple[Project, ...]:
        return self.catalog.list_projects()

    def rename(self, project_id: str, name: str) -> Project:
        current = self.get(project_id)
        validated = Project(
            project_id=current.project_id,
            name=name,
            created_at=current.created_at,
            updated_at=utc_now(),
            format_version=current.format_version,
            default_language=current.default_language,
            revision=current.revision + 1,
            archived_at=current.archived_at,
            schema_version=current.schema_version,
        )
        return cast(
            Project,
            self.catalog.rename_project(project_id, validated.name, validated.updated_at),
        )
