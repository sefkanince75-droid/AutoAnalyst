from __future__ import annotations

import socket
from urllib.parse import urlsplit

import pytest

from autoanalyst import desktop
from tools.check_startup import check_browser, running_ui


def test_launcher_loads_production_config_before_bootstrap(monkeypatch, capsys):
    from streamlit.web import bootstrap
    from streamlit.web.server import server_util

    calls = []

    def load(options):
        calls.append("load")
        assert options["global.developmentMode"] is False
        assert options["server.address"] == options["browser.serverAddress"] == "127.0.0.1"
        assert options["server.port"] == options["browser.serverPort"]
        assert options["server.baseUrlPath"] == ""
        assert options["server.fileWatcherType"] == "none"
        assert options["server.headless"] is False

    def run(*args):
        assert calls == ["load"]
        calls.append("run")

    monkeypatch.setattr(bootstrap, "load_config_options", load)
    monkeypatch.setattr(bootstrap, "run", run)
    monkeypatch.setattr(server_util, "get_url", lambda address: f"http://{address}:43210")
    assert desktop.main(["--port", "0"]) == 0
    assert calls == ["load", "run"]
    assert "AutoAnalyst URL: http://127.0.0.1:43210" in capsys.readouterr().out


@pytest.mark.parametrize("port", ["-1", "65536", "not-a-port"])
def test_invalid_port_rejected_without_starting_server(port):
    with pytest.raises(SystemExit) as error:
        desktop.main(["--port", port])
    assert error.value.code == 2


def test_busy_preferred_port_uses_another_loopback_port():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        preferred = occupied.getsockname()[1]
        actual = desktop._select_loopback_port(preferred)
        assert actual != preferred and actual > 0
        assert occupied.getsockname()[1] == preferred


@pytest.mark.parametrize("hostile_config", [False, True])
def test_real_http_assets_websocket_and_ui_from_unrelated_directory(hostile_config):
    # A health-only test misses the frozen dev-mode failure: health=200, root=404.
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        preferred = occupied.getsockname()[1]
        environment = (
            {
                "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "true",
                "STREAMLIT_SERVER_PORT": "8501",
                "STREAMLIT_BROWSER_SERVER_PORT": "3000",
                "STREAMLIT_SERVER_BASE_URL_PATH": "unexpected-prefix",
            }
            if hostile_config
            else {}
        )
        with running_ui(preferred_port=preferred, environment=environment) as server:
            assert urlsplit(server.url).port != preferred
            assert server.http["health"] == server.http["root"] == 200
            assert any(asset["path"].endswith(".js") for asset in server.http["assets"])
            assert check_browser(server)["example_project_loaded"] is True
            assert (server.workspace / "catalog.sqlite").is_file()
