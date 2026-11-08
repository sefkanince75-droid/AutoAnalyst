"""Owned Streamlit server in a native Windows WebView2 window."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from . import __version__


def server_command(port: int) -> list[str]:
    prefix = [sys.executable]
    if not getattr(sys, "frozen", False):
        prefix += ["-m", "autoanalyst.desktop"]
    return [*prefix, "--serve-ui", "--port", str(port)]


def wait_for_ui(process, url: str, timeout: float = 90) -> None:
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Desktop UI server exited before becoming ready")
        try:
            with opener.open(url + "/_stcore/health", timeout=2) as response:
                healthy = response.status == 200 and response.read() == b"ok"
            with opener.open(url, timeout=2) as response:
                html = response.read()
                if healthy and response.status == 200 and b"<html" in html:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise TimeoutError("Desktop UI did not become available")


def stop_server(process) -> None:
    """Stop this UI process tree while leaving independent analysis workers alive."""
    import psutil

    try:
        parent = psutil.Process(process.pid)
        owned = [
            child for child in parent.children(recursive=True) if "--serve-ui" in child.cmdline()
        ]
        for child in reversed(owned):
            child.terminate()
        if process.poll() is None:
            process.terminate()
        _, alive = psutil.wait_procs(owned, timeout=5)
        for child in alive:
            child.kill()
        process.wait(timeout=10)
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass


def hide_owned_console() -> None:
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    import ctypes

    import psutil

    kernel = ctypes.windll.kernel32
    kernel.GetConsoleWindow.restype = ctypes.c_void_p
    ctypes.windll.user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    identifiers = (ctypes.c_ulong * 32)()
    count = kernel.GetConsoleProcessList(identifiers, len(identifiers))
    try:
        if (
            count
            and count <= len(identifiers)
            and all(
                Path(psutil.Process(pid).exe()).resolve() == Path(sys.executable).resolve()
                for pid in identifiers[:count]
            )
        ):
            ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
    except psutil.Error:
        pass


def show_window(url: str, *, check: bool = False) -> None:
    import webview

    window = webview.create_window(
        f"AutoAnalyst V2 · {__version__}", url, width=1280, height=850, min_size=(900, 600)
    )
    if window is None:
        raise RuntimeError("Native desktop window could not be created")
    result = {"loaded": False}

    def verify():
        try:
            if not window.events.loaded.wait(60):
                return
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                state = window.evaluate_js("""JSON.stringify({
                    error: !!document.querySelector('[data-testid="stException"]'),
                    ready: document.querySelector('[data-testid="stApp"]')?.getAttribute('data-test-script-state') === 'notRunning',
                    sidebar: !!document.querySelector('[data-testid="stSidebar"]'),
                    title: document.body.innerText.includes('AutoAnalyst V2')
                })""")
                value = json.loads(state)
                if value["error"]:
                    raise RuntimeError("Rendered desktop UI contains an application exception")
                if all(value[key] for key in ("ready", "sidebar", "title")):
                    result["loaded"] = True
                    return
                time.sleep(0.2)
        finally:
            window.destroy()

    webview.start(verify if check else None, gui="edgechromium", private_mode=True, debug=False)
    if check and not result["loaded"]:
        raise RuntimeError("Desktop window did not render the application")


def launch(port: int, *, check: bool = False) -> int:
    from .desktop import _select_loopback_port
    from .storage.sqlite import SQLiteCatalog, WorkspacePaths

    if not check:
        hide_owned_console()
    paths = WorkspacePaths.create()
    log = paths.logs / "desktop-startup.log"
    process = None
    try:
        # Validate/migrate the actual workspace before displaying a blank webview.
        SQLiteCatalog(paths)
        selected = _select_loopback_port(port)
        url = f"http://127.0.0.1:{selected}"
        environment = os.environ.copy()
        if getattr(sys, "frozen", False):
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        with log.open("a", encoding="utf-8") as output:
            process = subprocess.Popen(
                server_command(selected),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            wait_for_ui(process, url)
            show_window(url, check=check)
        if check:
            print(
                json.dumps(
                    {"native_window": "WebView2", "rendered_ui": True, "version": __version__}
                ),
                flush=True,
            )
        return 0
    except Exception:
        with log.open("a", encoding="utf-8") as output:
            traceback.print_exc(file=output)
        if check:
            traceback.print_exc()
        elif sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"AutoAnalyst başlatılamadı. Verileriniz korunuyor.\nAyrıntılar: {log}",
                "AutoAnalyst V2",
                16,
            )
        return 1
    finally:
        if process is not None:
            stop_server(process)
