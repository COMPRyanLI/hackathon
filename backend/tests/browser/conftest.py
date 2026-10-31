"""Browser test fixtures: a real uvicorn server (with the background worker) on a
free loopback port and a scratch database, driven by Playwright through the
installed Microsoft Edge (or Chrome). No browser download; tests skip cleanly
when Playwright or a browser is unavailable. Only synthetic data is used."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND_DIST = BACKEND.parent / "frontend" / "dist" / "index.html"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def server(tmp_path: Path):
    if not FRONTEND_DIST.is_file():
        pytest.skip("frontend/dist is not built (run npm run build in frontend/)")
    port = _free_port()
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'browser.db').as_posix()}",
        "PRIVATE_DATA_DIR": str(tmp_path / "private"),
        "START_WORKER": "true",
        "PORT": str(port),
    })
    log = open(tmp_path / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=2) as r:
                    if json.loads(r.read()).get("status") == "ok":
                        break
            except OSError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                log.flush()
                raise RuntimeError("server did not start:\n" + (tmp_path / "server.log").read_text(errors="replace")[-3000:])
            time.sleep(0.3)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        log.close()
        for _ in range(30):
            if not _port_open(port):
                break
            time.sleep(0.2)
        assert not _port_open(port), f"port {port} still open after shutdown"


@pytest.fixture(scope="session")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")
    manager = sync_playwright().start()
    instance = None
    for channel in ("msedge", "chrome"):
        try:
            instance = manager.chromium.launch(channel=channel, headless=True)
            break
        except Exception:  # browser channel unavailable on this machine
            continue
    if instance is None:
        manager.stop()
        pytest.skip("neither Microsoft Edge nor Chrome could be launched")
    yield instance
    instance.close()
    manager.stop()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1400, "height": 1000})
    pg = context.new_page()
    pg.set_default_timeout(20_000)
    errors: list[str] = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg.base = server
    pg.page_errors = errors
    yield pg
    context.close()
