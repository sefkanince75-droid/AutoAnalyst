"""UI-independent application services for AutoAnalyst V2."""

from .datasets import DatasetService
from .projects import ProjectService

__all__ = ["DatasetService", "PreparationService", "ProjectService"]


def __getattr__(name: str):
    if name == "PreparationService":
        from .preparation import PreparationService

        return PreparationService
    raise AttributeError(name)
