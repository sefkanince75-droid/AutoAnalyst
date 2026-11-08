"""Windows portable launcher and frozen-worker dispatcher for AutoAnalyst V2."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from autoanalyst import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # ExecutionCoordinator intentionally invokes sys.executable with the normal
    # ``python -m autoanalyst.execution.worker`` shape. In a frozen executable
    # sys.executable points back to this launcher, so dispatch that shape here.
    if args[:2] == ["-m", "autoanalyst.execution.worker"]:
        from autoanalyst.execution.worker import main as worker_main

        sys.argv = ["autoanalyst-worker", *args[2:]]
        return worker_main()

    if args[:2] == ["-m", "autoanalyst.execution.data_worker"]:
        from autoanalyst.execution.data_worker import main as data_worker_main

        sys.argv = ["autoanalyst-data-worker", *args[2:]]
        return data_worker_main()

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
        _streamlit_entrypoint()

        with tempfile.TemporaryDirectory(prefix="autoanalyst-selftest-") as root:
            from autoanalyst.smoke import verify_workspace_journey

            verify_workspace_journey(Path(root))
        print(f"AutoAnalyst {__version__} self-test OK")
        return 0

    from streamlit.web import bootstrap

    app_path = _streamlit_entrypoint()
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


def _streamlit_entrypoint() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        candidate = Path(bundle_root) / "autoanalyst" / "ui" / "entrypoint.py"
    else:
        candidate = Path(__file__).resolve().parent / "ui" / "entrypoint.py"
    if not candidate.is_file():
        raise FileNotFoundError("AutoAnalyst Streamlit entry point is missing")
    return candidate


if __name__ == "__main__":
    raise SystemExit(main())
