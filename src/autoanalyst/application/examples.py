"""Deterministic synthetic examples; no network or real participant data."""

import csv
import io
import random

from ..data.ingest import CSVIngestor
from ..domain.errors import SchemaError
from ..storage.activity import write_activity
from .datasets import DatasetService
from .projects import ProjectService


def example_csv(kind: str) -> bytes:
    if kind not in {"quality", "binary", "regression", "multiclass"}:
        raise SchemaError({"reason": "example_unknown"})
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    rng = random.Random(42)
    writer.writerow(("value", "category", "target"))
    for index in range(400):
        value = round(rng.uniform(-5, 5), 4)
        category = ("A", "B", "C")[index % 3]
        if kind == "quality":
            writer.writerow(("" if index % 13 == 0 else value, category, index % 2))
        elif kind == "binary":
            writer.writerow((value, category, int(value + rng.gauss(0, 1) > 0)))
        elif kind == "regression":
            writer.writerow((value, category, round(value * 3 + rng.gauss(0, 0.4), 5)))
        else:
            writer.writerow(
                (value, category, "low" if value < -1.5 else "high" if value > 1.5 else "middle")
            )
    return output.getvalue().encode("utf-8")


def create_example(runtime, kind: str, *, language: str):
    raw = example_csv(kind)
    with write_activity(runtime.catalog.paths.root):
        project = ProjectService(runtime.catalog).create(
            f"Example · {kind}", default_language=language
        )
        dataset = DatasetService(runtime.catalog).create(project.project_id, kind)
        CSVIngestor(runtime.catalog, runtime.artifact_store).import_csv(
            project_id=project.project_id,
            dataset_id=dataset.dataset_id,
            source=raw,
            original_name=f"example-{kind}.csv",
        )
        return project
