"""The targetless pull: the scheduler's contract, previously protected by nothing.

Bare `obdi pull` exists so the scheduler cannot drift from the connection
store - and its promises (keep going after one bank fails, pick up Starling
only when configured, fail informatively when the store is unreadable) were
each independently regressible with the whole suite green. This file is the
protection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from obdi.cli import _pull_everything


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "OBDI_CONNECTION_STORE",
        "STARLING_PERSONAL_ACCESS_TOKEN",
        "STARLING_PERSONAL_ACCESS_TOKEN_FILE",
    ):
        monkeypatch.delenv(name, raising=False)


def _store_with(tmp_path: Path, names: list[str]) -> Path:
    payload = {
        name: {
            "connection_id": name,
            "provider": name,
            "access_token": "a",
            "refresh_token": "r",
            "access_expires_at": "2099-01-01T00:00:00+00:00",
            "consent_expires_at": "2099-01-01T00:00:00+00:00",
            "scopes": "",
        }
        for name in names
    }
    path = tmp_path / "connections.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestKeepGoing:
    def test_Pull_WhenOneBankFails_StillPullsTheOthers(self, monkeypatch, tmp_path):
        store_path = _store_with(tmp_path, ["a-bank", "b-bank"])
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(store_path))
        pulled: list[str] = []

        def fake_pull(target, db_path, since, deep=False):
            pulled.append(target)
            return 1 if target == "a-bank" else 0

        monkeypatch.setattr("obdi.cli._pull", fake_pull)

        outcome = _pull_everything(tmp_path / "db.sqlite3", None)

        assert pulled == ["a-bank", "b-bank"], "a failure must not stop the loop"
        assert outcome == 1, "but the failure must still be reported in the exit code"

    def test_Pull_WhenStarlingIsConfigured_ItIsIncludedWithoutBeingStored(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(_store_with(tmp_path, ["a-bank"])))
        monkeypatch.setenv("STARLING_PERSONAL_ACCESS_TOKEN", "token")
        pulled: list[str] = []
        monkeypatch.setattr("obdi.cli._pull", lambda t, d, s, deep=False: pulled.append(t) or 0)

        _pull_everything(tmp_path / "db.sqlite3", None)

        assert pulled == ["a-bank", "starling"]


class TestUnhappyPaths:
    def test_Pull_WhenTheConnectionStoreIsUnreadable_FailsWithTwoNotACrash(
        self, monkeypatch, tmp_path
    ):
        broken = tmp_path / "connections.json"
        broken.write_text("not json at all", encoding="utf-8")
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(broken))

        assert _pull_everything(tmp_path / "db.sqlite3", None) == 2

    def test_Pull_WhenThereIsNothingToPull_SaysSoAndFails(self, monkeypatch, tmp_path, capsys):
        outcome = _pull_everything(tmp_path / "db.sqlite3", None)

        assert outcome == 1
        assert "Authorise a bank first" in capsys.readouterr().err
