"""Subprocess entry point for V2 analysis execution."""

from __future__ import annotations

import argparse
import multiprocessing
from pathlib import Path

from ..bootstrap import build_runtime
from ..domain.errors import AutoAnalystError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoanalyst-worker")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    runtime = build_runtime(Path(args.workspace))
    run = runtime.run_store.get_run(args.run_id)
    if run.spec_id is None:
        return 2
    spec = runtime.run_store.get_spec(run.spec_id)
    module = runtime.registry.get(spec.module_id)
    cancel = multiprocessing.get_context("spawn").Event()
    try:
        runtime.coordinator.execute_inline(args.run_id, module, cancellation_event=cancel)
    except AutoAnalystError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
