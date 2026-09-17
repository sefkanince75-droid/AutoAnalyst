"""Composition root for V2 services. No Streamlit dependencies are allowed here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    modules = []
    try:
        from .analyses.profiling import ProfilingModule

        modules.append(ProfilingModule(catalog, artifact_store))
    except ImportError:
        pass
    try:
        from .analyses.comparisons import ComparisonModule

        modules.append(ComparisonModule(catalog, artifact_store))
    except ImportError:
        pass
    try:
        from .analyses.binary.module import BinaryClassificationModule

        modules.append(BinaryClassificationModule(catalog, artifact_store))
    except ImportError:
        pass
    return AnalysisRegistry(modules)


def build_runtime(workspace: str | Path | None = None) -> Runtime:
    catalog = SQLiteCatalog(workspace)
    artifact_store = ArtifactStore(catalog.paths.root)
    run_store = RunStore(catalog)
    reconcile_interrupted_runs(run_store, catalog.paths.root)
    registry = build_registry(catalog, artifact_store)
    coordinator = ExecutionCoordinator(run_store)
    return Runtime(catalog, artifact_store, run_store, registry, coordinator)
