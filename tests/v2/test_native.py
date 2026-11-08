from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from autoanalyst import desktop, native


def test_windows_double_click_selects_native_instead_of_browser(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(native, "launch", lambda port, check: calls.append((port, check)) or 0)
    assert desktop.main([]) == 0
    assert calls == [(8501, False)]


def test_native_server_command_is_headless_and_supports_frozen_dispatch(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert native.server_command(1234) == [sys.executable, "--serve-ui", "--port", "1234"]
    monkeypatch.setattr(sys, "frozen", False)
    assert native.server_command(1234)[1:3] == ["-m", "autoanalyst.desktop"]


@pytest.mark.parametrize("error", [False, True])
def test_native_check_verifies_rendered_ui_and_closes_window(monkeypatch, error):
    import json

    calls = []
    window = SimpleNamespace(
        events=SimpleNamespace(loaded=SimpleNamespace(wait=lambda timeout: True)),
        evaluate_js=lambda script: json.dumps(
            dict(error=error, ready=True, sidebar=True, title=True)
        ),
        destroy=lambda: calls.append("closed"),
    )

    def start(callback, **options):
        assert options["gui"] == "edgechromium"
        assert options["debug"] is False
        try:
            callback()
        except RuntimeError:
            pass

    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(settings={}, create_window=lambda *a, **k: window, start=start),
    )
    if error:
        with pytest.raises(RuntimeError, match="did not render"):
            native.show_window("http://127.0.0.1:1234", check=True)
    else:
        native.show_window("http://127.0.0.1:1234", check=True)
    assert calls == ["closed"]
    assert sys.modules["webview"].settings["ALLOW_DOWNLOADS"] is True


def test_server_cleanup_runs_when_window_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOANALYST_WORKSPACE", str(tmp_path))
    calls = []
    process = object()
    monkeypatch.setattr(native.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(native, "wait_for_ui", lambda *a: None)
    monkeypatch.setattr(
        native, "show_window", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("webview failed"))
    )
    monkeypatch.setattr(native, "stop_server", lambda p: calls.append(p))
    assert native.launch(0, check=True) == 1
    assert calls == [process]
    assert "webview failed" in (tmp_path / "logs/desktop-startup.log").read_text()


def test_cleanup_does_not_terminate_analysis_workers(monkeypatch):
    import psutil

    stopped = []

    def child(kind):
        return SimpleNamespace(cmdline=lambda: [kind], terminate=lambda: stopped.append(kind))

    ui, worker = child("--serve-ui"), child("autoanalyst.execution.worker")
    monkeypatch.setattr(
        psutil, "Process", lambda pid: SimpleNamespace(children=lambda recursive: [ui, worker])
    )
    monkeypatch.setattr(psutil, "wait_procs", lambda processes, timeout: (processes, []))
    process = SimpleNamespace(
        pid=123,
        poll=lambda: None,
        terminate=lambda: stopped.append("parent"),
        wait=lambda timeout: None,
    )
    native.stop_server(process)
    assert stopped == ["--serve-ui", "parent"]


def test_readiness_detects_server_crash_without_opening_window():
    with pytest.raises(RuntimeError, match="exited"):
        native.wait_for_ui(SimpleNamespace(poll=lambda: 1), "http://127.0.0.1:1", timeout=1)


def test_missing_native_window_returns_an_actionable_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "webview", SimpleNamespace(settings={}, create_window=lambda *a, **k: None)
    )
    with pytest.raises(RuntimeError, match="could not be created"):
        native.show_window("http://127.0.0.1:1234")


@pytest.mark.parametrize("fails", [False, True])
def test_native_startup_displays_progress_then_navigates_or_reports_failure(monkeypatch, fails):
    calls = []
    window = SimpleNamespace(
        load_url=lambda url: calls.append("navigate"), destroy=lambda: calls.append("closed")
    )

    def create(*args, **kwargs):
        assert args[1] is None
        assert "<progress" in kwargs["html"]
        calls.append("window")
        return window

    def wait(*args):
        calls.append("ready")
        if fails:
            raise TimeoutError("not ready")

    monkeypatch.setitem(
        sys.modules,
        "webview",
        SimpleNamespace(settings={}, create_window=create, start=lambda callback, **kw: callback()),
    )
    monkeypatch.setattr(native, "wait_for_ui", wait)
    if fails:
        with pytest.raises(RuntimeError, match="failed during startup"):
            native.show_window("http://127.0.0.1:1234", process=object())
        assert calls == ["window", "ready", "closed"]
    else:
        native.show_window("http://127.0.0.1:1234", process=object())
        assert calls == ["window", "ready", "navigate"]
