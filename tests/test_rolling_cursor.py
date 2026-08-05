"""The rolling cursor: incremental by default, loud when reality disagrees.

The design rests on the probe's demonstrated facts - update-time
semantics, inclusive millisecond boundary - and adds the discipline that
makes it safe to rely on: every routine ask deliberately overlaps the
previous anchor, so the anchor item's absence is an ALARM, not a quiet
gap. These tests drive the whole ladder: advance, canary miss, step
back, exhaust, sweep, and the sweep's own honesty about what the
incremental path missed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from obdi import cursor
from obdi.accounts import AccountMap
from obdi.pull import STARLING_CONNECTION, pull_starling
from obdi.store import Store


def _item(uid: str, txn: str, updated: str | None = None, minor: int = 100) -> dict:
    payload = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": txn,
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Tesco",
        "reference": "REF",
    }
    if updated:
        payload["updatedAt"] = updated
    return payload


class _FakeStarling:
    """A provider whose responses depend on the ask, like the real one."""

    def __init__(self, monkeypatch, full: list[dict]) -> None:
        self.full = full
        self.asks: list[object] = []
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_accounts",
            lambda token: (
                [{"accountUid": "acc-1", "defaultCategory": "cat-1"}],
                b'{"accounts": []}',
            ),
        )
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_categories",
            lambda token, uid: (
                [type("C", (), {"uid": "cat-1", "is_space": False, "name": "main"})()],
                b'{"spaces": []}',
            ),
        )
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_balance",
            lambda token, uid: b'{"clearedBalance": {}}',
        )
        monkeypatch.setattr(
            "obdi.providers.starling.fetch_identifiers",
            lambda token, uid: b'{"accountIdentifiers": []}',
        )

        def feed(token, account_uid, category_uid, since=None, since_at=None):
            self.asks.append(since_at)
            if since_at is None:
                got = list(self.full)
            else:
                got = [
                    item
                    for item in self.full
                    if self._update_of(item) >= since_at
                ]
            body = json.dumps({"feedItems": got}).encode()
            return got, body, f"changesSince={since_at or 'epoch'}"

        monkeypatch.setattr("obdi.providers.starling.fetch_feed", feed)

    @staticmethod
    def _update_of(item: dict) -> datetime:
        stamp = item.get("updatedAt") or item["transactionTime"]
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        # Mirror production: a naked stamp is treated as UTC (and flagged).
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pull(store: Store) -> list[str]:
    result = pull_starling(store, "token", account_map=AccountMap())
    return result.notes


class TestTheCursorLifecycle:
    def test_FirstPull_FetchesEverything_AndPlantsTheCursor(
        self, tmp_path, monkeypatch
    ):
        fake = _FakeStarling(
            monkeypatch,
            [
                _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
                _item("u-2", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
            ],
        )
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            planted = cursor.load(store, "acc-1", STARLING_CONNECTION)

        assert fake.asks == [None], "first pull must be a full fetch"
        assert planted is not None
        assert planted.anchor_uid == "u-2"

    def test_SecondPull_AsksSinceAnchorMinusBuffer_AndSeesTheCanary(
        self, tmp_path, monkeypatch
    ):
        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
            _item("u-2", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            # New traffic arrives; the anchor item is still served because
            # the overlap reaches behind it.
            fake.full.append(
                _item("u-3", "2026-08-03T09:00:00.000Z", "2026-08-03T09:00:00.000Z")
            )
            notes = _pull(store)
            advanced = cursor.load(store, "acc-1", STARLING_CONNECTION)

        incremental_ask = fake.asks[1]
        assert incremental_ask is not None, "second pull must be incremental"
        assert incremental_ask == datetime(
            2026, 8, 2, 11, 30, tzinfo=UTC
        ), "the ask is anchor minus the 30-minute buffer"
        assert not any("CANARY" in note for note in notes)
        assert advanced is not None and advanced.anchor_uid == "u-3"
        assert ("u-2", "2026-08-02T12:00:00.000Z") in advanced.history

    def test_AnAmendment_ArrivesThroughTheCursor(self, tmp_path, monkeypatch):
        """The reason update-time semantics were worth proving: a
        days-old transaction whose record changes is returned by a
        cursor that never looks that far back in transaction time."""
        items = [
            _item("u-old", "2026-07-20T10:00:00.000Z", "2026-07-20T10:00:00.000Z", 9900),
            _item("u-2", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            # The fuel pump settles: same uid, new content, new updatedAt.
            fake.full[0] = _item(
                "u-old", "2026-07-20T10:00:00.000Z", "2026-08-03T02:00:00.000Z", 2500
            )
            _pull(store)
            amount = store.connection.execute(
                "SELECT amount_minor FROM transactions WHERE source_id = 'u-old'"
            ).fetchone()[0]

        assert amount == -2500, "the amendment must supersede through the cursor"


class TestTheCanaryLadder:
    def test_AnchorVanishes_StepsBackToThePriorAnchor_Loudly(
        self, tmp_path, monkeypatch
    ):
        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
            _item("u-2", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)  # anchor u-2
            fake.full.append(
                _item("u-3", "2026-08-03T09:00:00.000Z", "2026-08-03T09:00:00.000Z")
            )
            _pull(store)  # anchor u-3, history holds u-2
            # The provider "removes" u-3 outright.
            fake.full = [item for item in fake.full if item["feedItemUid"] != "u-3"]
            notes = _pull(store)
            landed = cursor.load(store, "acc-1", STARLING_CONNECTION)

        assert any("CANARY MISS" in note for note in notes)
        # The prior anchor u-2 satisfied the ladder's second rung.
        assert landed is not None
        assert landed.anchor_uid == "u-2"

    def test_LadderExhausted_FallsBackToFullHistory_AndSaysSo(
        self, tmp_path, monkeypatch
    ):
        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
            _item("u-2", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            # EVERYTHING the cursor has ever anchored on disappears.
            fake.full = [
                _item("u-9", "2026-08-04T09:00:00.000Z", "2026-08-04T09:00:00.000Z")
            ]
            notes = _pull(store)

        assert any("CANARY LADDER EXHAUSTED" in note for note in notes)
        assert fake.asks[-1] is None, "the final ask must be full-history"


class TestTheSweep:
    def test_ASweep_ReportsWhatTheIncrementalPathMissed(
        self, tmp_path, monkeypatch
    ):
        """The tier must show its catches or it is superstition.

        An item that existed all along but never appeared in any
        incremental response (the exact shape a semantics change
        produces) surfaces in the sweep as a MISS, named loudly."""
        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)  # full; plants cursor + sweep stamp
            # Force the next pull to sweep.
            monkeypatch.setenv("OBDI_STARLING_SWEEP_DAYS", "1")
            cursor.stamp_sweep(store, "acc-1", STARLING_CONNECTION)
            store.record_provider_fact(
                "starling",
                STARLING_CONNECTION,
                "feed-last-sweep:acc-1",
                "2026-01-01T00:00:00Z",
            )
            # An old item the incremental path never delivered.
            fake.full.append(
                _item("u-ghost", "2026-07-01T10:00:00.000Z", "2026-07-01T10:00:00.000Z")
            )
            notes = _pull(store)

        assert any("SWEEP CAUGHT 1" in note and "u-ghost"[:8] in note for note in notes)

    def test_ACleanSweep_RaisesNoAlarm(self, tmp_path, monkeypatch):
        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
        ]
        _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            store.record_provider_fact(
                "starling",
                STARLING_CONNECTION,
                "feed-last-sweep:acc-1",
                "2026-01-01T00:00:00Z",
            )
            notes = _pull(store)

        assert not any("SWEEP CAUGHT" in note for note in notes)


class TestExplicitWindowsBypassTheCursor:
    def test_AnExplicitSince_NeitherUsesNorMovesTheCursor(
        self, tmp_path, monkeypatch
    ):
        from datetime import date

        items = [
            _item("u-1", "2026-08-01T10:00:00.000Z", "2026-08-01T10:00:00.000Z"),
        ]
        fake = _FakeStarling(monkeypatch, items)
        with Store(tmp_path / "s.sqlite3") as store:
            _pull(store)
            before = cursor.load(store, "acc-1", STARLING_CONNECTION)
            pull_starling(
                store,
                "token",
                account_map=AccountMap(),
                since=date(2026, 1, 1),
            )
            after = cursor.load(store, "acc-1", STARLING_CONNECTION)

        assert fake.asks[-1] is None, "explicit windows go through the date path"
        assert before == after, "a deliberate ask must not move the cursor"


class TestAnomalyInstrumentation:
    """Each check re-verifies a demonstrated fact on every routine cycle.

    The probe proved the semantics once; these turn "proved once" into
    "watched always", and each fires loudly rather than adapting
    silently - an anomaly absorbed is an anomaly institutionalised.
    """

    def _planted(self, tmp_path, monkeypatch, items):
        fake = _FakeStarling(monkeypatch, items)
        store = Store(tmp_path / "s.sqlite3")
        _pull(store)  # full fetch plants the cursor
        return fake, store

    def test_AFilterLeak_IsReportedNotAbsorbed(self, tmp_path, monkeypatch):
        """An item with an update stamp before the asked cutoff violates
        the demonstrated semantics - the retro-insertion case: a 5pm
        transaction materialising in a slot checked four times before."""
        items = [
            _item("u-1", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake, store = self._planted(tmp_path, monkeypatch, items)

        original_feed = fake.full

        def leaky_feed(token, account_uid, category_uid, since=None, since_at=None):
            fake.asks.append(since_at)
            got = [
                item
                for item in original_feed
                if since_at is None or fake._update_of(item) >= since_at
            ]
            # The provider returns something it should have excluded: a
            # stale-stamped record from long before the cutoff.
            got.append(
                _item("u-ghost", "2026-08-01T17:00:00.000Z", "2026-08-01T17:00:00.000Z")
            )
            body = json.dumps({"feedItems": got}).encode()
            return got, body, "changesSince=leaky"

        monkeypatch.setattr("obdi.providers.starling.fetch_feed", leaky_feed)
        notes = _pull(store)
        store.close()

        assert any(
            "ANOMALY FILTER LEAK" in note and "u-ghost"[:8] in note
            for note in notes
        ), notes

    def test_ANakedTimestamp_IsReported(self, tmp_path, monkeypatch):
        """The UTC assumption made monitorable: the day a stamp arrives
        without an offset, the pull says so instead of guessing."""
        items = [
            _item("u-1", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake, store = self._planted(tmp_path, monkeypatch, items)
        fake.full.append(
            _item("u-naked", "2026-08-02T13:00:00", "2026-08-02T13:00:00")
        )
        notes = _pull(store)
        store.close()

        assert any(
            "ANOMALY NAKED TIMESTAMP" in note and "u-naked"[:8] in note
            for note in notes
        ), notes

    def test_AMovedTransactionTime_IsReported(self, tmp_path, monkeypatch):
        """Amounts and statuses amend routinely; the economic date moving
        is the rare beast that reshuffles which day money left."""
        items = [
            _item("u-1", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake, store = self._planted(tmp_path, monkeypatch, items)
        # Same item, same uid - but its transactionTime moves a day.
        fake.full[0] = _item(
            "u-1", "2026-08-01T12:00:00.000Z", "2026-08-03T02:00:00.000Z"
        )
        notes = _pull(store)
        store.close()

        assert any(
            "ANOMALY TRANSACTION TIME MOVED" in note
            and "2026-08-02" in note
            and "2026-08-01" in note
            for note in notes
        ), notes

    def test_AWellBehavedCycle_RaisesNoAnomalies(self, tmp_path, monkeypatch):
        """The checks must be quiet when reality agrees - alarms that cry
        wolf teach the reader to skim past the one that matters."""
        items = [
            _item("u-1", "2026-08-02T12:00:00.000Z", "2026-08-02T12:00:00.000Z"),
        ]
        fake, store = self._planted(tmp_path, monkeypatch, items)
        fake.full.append(
            _item("u-2", "2026-08-03T09:00:00.000Z", "2026-08-03T09:00:00.000Z")
        )
        notes = _pull(store)
        store.close()

        assert not any("ANOMALY" in note for note in notes), notes
