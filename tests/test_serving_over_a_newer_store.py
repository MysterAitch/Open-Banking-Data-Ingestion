"""The web process does not start serving over a store newer than its code.

A SECOND-ROUND REVIEW FINDING, held as a failing test. A store stamped newer than the code is
refused wherever it is opened (`StoreIsNewer`), and nothing restamps it. But `cli._serve` builds
the pages' wiring without opening the store, and the one open it makes at start-up sits inside
"never let the freshness check keep the service down": the refusal is printed as
"startup: rebuild-on-deploy check failed" and the server starts. `/healthz` then answers "ok",
the home page answers 200 with "the overview could not be assembled", and the account pages
answer 500. A rollback onto a newer store is therefore not stopped where whoever is deploying is
watching: the container comes up and is healthy.

Decided before the first run: started over a store stamped one version newer, `_serve` does not
go on to serve (it raises the refusal or returns a failing code). Over a current store it does,
which is the control.

What the scheduled fetch does was read and not run: `_pull` opens the store for the account map
before anything else, outside any handler, so the refusal ends the cycle with a traceback before
a provider is asked.
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest

import obdi.cli as cli
from obdi.store import SCHEMA_VERSION, Store, StoreIsNewer


@pytest.fixture
def started(tmp_path, monkeypatch):
    """`_serve` with the socket and the warm-up thread replaced, recording whether it served."""
    monkeypatch.setenv("OBDI_CONNECTION_STORE", str(tmp_path / "connections.json"))
    monkeypatch.delenv("OBDI_ACCOUNT_MAP", raising=False)
    for variable in ("TRUELAYER_CLIENT_ID", "TRUELAYER_CLIENT_SECRET_FILE"):
        monkeypatch.delenv(variable, raising=False)
    served: list[int] = []
    monkeypatch.setattr(cli, "serve_web", lambda config, host, port: served.append(port))
    monkeypatch.setattr(cli, "_warm", lambda warm: None)
    db = tmp_path / "store.sqlite3"
    with Store(db):
        pass

    def start() -> list[int]:
        with contextlib.suppress(StoreIsNewer):
            cli._serve("127.0.0.1", 0, db)
        return served

    return db, start


class TestStartingTheWebProcess:
    def test_Serve_OverACurrentStore_Serves(self, started):
        _, start = started

        assert start() == [0]

    def test_Serve_OverAStoreStampedNewerThanTheCode_DoesNotServe(self, started):
        db, start = started
        connection = sqlite3.connect(str(db))
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION + 1),),
        )
        connection.commit()
        connection.close()

        assert start() == [], "the refusal was printed and the server started all the same"
