"""A rebuild replays artefacts in the order they really arrived.

A pull stamps its artefact in UTC and a file import in local time with an
offset, and the stamps are stored as text. Compared as text, an import made at
11:00 UTC but stamped "12:00+01:00" sorts AFTER a pull made at 11:30 UTC, so the
rebuild replayed it after a pull it had actually preceded. Last writer wins on a
merged row, so the replayed store could disagree with the live one about which
source's facts the row carries.

The answer is fixed before the first run: three artefacts, one payment, and the
row carries the facts of whichever arrived LAST.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from obdi.core.models import RawArtefact
from obdi.ingest.arrival_order import in_arrival_order
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store

ACCOUNT = "halifax-current"
LOCAL = timezone(timedelta(hours=1))

CSV = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes\n"
    b"14/03/2026,Tesco,TESCO STORES,CARD,-14.99,1200.00,GROCERIES,\n"
)


def _aggregator(when: datetime) -> RawArtefact:
    record = {
        "transaction_id": "volatile-1",
        "normalised_provider_transaction_id": "tl-1",
        "timestamp": "2026-03-14T00:00:00Z",
        "description": "TESCO STORES 4912",
        "amount": -14.99,
        "currency": "GBP",
        "transaction_type": "DEBIT",
    }
    body = json.dumps({"results": [record], "status": "Succeeded"}).encode()
    artefact = truelayer.artefact_for(body, account_id="tl-1", kind="booked", account_ref=ACCOUNT)
    return RawArtefact(**{**artefact.__dict__, "fetched_at": when})


def _export(when: datetime) -> RawArtefact:
    return RawArtefact(
        source="csv",
        account_ref=ACCOUNT,
        fetched_at=when,
        media_type="text/csv",
        digest="export-digest",
        payload=CSV,
        origin="export.csv",
    )


def _sources_of_rows(store: Store) -> list[str]:
    return [str(row[0]) for row in store.connection.execute("SELECT source FROM transactions")]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


class TestRebuildReplaysByInstantNotByText:
    def test_ExportStampedWithAnOffset_ArrivingBeforeAPullInUtc_IsReplayedBeforeIt(self, store):
        """11:00 UTC written as 12:00+01:00 sorts after 11:30 UTC as text.
        The pull arrived last, so the row carries the pull's facts."""
        store.land_artefact(_export(datetime(2026, 3, 20, 12, 0, tzinfo=LOCAL)))
        store.land_artefact(_aggregator(datetime(2026, 3, 20, 11, 30, tzinfo=UTC)))

        rebuild_from_raw(store)

        assert _sources_of_rows(store) == ["truelayer"]

    def test_ExportArrivingAfterAPull_IsReplayedAfterIt(self, store):
        """Where text and instant agree, nothing moves: 12:30+01:00 is 11:30
        UTC, after the 11:00 UTC pull."""
        store.land_artefact(_aggregator(datetime(2026, 3, 20, 11, 0, tzinfo=UTC)))
        store.land_artefact(_export(datetime(2026, 3, 20, 12, 30, tzinfo=LOCAL)))

        rebuild_from_raw(store)

        assert _sources_of_rows(store) == ["starling-csv"]

    def test_TwoArtefactsAtTheSameInstant_KeepTheOrderTheyLanded(self, store):
        """The same instant written with two offsets ties, and the tie falls
        to arrival order rather than to the text, which would put the pull
        first."""
        store.land_artefact(_export(datetime(2026, 3, 20, 12, 0, tzinfo=LOCAL)))
        store.land_artefact(_aggregator(datetime(2026, 3, 20, 11, 0, tzinfo=UTC)))

        rebuild_from_raw(store)

        assert _sources_of_rows(store) == ["truelayer"]

    def test_TwoArtefactsAtTheSameInstant_LandedTheOtherWay_KeepThatOrderToo(self, store):
        """The opposite tie: the pull landed first, so the export is replayed
        last, although as text the export would sort first."""
        store.land_artefact(_aggregator(datetime(2026, 3, 20, 11, 0, tzinfo=UTC)))
        store.land_artefact(_export(datetime(2026, 3, 20, 12, 0, tzinfo=LOCAL)))

        rebuild_from_raw(store)

        assert _sources_of_rows(store) == ["starling-csv"]


class TestInArrivalOrder:
    @staticmethod
    def _row(rowid: int, stamp: str) -> dict[str, object]:
        return {"rowid": rowid, "fetched_at": stamp}

    def test_StampWithNoOffset_IsReadAsUtc(self):
        rows = [
            self._row(1, "2026-03-20T12:00:00+01:00"),
            self._row(2, "2026-03-20T11:30:00"),
        ]

        assert [row["rowid"] for row in in_arrival_order(rows)] == [1, 2]

    def test_StampsOneMicrosecondApart_AreOrderedByTheMicrosecond(self):
        rows = [
            self._row(1, "2026-03-20T11:00:00.000002+00:00"),
            self._row(2, "2026-03-20T11:00:00.000001+00:00"),
        ]

        assert [row["rowid"] for row in in_arrival_order(rows)] == [2, 1]

    def test_StampWithAWholeSecond_SortsBeforeOneWithFractionsInThatSecond(self):
        rows = [
            self._row(1, "2026-03-20T11:00:00.500000+00:00"),
            self._row(2, "2026-03-20T11:00:00+00:00"),
        ]

        assert [row["rowid"] for row in in_arrival_order(rows)] == [2, 1]

    def test_StampAcrossAClockChange_IsOrderedByInstantNotByWallClock(self):
        """On the night the clocks go back, 01:30 BST (+01:00) is 00:30 UTC,
        before 01:10 GMT (+00:00), though the text sorts 01:10 first."""
        rows = [
            self._row(1, "2026-10-25T01:10:00+00:00"),
            self._row(2, "2026-10-25T01:30:00+01:00"),
        ]

        assert [row["rowid"] for row in in_arrival_order(rows)] == [2, 1]
