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


class TestConfiguredMeansResolvable:
    """A set variable is not a usable token, and the difference is six-hourly noise.

    The deployment always sets the _FILE variable; what may be absent is the
    file. Attempting Starling anyway prints a failure every cycle until the
    token exists - a standing wolf-cry that trains the reader to skim past the
    line that will one day matter.
    """

    def test_Pull_WhenTheTokenFileDoesNotExist_SkipsStarlingWithANoteNotAFailure(
        self, monkeypatch, tmp_path, capsys
    ):
        store_path = _store_with(tmp_path, ["a-bank"])
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(store_path))
        monkeypatch.setenv(
            "STARLING_PERSONAL_ACCESS_TOKEN_FILE", str(tmp_path / "absent-token")
        )
        pulled: list[str] = []
        monkeypatch.setattr("obdi.cli._pull", lambda t, d, s, deep=False: pulled.append(t) or 0)

        outcome = _pull_everything(tmp_path / "db.sqlite3", None)

        assert pulled == ["a-bank"], "the other banks must still be pulled"
        assert outcome == 0, "an unconfigured token is not a pull failure"
        err = capsys.readouterr().err
        assert "starling" in err.casefold(), "but the skip must be said, once, plainly"

    def test_Pull_WhenNothingResolvesAtAll_StillReportsNothingToPull(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv(
            "STARLING_PERSONAL_ACCESS_TOKEN_FILE", str(tmp_path / "absent-token")
        )

        assert _pull_everything(tmp_path / "db.sqlite3", None) == 1


class TestBindCommand:
    """Binding writes the map for the future and moves the rows from the past."""

    def test_Bind_WritesAMapEntryTheLoaderAccepts_AndMovesStoredRows(
        self, monkeypatch, tmp_path
    ):
        from datetime import date as date_type

        from obdi.cli import _account_map, main
        from obdi.ingest import reconcile_batch
        from obdi.models import SourceTier, Transaction
        from obdi.store import Store

        db = tmp_path / "store.sqlite3"
        monkeypatch.setenv("OBDI_DB_PATH", str(db))
        monkeypatch.setenv("OBDI_ACCOUNT_MAP", str(tmp_path / "accounts.json"))
        monkeypatch.setattr("obdi.cli.load_dotenv", lambda *a, **k: None)

        with Store(db) as store:
            reconcile_batch(
                store,
                [
                    Transaction(
                        account_id="truelayer:e9f8",
                        amount_minor=-100,
                        currency="GBP",
                        value_date=date_type(2026, 3, 5),
                        booking_date=date_type(2026, 3, 5),
                        description="X",
                        source="truelayer",
                        source_id="t1",
                        tier=SourceTier.AUTHORITATIVE,
                        content_key="k1",
                    )
                ],
                digest="d1",
            )

        assert main(["bind", "truelayer", "e9f8", "halifax-current"]) == 0

        # The loader must accept what bind wrote - a map the app cannot read
        # is worse than no map.
        resolved = _account_map().resolve("truelayer", "e9f8")
        assert resolved == "halifax-current"

        with Store(db) as store:
            assert {t.account_id for t in store.all_transactions()} == {"halifax-current"}
