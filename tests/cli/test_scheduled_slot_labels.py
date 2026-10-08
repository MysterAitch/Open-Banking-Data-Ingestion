"""A pull a person runs by hand in the scheduler's container must not move the scheduler's slot.

On 2026-10-03 to 04 the owner ran `docker exec obdi-pull obdi pull starling` and four pulls with
`--attended-from` inside the scheduler's container.
That container carries OBDI_TRIGGER=scheduled for everything run in it, so each was ledgered as
scheduled, and the scheduler's own bare pull then slept for most of a day:
"last scheduled cycle ran 161 min ago (minimum spacing 324 min) - ... waiting 162 min for the slot".

The rule is decided in `pull_trigger_label` and `standing_trigger_label`; these scenarios drive it
through the commands, with a ledger written the way the real pulls write it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from obdi.cli import (
    _await_scheduled_clearance,
    _pull,
    _pull_everything,
    main,
    scheduled_pull_skip_reason,
)
from obdi.ingest.pull import STARLING_CONNECTION, PullResult
from obdi.ingest.store import Store

ADDRESS = "198.51.100.7"
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _scheduler_container(monkeypatch, tmp_path):
    """The environment the scheduler's container gives every process inside it."""
    monkeypatch.setenv("OBDI_TRIGGER", "scheduled")
    monkeypatch.setenv("OBDI_PULL_INTERVAL_SECONDS", "21600")
    monkeypatch.setenv("OBDI_LOCKS_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("OBDI_PULL_MIN_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("OBDI_CONNECTION_STORE", raising=False)
    monkeypatch.setenv("STARLING_PERSONAL_ACCESS_TOKEN", "invented-token")


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "store.sqlite3"
    with Store(path):
        pass
    return path


def _starling_that_writes_its_ledger(monkeypatch, db: Path, when: datetime, seen: list[str]):
    """A stand-in for the Starling pull that ledgers an ask the way the real one does."""

    def fake(store, token, *, account_map, since, trigger, finishers):
        seen.append(trigger)
        store.record_attempt(
            source="starling",
            connection_id=STARLING_CONNECTION,
            account_ref="starling:acc",
            asked="window",
            request_meta=json.dumps({"trigger": trigger}, sort_keys=True),
            outcome="landed",
            now=when,
        )
        return PullResult(provider="starling")

    monkeypatch.setattr("obdi.cli.pull_starling", fake)


def _connection_store(tmp_path: Path, names: list[str]) -> Path:
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


class TestAHandRunPullDoesNotMoveTheSlot:
    def test_StarlingPull_RunByHandInTheSchedulersContainer_LeavesTheSlotOpen(
        self, monkeypatch, db
    ):
        seen: list[str] = []
        _starling_that_writes_its_ledger(monkeypatch, db, NOW - timedelta(minutes=10), seen)

        _pull("starling", db, None)

        assert seen == ["cli"]
        assert scheduled_pull_skip_reason(db, now=NOW) is None

    def test_StarlingPull_RunByTheLoopsBarePull_MovesTheSlot(self, monkeypatch, db):
        seen: list[str] = []
        _starling_that_writes_its_ledger(monkeypatch, db, NOW - timedelta(minutes=10), seen)

        _pull_everything(db, None)

        assert seen == ["scheduled"]
        reason = scheduled_pull_skip_reason(db, now=NOW)
        assert reason is not None
        assert "last scheduled cycle ran 10 min ago" in reason

    def test_AggregatorPull_NamedByHandAndAttended_IsLedgeredAttendedNotScheduled(
        self, monkeypatch, tmp_path, db
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(_connection_store(tmp_path, ["halifax"])))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "invented-secret")
        labels: list[str] = []

        def fake(store, connection, **kwargs):
            labels.append(kwargs["trigger"])
            return PullResult(provider="halifax")

        monkeypatch.setattr("obdi.cli.pull_truelayer", fake)

        _pull("halifax", db, None, psu_ip=ADDRESS)

        assert labels == ["cli-attended"]

    def test_AggregatorPull_NamedByHandWithNoAttendance_IsLedgeredAsTheCommandLine(
        self, monkeypatch, tmp_path, db
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(_connection_store(tmp_path, ["halifax"])))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "invented-secret")
        labels: list[str] = []
        monkeypatch.setattr(
            "obdi.cli.pull_truelayer",
            lambda store, connection, **kwargs: labels.append(kwargs["trigger"])
            or PullResult(provider="halifax"),
        )

        main(["--db", str(db), "pull", "halifax"])

        assert labels == ["cli"]

    def test_AggregatorPulls_WhenTheLoopRunsTheBarePull_AreEachLedgeredAsScheduled(
        self, monkeypatch, tmp_path, db
    ):
        monkeypatch.setenv(
            "OBDI_CONNECTION_STORE", str(_connection_store(tmp_path, ["halifax", "monzo"]))
        )
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "invented-secret")
        monkeypatch.delenv("STARLING_PERSONAL_ACCESS_TOKEN")
        labels: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "obdi.cli.pull_truelayer",
            lambda store, connection, **kwargs: labels.append(
                (connection.connection_id, kwargs["trigger"])
            )
            or PullResult(provider=connection.connection_id),
        )
        # A first cycle on an empty ledger has no slot to wait for.
        main(["--db", str(db), "pull"])

        assert labels == [("halifax", "scheduled"), ("monzo", "scheduled")]

    def test_PullFromThePage_KeepsItsOwnLabelWhateverTheEnvironmentSays(
        self, monkeypatch, tmp_path, db
    ):
        monkeypatch.setenv("OBDI_CONNECTION_STORE", str(_connection_store(tmp_path, ["halifax"])))
        monkeypatch.setenv("TRUELAYER_CLIENT_SECRET", "invented-secret")
        labels: list[str] = []
        monkeypatch.setattr(
            "obdi.cli.pull_truelayer",
            lambda store, connection, **kwargs: labels.append(kwargs["trigger"])
            or PullResult(provider="halifax"),
        )

        _pull("halifax", db, None, psu_ip=ADDRESS, trigger="web-fetch-now")
        _pull("halifax", db, None, trigger="web-extend")

        assert labels == ["web-fetch-now", "web-extend"]


class TestTheLedgerAlreadyHoldingHandRunPullsLabelledScheduled:
    """The ledger is evidence and is not relabelled, so the first cycle still sees them."""

    def _attempt(self, db: Path, at: datetime, trigger: str) -> None:
        with Store(db) as store:
            store.record_attempt(
                source="truelayer",
                connection_id="halifax",
                account_ref="acc",
                asked="window",
                request_meta=json.dumps({"trigger": trigger}),
                outcome="landed",
                now=at,
            )

    def test_FirstCycle_WhenAMislabelledHandRunPullIsRecent_StillWaitsForTheSlotItHolds(
        self, db
    ):
        self._attempt(db, NOW - timedelta(hours=9), "scheduled")
        self._attempt(db, NOW - timedelta(minutes=60), "scheduled")
        slept: list[float] = []

        class Moving:
            now = NOW

            def __call__(self) -> datetime:
                return self.now

        clock = Moving()

        def sleep(seconds: float) -> None:
            slept.append(seconds)
            clock.now += timedelta(seconds=seconds)

        outcome = _await_scheduled_clearance(db, sleep=sleep, clock=clock)

        assert outcome is None
        assert sum(slept) == (324 - 60) * 60

    def test_FirstCycle_WhenTheMislabelledPullIsOlderThanTheSpacing_DoesNotWait(self, db):
        self._attempt(db, NOW - timedelta(minutes=400), "scheduled")

        def explode(_seconds):
            raise AssertionError("the slot is open")

        assert _await_scheduled_clearance(db, sleep=explode, clock=lambda: NOW) is None

    def test_Slot_WhenOnlyHandRunPullsLabelledCliExist_IsOpen(self, db):
        self._attempt(db, NOW - timedelta(minutes=5), "cli")
        self._attempt(db, NOW - timedelta(minutes=5), "cli-attended")

        assert scheduled_pull_skip_reason(db, now=NOW) is None
