"""Windows portable launcher and frozen-worker dispatcher for AutoAnalyst V2."""

from __future__ import annotations

import argparse
import socket
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
    parser.add_argument(
        "--native-check", action="store_true", help="Verify the rendered desktop window"
    )
    parser.add_argument("--browser", action="store_true", help="Open the external browser instead")
    parser.add_argument("--serve-ui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--port",
        type=int,
        default=8501,
        help="Preferred loopback port; 0 selects an available port",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Serve the UI without opening a browser"
    )
    parsed = parser.parse_args(args)
    if not 0 <= parsed.port <= 65535:
        parser.error("--port must be between 0 and 65535")

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

    if parsed.native_check or (
        sys.platform == "win32" and not (parsed.no_browser or parsed.serve_ui or parsed.browser)
    ):
        from autoanalyst.native import launch

        return launch(parsed.port, check=parsed.native_check)

    from streamlit.web import bootstrap
    from streamlit.web.server import server_util

    app_path = _streamlit_entrypoint()
    port = _select_loopback_port(parsed.port)
    options = {
        # Frozen Streamlit paths contain no site-packages component, which would
        # otherwise enable its Vite development frontend (port 3000, no static UI).
        "global.developmentMode": False,
        "server.address": "127.0.0.1",
        "server.port": port,
        "browser.serverAddress": "127.0.0.1",
        "browser.serverPort": port,
        "server.baseUrlPath": "",
        "server.headless": parsed.no_browser or parsed.serve_ui,
        "server.fileWatcherType": "none",
        "server.runOnSave": False,
        "server.maxUploadSize": 256,
        "browser.gatherUsageStats": False,
    }
    # bootstrap.run does not apply flag_options initially. The Streamlit CLI
    # calls this first; without it these flags only take effect on config reload.
    bootstrap.load_config_options(options)
    # Use the same resolver as Streamlit's real browser-opening callback.
    print(f"AutoAnalyst URL: {server_util.get_url('127.0.0.1')}", flush=True)
    bootstrap.run(str(app_path), False, [], options)
    return 0


def _select_loopback_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", preferred))
        except OSError:
            if preferred == 0:
                raise
            probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


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
