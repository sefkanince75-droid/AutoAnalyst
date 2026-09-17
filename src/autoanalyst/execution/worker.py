"""Subprocess entry point for V2 analysis execution."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ..bootstrap import build_runtime
from ..domain.errors import AutoAnalystError
from .lease import release_worker
from .protocol import FileCancellationToken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoanalyst-worker")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    runtime = build_runtime(workspace)
    run = runtime.run_store.get_run(args.run_id)
    if run.spec_id is None:
        release_worker(workspace, args.run_id, pid=os.getpid())
        return 2
    spec = runtime.run_store.get_spec(run.spec_id)
    module = runtime.registry.get(spec.module_id)
    cancel = FileCancellationToken(workspace / "staging" / f"cancel-{args.run_id}.flag")
    try:
        runtime.coordinator.execute_inline(args.run_id, module, cancellation_event=cancel)
    except AutoAnalystError:
        return 1
    finally:
        release_worker(workspace, args.run_id, pid=os.getpid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
