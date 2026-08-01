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
