"""UI-independent, persistence-ready AutoAnalyst V2 domain models."""

from .datasets import ColumnRole, Dataset, DatasetSource, DatasetVersion, Project
from .plans import AnalysisSpec, PreparationRecipe, PreparationStep
from .results import AnalysisResult, Artifact, Finding, Metric, Report
from .runs import AnalysisRun, SplitManifest

__all__ = [
    "AnalysisResult",
    "AnalysisRun",
    "AnalysisSpec",
    "Artifact",
    "ColumnRole",
    "Dataset",
    "DatasetSource",
    "DatasetVersion",
    "Finding",
    "Metric",
    "PreparationRecipe",
    "PreparationStep",
    "Project",
    "Report",
    "SplitManifest",
]
