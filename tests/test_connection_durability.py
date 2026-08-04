"""Durability of the credential file.

This file is the sole copy of every refresh token. Losing it means
re-authorising every bank by hand, at the bank, one at a time - the exact chore
the rest of the project exists to minimise. So it is worth more care than an
ordinary data file.

Two hazards, both introduced by the deployment rather than the code: a write
interrupted by a crash or a container stop, and two processes in the compose
stack doing read-modify-write over the same path.
"""

import json
import os
import stat
import sys

import pytest

from obdi.connections import ConnectionStore, build_connection

TOKENS = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}


def connection(name: str = "halifax"):
    return build_connection(connection_id=name, provider="p", token_response=TOKENS)


class TestAtomicWrites:
    def test_Credentials_WhenWriteIsInterrupted_PreviousContentSurvives(
        self, tmp_path, monkeypatch
    ):
        # A truncating in-place rewrite loses every token at once. The previous
        # file must remain intact until the replacement is complete on disk.
        path = tmp_path / "connections.json"
        store = ConnectionStore(path)
        store.put(connection("halifax"))
        original = path.read_text(encoding="utf-8")

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", explode)
        with pytest.raises(OSError):
            store.put(connection("nationwide"))

        assert path.read_text(encoding="utf-8") == original
        assert "halifax" in ConnectionStore(path).load()

    def test_Credentials_WhenWriteFails_NoTemporaryFilesLeftBehind(self, tmp_path, monkeypatch):
        path = tmp_path / "connections.json"
        store = ConnectionStore(path)
        store.put(connection())

        monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        with pytest.raises(OSError):
            store.put(connection("nationwide"))

        leftovers = [p for p in tmp_path.iterdir() if p.name != "connections.json"]
        assert leftovers == []

    def test_Credentials_WhenWritten_FileIsCompleteAndParsable(self, tmp_path):
        path = tmp_path / "connections.json"
        store = ConnectionStore(path)
        store.put(connection("halifax"))
        store.put(connection("nationwide"))

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert set(loaded) == {"halifax", "nationwide"}


class TestPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission model")
    def test_Credentials_WhenWritten_NeverReadableByOthers(self, tmp_path):
        # Chmodding after writing leaves a window in which the tokens exist on
        # disk with default permissions. The temporary file must be created
        # restricted, not corrected afterwards.
        path = tmp_path / "connections.json"
        ConnectionStore(path).put(connection())
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0


class TestConcurrentUpdates:
    def test_Credentials_WhenTwoProcessesRefreshDifferentBanks_NeitherIsLost(self, tmp_path):
        # The compose stack runs a web service and a scheduler over one file.
        # A read-modify-write that starts before the other finishes would drop
        # whichever token was written first.
        path = tmp_path / "connections.json"
        first = ConnectionStore(path)
        second = ConnectionStore(path)

        first.put(connection("halifax"))
        second.put(connection("nationwide"))

        assert set(ConnectionStore(path).load()) == {"halifax", "nationwide"}

    def test_Credentials_WhenReloadedAfterEachWrite_LatestValuesWin(self, tmp_path):
        path = tmp_path / "connections.json"
        store = ConnectionStore(path)
        store.put(connection("halifax"))

        refreshed = build_connection(
            connection_id="halifax",
            provider="p",
            token_response={**TOKENS, "refresh_token": "rotated"},
        )
        ConnectionStore(path).put(refreshed)

        assert ConnectionStore(path).load()["halifax"].refresh_token == "rotated"


class TestOpeningAStoreDoesNotNeedTheWriteLock:
    """The page must render while a fetch is writing.

    Opening a store used to run migrations, and migrations write. A pull
    cycle holds its write transaction for minutes, so every page load
    waited on the busy timeout and the browser gave up - which is exactly
    what ERR_CONNECTION_ABORTED looked like from the phone.
    """

    def test_AnUpToDateStore_OpensWhileAnotherWriterHoldsItsTransaction(
        self, tmp_path
    ):
        import sqlite3
        import time

        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass  # first open does the work and stamps the version

        writer = sqlite3.connect(db, timeout=30.0)
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO fetch_attempts (attempted_at, source, connection_id, "
            "account_ref, asked, request_meta, outcome) VALUES "
            "('2026-08-04T16:45:00Z','truelayer-booked','halifax',"
            "'truelayer:a','90d','{}','ok')"
        )
        try:
            started = time.perf_counter()
            with Store(db) as store:
                store.counts()
            elapsed = time.perf_counter() - started
        finally:
            writer.rollback()
            writer.close()

        assert elapsed < 2.0, (
            f"opening the store took {elapsed:.1f}s while a writer held the "
            "lock - it is taking the write lock on open again"
        )

    def test_AFreshStore_StampsItsVersion_SoTheNextOpenIsReadOnly(self, tmp_path):
        from obdi.store import SCHEMA_VERSION, Store

        db = tmp_path / "s.sqlite3"
        with Store(db) as store:
            stamped = store.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()

        assert stamped[0] == str(SCHEMA_VERSION)

    def test_AStoreFromBeforeTheMechanism_IsStillMigrated(self, tmp_path):
        """An existing store has no meta table, so the first open after
        the upgrade must still do the work rather than assume it done."""
        import sqlite3

        from obdi.store import Store

        db = tmp_path / "s.sqlite3"
        with Store(db):
            pass
        legacy = sqlite3.connect(db)
        legacy.execute("DROP TABLE obdi_meta")
        legacy.execute(
            "INSERT INTO fetch_attempts (attempted_at, source, connection_id, "
            "account_ref, asked, request_meta, outcome) VALUES "
            "('2026-08-01T00:00:00Z','starling-feed','starling',"
            "'starling:uid-1','routine','{}','ok')"
        )
        legacy.commit()
        legacy.close()

        with Store(db) as store:
            ids = [
                row[0]
                for row in store.connection.execute(
                    "SELECT connection_id FROM fetch_attempts"
                )
            ]

        assert ids == ["starling-api"]
