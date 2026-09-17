"""Composition root for V2 services. No Streamlit dependencies are allowed here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analyses.binary.module import BinaryClassificationModule
from .analyses.comparisons import ComparisonModule
from .analyses.preparation import PreparationModule
from .analyses.profiling import ProfilingModule
from .analyses.registry import AnalysisRegistry
from .execution.coordinator import ExecutionCoordinator
from .execution.recovery import reconcile_interrupted_runs
from .storage.artifacts import ArtifactStore
from .storage.runs import RunStore
from .storage.sqlite import SQLiteCatalog


@dataclass(slots=True)
class Runtime:
    catalog: SQLiteCatalog
    artifact_store: ArtifactStore
    run_store: RunStore
    registry: AnalysisRegistry
    coordinator: ExecutionCoordinator


def build_registry(catalog: SQLiteCatalog, artifact_store: ArtifactStore) -> AnalysisRegistry:
    """Build the fixed V2 registry.

    Required V2 modules are imported eagerly. A missing dependency is therefore a
    startup/package failure instead of silently shrinking the supported product.
    """
    return AnalysisRegistry(
        (
            ProfilingModule(catalog, artifact_store),
            PreparationModule(catalog, artifact_store),
            ComparisonModule(catalog, artifact_store),
            BinaryClassificationModule(catalog, artifact_store),
        )
    )


def build_runtime(workspace: str | Path | None = None) -> Runtime:
    catalog = SQLiteCatalog(workspace)
    artifact_store = ArtifactStore(catalog.paths.root)
    run_store = RunStore(catalog)
    reconcile_interrupted_runs(run_store, catalog.paths.root)
    registry = build_registry(catalog, artifact_store)
    coordinator = ExecutionCoordinator(run_store)
    return Runtime(catalog, artifact_store, run_store, registry, coordinator)
