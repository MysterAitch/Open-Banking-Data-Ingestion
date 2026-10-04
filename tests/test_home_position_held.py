"""The home page's Position line does not re-walk every account each time it is opened.

`read_position` runs several statements per account (186 over twenty accounts, measured), and the
home page is the one opened most. The line is held while nothing the standings read has changed,
and read again the moment something has.
"""

from __future__ import annotations

import threading
from http.server import HTTPServer

import httpx
import pytest

import home_world as world
import obdi.position as position_module
from obdi.balance_anchors import record_stated_anchor
from obdi.cli import build_web_config
from obdi.store import Store
from obdi.web import AuthorisationSession, ConnectionHandler


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    db = tmp_path / "store.sqlite3"
    world.build_scale_world(db)
    config = build_web_config(db)
    assert config is not None
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield db, f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


@pytest.fixture
def reads(monkeypatch):
    calls: list[int] = []
    real = position_module.read_position

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(position_module, "read_position", counting)
    return calls


def test_Home_OpenedTwiceWithNothingChanged_ReadsThePositionOnce(served, reads):
    _, base = served

    first = httpx.get(f"{base}/", timeout=30).text
    second = httpx.get(f"{base}/", timeout=30).text

    assert len(reads) == 1
    assert "Counts 18 accounts of 20" in first and "Counts 18 accounts of 20" in second


def test_Home_AfterABalanceIsStated_ReadsThePositionAgain(served, reads):
    db, base = served
    httpx.get(f"{base}/", timeout=30)

    with Store(db) as store:
        record_stated_anchor(store, world.AGREE[1], "2026-03-25", "1.00")
    httpx.get(f"{base}/", timeout=30)

    assert len(reads) == 2, "a changed standing must never be shown a position held from before"


def test_PositionPage_OpenedAfterTheHome_IsNeverServedFromTheHeldCopy(served, reads):
    _, base = served
    httpx.get(f"{base}/", timeout=30)

    httpx.get(f"{base}/position", timeout=30)

    assert len(reads) == 2, "the Position page reads for itself; only the home line is held"
