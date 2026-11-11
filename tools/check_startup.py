"""Verify real HTTP assets, websocket and rendered UI using a private workspace.

This deliberately launches the normal application, not --self-test. Playwright is
a development-only dependency; nothing here is imported by the shipped launcher.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import ProxyHandler, build_opener

import psutil


@dataclass
class RunningUI:
    url: str
    workspace: Path
    process: subprocess.Popen
    http: dict


def _request(url):
    with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
        return response.status, response.headers.get_content_type(), response.read()


def _http_ready(url):
    status, _, body = _request(url + "/_stcore/health")
    if status != 200 or body.strip() != b"ok":
        raise RuntimeError("UI health endpoint is not ready")
    status, content_type, body = _request(url + "/")
    if status != 200 or content_type != "text/html" or b"<html" not in body.lower():
        raise RuntimeError("Health succeeded but the frontend HTML is unavailable")
    assets = re.findall(r'(?:src|href)="([^"?]+\.(?:js|css))"', body.decode())
    if not any(asset.endswith(".js") for asset in assets):
        raise RuntimeError("Frontend HTML has no production JavaScript bundle")
    checked = []
    for asset in assets:
        target = urljoin(url + "/", asset)
        if urlsplit(target).netloc != urlsplit(url).netloc:
            raise RuntimeError("Unexpected remote frontend asset")
        code, kind, data = _request(target)
        if code != 200 or not data or kind == "text/html":
            raise RuntimeError("Frontend asset is missing or falls back to HTML")
        checked.append({"path": urlsplit(target).path, "status": code, "type": kind})
    return {"health": 200, "root": 200, "assets": checked}


def _stop_owned(process):
    if process.poll() is not None:
        return
    try:
        parent = psutil.Process(process.pid)
        # A normal launch may start the user's default browser. Never terminate
        # that browser (or restored tabs) when stopping our test server.
        allowed = {parent.name().casefold(), "conhost.exe"}
        children = [
            child for child in parent.children(recursive=True) if child.name().casefold() in allowed
        ]
    except psutil.NoSuchProcess:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.wait(timeout=10)
    psutil.wait_procs(children, timeout=10)


def _owns_listener(process, url):
    parent = psutil.Process(process.pid)
    for child in [parent, *parent.children(recursive=True)]:
        try:
            if any(
                # psutil on this Windows build reports the live uvicorn TCP
                # listening endpoint as NONE. HTTP readiness is checked next.
                item.status in {psutil.CONN_LISTEN, psutil.CONN_NONE}
                and item.laddr.port == urlsplit(url).port
                and item.laddr.ip == "127.0.0.1"
                for item in child.net_connections(kind="tcp")
            ):
                return True
        except psutil.NoSuchProcess:
            continue
    return False


@contextmanager
def running_ui(executable=None, *, preferred_port=0, environment=None, timeout=90):
    command = (
        [str(Path(executable).resolve())]
        if executable
        else [sys.executable, "-m", "autoanalyst.desktop"]
    )
    with TemporaryDirectory(prefix="autoanalyst-http-check-") as directory:
        root = Path(directory)
        env = {
            **os.environ,
            **(environment or {}),
            "AUTOANALYST_WORKSPACE": str(root / "workspace"),
        }
        log = root / "launcher.log"
        with log.open("wb") as output:
            process = subprocess.Popen(
                [*command, "--port", str(preferred_port), "--no-browser"],
                cwd=root,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + timeout
                last_error = None
                while time.monotonic() < deadline:
                    text = log.read_text(encoding="utf-8", errors="replace")
                    if process.poll() is not None:
                        raise RuntimeError(
                            f"UI process exited ({process.returncode}):\n{text[-4000:]}"
                        )
                    match = re.search(r"^AutoAnalyst URL: (http://127\.0\.0\.1:\d+)$", text, re.M)
                    if match:
                        url = match[1]
                        try:
                            if not _owns_listener(process, url):
                                raise RuntimeError(
                                    "Advertised listener is not owned by this test process"
                                )
                            http = _http_ready(url)
                        except (OSError, URLError, RuntimeError) as exc:
                            last_error = exc
                        else:
                            yield RunningUI(url, root / "workspace", process, http)
                            return
                    time.sleep(0.1)
                raise TimeoutError(
                    f"UI never served production HTML/assets: {last_error}\n{text[-4000:]}"
                )
            finally:
                _stop_owned(process)


def check_browser(server, screenshot=None):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": 1280, "height": 900}, service_workers="block"
            )
            errors, sockets = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("websocket", lambda socket: sockets.append(socket.url))
            page.route(
                "**/*",
                lambda route: route.continue_()
                if urlsplit(route.request.url).hostname == "127.0.0.1"
                else route.abort(),
            )
            response = page.goto(server.url, wait_until="domcontentloaded", timeout=30_000)
            assert response and response.status == 200
            expect(page.get_by_role("heading", name="AutoAnalyst V2", exact=True)).to_be_visible(
                timeout=60_000
            )
            expect(page.get_by_test_id("stApp")).to_have_attribute(
                "data-test-script-state", "notRunning", timeout=60_000
            )
            if not (server.workspace / "catalog.sqlite").is_file():
                raise RuntimeError("UI did not initialize this check's private workspace")
            page.get_by_text("Quick start and examples", exact=True).click()
            page.get_by_role("button", name="Create new example project", exact=True).click()
            expect(page.get_by_role("tab", name="Data", exact=True)).to_be_visible(timeout=30_000)
            expect(page.get_by_test_id("stApp")).to_have_attribute(
                "data-test-script-state", "notRunning", timeout=60_000
            )
            expect(page.get_by_test_id("stDataFrame").first).to_be_visible(timeout=30_000)
            expect(page.locator('[data-testid="stException"]')).to_have_count(0)
            if errors:
                raise RuntimeError(f"Browser runtime errors: {errors}")
            expected = urlsplit(server.url)
            if not any(
                urlsplit(url).netloc == expected.netloc and urlsplit(url).path == "/_stcore/stream"
                for url in sockets
            ):
                raise RuntimeError("UI websocket did not connect to the advertised server")
            if screenshot:
                Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot), full_page=True)
            return {"title": page.title(), "example_project_loaded": True, "websockets": sockets}
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    with running_ui(args.executable) as server:
        record = {
            "url": server.url,
            "http": server.http,
            "browser": check_browser(server, args.screenshot),
        }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
