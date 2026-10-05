"""The real pages, served in this process over the large store, each GET timed and its SQL counted.

The wiring is `cli.build_web_config`, the one the server uses, so a page is drawn by the hooks a
person's request would reach. Statements are counted on every connection the process opens
(`sqlite3.connect` is wrapped for the duration), because the hooks open a store of their own.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from large_store_corpus import LargeStore
from obdi import cli
from obdi.web import AuthorisationSession, ConnectionHandler


@dataclass(frozen=True)
class Served:
    """One GET: how long it took, how many statements it issued, and what came back."""

    path: str
    status: int
    seconds: float
    statements: int
    selects: int
    body: str


class Pages:
    def __init__(self, port: int, issued: list[str]) -> None:
        self._port = port
        self._issued = issued

    def get(self, path: str) -> Served:
        self._issued.clear()
        started = time.perf_counter()
        response = httpx.get(f"http://127.0.0.1:{self._port}{path}", timeout=300)
        seconds = time.perf_counter() - started
        issued = list(self._issued)
        return Served(
            path,
            response.status_code,
            seconds,
            len(issued),
            sum(1 for sql in issued if sql.lstrip().upper().startswith("SELECT")),
            response.text,
        )


@contextmanager
def serving(large: LargeStore, workdir: Path) -> Iterator[Pages]:
    """Serve `large` (never written to) with its account map placed in `workdir`."""
    issued: list[str] = []
    real_connect = sqlite3.connect

    def counting(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(issued.append)
        return connection

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OBDI_ACCOUNT_MAP", str(large.write_account_map(workdir)))
        patch.setenv("OBDI_CONNECTION_STORE", str(workdir / "connections.json"))
        patch.setenv("OBDI_RAW_DIR", str(workdir / "raw"))
        patch.setenv("OBDI_DB_PATH", str(large.path))
        patch.setenv("TRUELAYER_CLIENT_ID", "x")
        patch.setenv("TRUELAYER_CLIENT_SECRET", "tlcs_live_abcdefghij1234567890")
        patch.setenv("TRUELAYER_REDIRECT_URI", "http://127.0.0.1/callback")
        patch.setattr(sqlite3, "connect", counting)
        config = cli.build_web_config(large.path)
        assert config is not None
        handler = type(
            "H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
        )
        httpd = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            yield Pages(httpd.server_port, issued)
        finally:
            httpd.shutdown()
            httpd.server_close()
