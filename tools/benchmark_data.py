"""Reproducible synthetic C04 measurements; never opens a user workspace."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import psutil


def fingerprint_case(mode: str):
    import pandas as pd

    from autoanalyst.data.ingest import _canonical_scalar, _content_fingerprint
    from autoanalyst.domain.codec import fingerprint

    frame = pd.DataFrame({f"column_{i}": range(50_000) for i in range(8)})
    headers = tuple(frame.columns)
    contract = {"format": "csv", "delimiter": ",", "encoding": "utf-8", "header": True}
    process = psutil.Process()
    baseline = process.memory_info().rss
    peak = baseline
    stop = threading.Event()

    def sample():
        nonlocal peak
        while not stop.wait(0.005):
            peak = max(peak, process.memory_info().rss)

    thread = threading.Thread(target=sample)
    thread.start()
    started = time.perf_counter()
    try:
        if mode == "stream":
            digest = _content_fingerprint(frame, headers, contract)
        else:
            descriptor = {
                "parse_contract": contract,
                "columns": [
                    {"display_name": name, "physical_type": str(frame.dtypes.iloc[i]), "ordinal": i}
                    for i, name in enumerate(headers)
                ],
                "rows": [
                    [_canonical_scalar(v) for v in row]
                    for row in frame.itertuples(index=False, name=None)
                ],
            }
            digest = fingerprint(descriptor)
        return {
            "mode": mode,
            "rows": 50_000,
            "columns": 8,
            "seconds": time.perf_counter() - started,
            "baseline_rss_bytes": baseline,
            "peak_rss_bytes": peak,
            "sha256": digest,
        }
    finally:
        stop.set()
        thread.join()


def wait(service, identifier):
    deadline = time.monotonic() + 160
    while time.monotonic() < deadline:
        state = service.poll(identifier)
        if state["status"] != "running":
            if state["status"] != "completed":
                raise RuntimeError(f"Benchmark failed: {state.get('reason')}")
            return {
                key: state[key]
                for key in ("status", "elapsed_seconds", "peak_rss_bytes", "disk_growth_bytes")
            }
        time.sleep(0.05)
    service.cancel(identifier)
    raise TimeoutError("Synthetic benchmark timed out")


def benchmark(output: Path):
    from autoanalyst.application import DatasetService, PreparationService, ProjectService
    from autoanalyst.execution.data_tasks import DEFAULT_DATA_BUDGET, DataTaskService
    from autoanalyst.storage import ArtifactStore, SQLiteCatalog

    base = Path(tempfile.mkdtemp(prefix="autoanalyst-benchmark-")).resolve()
    expected_parent = Path(tempfile.gettempdir()).resolve()
    results = []
    try:
        for rows, columns in ((25_000, 8), (100_000, 8), (20_000, 80), (1_000_000, 5)):
            catalog = SQLiteCatalog(base / f"{rows}-{columns}")
            project = ProjectService(catalog).create("Synthetic benchmark")
            dataset = DatasetService(catalog).create(project.project_id, "Generated integers")
            service = DataTaskService(catalog)
            header = ",".join(f"c{i}" for i in range(columns)) + "\n"
            source = (
                header
                + "\n".join(
                    ",".join(str((r + c) % 10007) for c in range(columns)) for r in range(rows)
                )
                + "\n"
            ).encode()
            imported = wait(
                service,
                service.start(
                    operation="csv",
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    source=source,
                    options={"original_name": "synthetic.csv"},
                ),
            )
            version_record = DatasetService(catalog).current_version(dataset.dataset_id)
            user_columns = [
                c for c in catalog.list_columns(version_record.version_id) if not c["is_system"]
            ]
            preparation = PreparationService(catalog, ArtifactStore(catalog.paths.root))
            recipe = preparation.create_recipe(
                project_id=project.project_id,
                base_version_id=version_record.version_id,
                steps=[
                    {
                        "operation": "filter_rows",
                        "parameters": {
                            "column_id": user_columns[0]["column_id"],
                            "operator": "gt",
                            "value": 2500,
                        },
                    },
                    {
                        "operation": "rename_column",
                        "parameters": {
                            "column_id": user_columns[1]["column_id"],
                            "new_name": "renamed",
                        },
                    },
                ],
            )
            preview = wait(
                service,
                service.start(
                    operation="preparation",
                    project_id=project.project_id,
                    dataset_id=dataset.dataset_id,
                    recipe_id=recipe.recipe_id,
                ),
            )
            results.append(
                {
                    "rows": rows,
                    "columns": columns,
                    "source_bytes": len(source),
                    "import": imported,
                    "preparation_two_steps": preview,
                }
            )
            print(f"Measured {rows} rows x {columns} columns", flush=True)
        comparison = []
        for mode in ("legacy", "stream"):
            run = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--fingerprint-mode", mode],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            comparison.append(json.loads(run.stdout))
        assert comparison[0]["sha256"] == comparison[1]["sha256"]
        payload = {
            "synthetic_only": True,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "logical_cpus": os.cpu_count(),
            "total_ram_bytes": psutil.virtual_memory().total,
            "versions": {name: version(name) for name in ("pandas", "pyarrow", "duckdb", "psutil")},
            "budget": asdict(DEFAULT_DATA_BUDGET),
            "worker_sampling_seconds": 0.1,
            "notes": [
                "Worker RSS includes dependency loading; subprocess startup before monitor initialization is excluded from elapsed time.",
                "File growth is logical net workspace bytes; it is not total writes or allocated disk blocks.",
                "These are local synthetic measurements, not hardware-independent performance guarantees.",
                "Fingerprint comparison samples process RSS every 0.005 seconds in separate processes.",
            ],
            "cases": results,
            "fingerprint_comparison": comparison,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(output, flush=True)
    finally:
        # Verify the final absolute recursive-delete target belongs to this tool.
        if (
            base.parent != expected_parent
            or not base.name.startswith("autoanalyst-benchmark-")
            or base.is_symlink()
            or base.is_junction()
        ):
            raise RuntimeError("Unsafe benchmark cleanup path")
        shutil.rmtree(base)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("build/c04-benchmark.json"))
    parser.add_argument("--fingerprint-mode", choices=("legacy", "stream"))
    args = parser.parse_args()
    if args.fingerprint_mode:
        print(json.dumps(fingerprint_case(args.fingerprint_mode)))
    else:
        benchmark(args.output)


if __name__ == "__main__":
    main()
