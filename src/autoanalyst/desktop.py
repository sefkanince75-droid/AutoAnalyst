"""Windows portable launcher and frozen-worker dispatcher for AutoAnalyst V2."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from autoanalyst import __version__
from autoanalyst.bootstrap import build_runtime


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # ExecutionCoordinator intentionally invokes sys.executable with the normal
    # ``python -m autoanalyst.execution.worker`` shape. In a frozen executable
    # sys.executable points back to this launcher, so dispatch that shape here.
    if args[:2] == ["-m", "autoanalyst.execution.worker"]:
        from autoanalyst.execution.worker import main as worker_main

        sys.argv = ["autoanalyst-worker", *args[2:]]
        return worker_main()

    parser = argparse.ArgumentParser(prog="AutoAnalyst")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parsed = parser.parse_args(args)

    if parsed.version:
        print(__version__)
        return 0
    if parsed.self_test:
        from autoanalyst.ui.app import main as ui_main

        if not callable(ui_main):
            raise RuntimeError("Bundled V2 UI entry point is unavailable")
        _bundled_file("app.py")
        worker_probe = subprocess.run(
            [
                sys.executable,
                "-m",
                "autoanalyst.execution.worker",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if worker_probe.returncode != 0:
            raise RuntimeError(
                "Bundled worker dispatcher failed self-test: " + worker_probe.stderr[-1000:]
            )

        with tempfile.TemporaryDirectory(prefix="autoanalyst-selftest-") as root:
            runtime = build_runtime(Path(root))
            if not runtime.catalog.paths.catalog.is_file():
                raise RuntimeError("Self-test workspace catalog was not created")
            if not runtime.registry.module_ids:
                raise RuntimeError("Self-test analysis registry is empty")
        print(f"AutoAnalyst {__version__} self-test OK")
        return 0

    from streamlit.web import bootstrap

    app_path = _bundled_file("app.py")
    bootstrap.run(
        str(app_path),
        False,
        [],
        {
            "server.headless": False,
            "server.maxUploadSize": 256,
            "browser.gatherUsageStats": False,
        },
    )
    return 0


def _bundled_file(name: str) -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidate = Path(bundle_root) / name
    else:
        candidate = Path(__file__).resolve().parents[2] / name
    if not candidate.is_file():
        raise FileNotFoundError(f"Bundled AutoAnalyst file is missing: {name}")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
