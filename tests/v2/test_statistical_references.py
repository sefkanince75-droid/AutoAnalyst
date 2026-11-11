from __future__ import annotations

import math
from uuid import uuid4

from autoanalyst.analyses.comparisons import ComparisonModule
from autoanalyst.analyses.contract import ExecutionContext
from autoanalyst.application import DatasetService, ProjectService
from autoanalyst.data import CSVIngestor
from autoanalyst.domain.codec import fingerprint, utc_now
from autoanalyst.domain.plans import AnalysisModuleId, AnalysisSpec, ResourceBudget
from autoanalyst.storage import ArtifactStore, SQLiteCatalog


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


def _workspace(tmp_path, csv_bytes: bytes):
    catalog = SQLiteCatalog(tmp_path)
    store = ArtifactStore(catalog.paths.root)
    project = ProjectService(catalog).create("Reference statistics")
    dataset = DatasetService(catalog).create(project.project_id, "Reference")
    imported = CSVIngestor(catalog, store).import_csv(
        project_id=project.project_id,
        dataset_id=dataset.dataset_id,
        source=csv_bytes,
        original_name="reference.csv",
    )
    columns = {
        str(row["display_name"]): str(row["column_id"])
        for row in catalog.list_columns(imported.version.version_id)
        if not row["is_system"]
    }
    return catalog, store, project, imported, columns


def _spec(project, imported, operation: str, parameters: dict[str, object]) -> AnalysisSpec:
    payload = {
        "operation": operation,
        "parameters": parameters,
        "version": imported.version.version_id,
    }
    return AnalysisSpec(
        spec_id=str(uuid4()),
        project_id=project.project_id,
        module_id=AnalysisModuleId.COMPARISON,
        module_version="2.0",
        operation=operation,
        input_version_id=imported.version.version_id,
        column_roles=(),
        parameters=parameters,
        seed=42,
        resource_budget=ResourceBudget(100_000_000, 100_000_000, 30, 1),
        spec_hash=fingerprint(payload),
        created_at=utc_now(),
    )


def _context(spec: AnalysisSpec) -> ExecutionContext:
    return ExecutionContext(str(uuid4()), spec.input_version_id, spec.seed, _NeverCancelled())


def _metrics(draft) -> dict[str, object]:
    return {metric.name: metric for metric in draft.metrics}


def test_reference_pearson_and_spearman_match_known_values(tmp_path) -> None:
    catalog, store, project, imported, columns = _workspace(
        tmp_path,
        b"x,y,reverse\n1,2,10\n2,4,8\n3,6,6\n4,8,4\n5,10,2\n",
    )
    module = ComparisonModule(catalog, store)

    pearson = _spec(
        project,
        imported,
        "correlation",
        {"x_column_id": columns["x"], "y_column_id": columns["y"], "method": "pearson"},
    )
    spearman = _spec(
        project,
        imported,
        "correlation",
        {"x_column_id": columns["x"], "y_column_id": columns["reverse"], "method": "spearman"},
    )

    assert math.isclose(
        _metrics(module.run(pearson, _context(pearson)))["correlation"].value, 1.0, abs_tol=1e-12
    )
    assert math.isclose(
        _metrics(module.run(spearman, _context(spearman)))["correlation"].value, -1.0, abs_tol=1e-12
    )


def test_reference_welch_matches_hand_checked_fixture(tmp_path) -> None:
    catalog, store, project, imported, columns = _workspace(
        tmp_path,
        b"group,value\nA,1\nA,2\nA,3\nB,4\nB,5\nB,6\n",
    )
    module = ComparisonModule(catalog, store)
    spec = _spec(
        project,
        imported,
        "welch_two_groups",
        {
            "group_column_id": columns["group"],
            "measure_column_id": columns["value"],
            "group_a": "A",
            "group_b": "B",
            "independence_confirmed": True,
        },
    )

    metrics = _metrics(module.run(spec, _context(spec)))
    assert math.isclose(metrics["mean_difference"].value, -3.0, abs_tol=1e-12)
    assert math.isclose(metrics["welch_t"].value, -3.6742346141747673, rel_tol=1e-12)
    assert math.isclose(metrics["welch_df"].value, 4.0, abs_tol=1e-12)
    assert math.isclose(metrics["p_value"].value, 0.021311641128756713, rel_tol=1e-12)
    assert math.isclose(metrics["mean_difference"].interval[0], -5.266957935527519, rel_tol=1e-12)
    assert math.isclose(metrics["mean_difference"].interval[1], -0.7330420644724809, rel_tol=1e-12)


def test_reference_fisher_exact_matches_known_2x2_table(tmp_path) -> None:
    rows = ["row_flag,col_flag"]
    rows += ["yes,yes"] * 1
    rows += ["yes,no"] * 9
    rows += ["no,yes"] * 11
    rows += ["no,no"] * 3
    catalog, store, project, imported, columns = _workspace(
        tmp_path,
        ("\n".join(rows) + "\n").encode("utf-8"),
    )
    module = ComparisonModule(catalog, store)
    spec = _spec(
        project,
        imported,
        "fisher_2x2",
        {
            "row_column_id": columns["row_flag"],
            "column_column_id": columns["col_flag"],
            "row_positive": "yes",
            "column_positive": "yes",
        },
    )

    metrics = _metrics(module.run(spec, _context(spec)))
    assert math.isclose(metrics["odds_ratio"].value, 0.030303030303030304, rel_tol=1e-12)
    assert math.isclose(metrics["p_value"].value, 0.0027594561852200836, rel_tol=1e-12)
