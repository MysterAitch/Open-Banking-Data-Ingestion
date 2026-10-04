"""Every moment a feed item states is kept against the payment, as it was stated.

A real Starling feed item states three: when the payment was made, when it
settled, and when the bank last touched the record. The row keeps only one of
them as its date, so the others are held per sighting (`sighting_times`), found
by parsing every top-level field and not by a list of names. The aggregator, the
export, and the statements are not yet recorded.

KNOWN ANSWERS, decided before the first run (invented feed; amounts in pence):

    an item stating transactionTime, settlementTime, updatedAt, a date-only field
    and a field the bank might add later, beside amount, text, and a malformed date
        the row holds exactly those five moments, by name, with the text as stated,
        its kind, and the offset it carried; nothing else
    the same after a rebuild from raw
    a round-up leg derived from the item holds none
    an item the aggregator reported too: the feed's moments are the row's; the
        aggregator contributes none
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from obdi.providers import truelayer
from obdi.rebuild import rebuild_from_raw
from obdi.stated_times import days_of, london_date, settlement_days, stated_times
from obdi.store import SCHEMA_VERSION, Store
from round_up_corpus import SPACE_FEED_ORIGIN, card_payment, land_feed, round_up_of
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

STATED = {
    "transactionTime": "2026-09-14T10:18:00.000Z",
    "settlementTime": "2027-01-20T02:44:19.000Z",
    "updatedAt": "2027-01-20T02:44:25.123+00:00",
    "spendingDate": "2026-09-14",
    "reconciledAtTime": "2027-01-21T00:00:00Z",
}


def feed_with(**more):
    return card_payment("f-stated", "Abroad Shop", 4210, 14, **{**STATED, **more})


@pytest.fixture
def store(tmp_path):
    opened = Store(tmp_path / "times.sqlite3")
    land_evidence(opened)
    yield opened
    opened.close()


def row_of(store: Store, minor: int):
    (row,) = [
        t
        for t in store.transactions_for_account(MAIN)
        if t.amount_minor == minor and not t.source_id.endswith(":round-up")
    ]
    return row


def moments(store: Store, minor: int) -> dict[tuple[str, str], tuple[str, str, str]]:
    return {
        (t["source"], t["field"]): (t["stated"], t["kind"], t["zone"])
        for t in store.stated_times_for(row_of(store, minor).entity_id)
    }


class TestWhatAFeedItemStates:
    def test_Row_WhenTheFeedItemStatesSeveralMoments_KeepsEveryOneAsStated(self, store):
        land_feed(
            store,
            [feed_with(roundUp=None, note="not a moment", badDate="2026-13-45")],
            origin=FEED_ORIGIN,
        )
        rebuild_from_raw(store, account_map=MAP)

        assert moments(store, -4210) == {
            ("starling", "transactionTime"): ("2026-09-14T10:18:00.000Z", "instant", "Z"),
            ("starling", "settlementTime"): ("2027-01-20T02:44:19.000Z", "instant", "Z"),
            ("starling", "updatedAt"): ("2027-01-20T02:44:25.123+00:00", "instant", "+00:00"),
            ("starling", "spendingDate"): ("2026-09-14", "date", ""),
            ("starling", "reconciledAtTime"): ("2027-01-21T00:00:00Z", "instant", "Z"),
        }

    def test_Row_WhenTheBankAddsAFieldNobodyListed_ItIsKeptToo(self, store):
        land_feed(store, [feed_with(lastReviewedAt="2027-02-01T08:00:00Z")], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        assert ("starling", "lastReviewedAt") in moments(store, -4210)

    def test_Row_WhenTheItemStatesOnlyItsSettlement_HoldsThatOneAndIsStillCounted(self, store):
        bare = card_payment("f-bare", "Cafe", 350, 3)
        bare.pop("transactionTime")
        bare["settlementTime"] = "2026-09-03T10:00:00Z"
        land_feed(store, [bare], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        assert set(moments(store, -350)) == {("starling", "settlementTime")}

    def test_Rebuild_WhenRunAgain_HoldsTheSameMomentsAndNoMore(self, store):
        land_feed(store, [feed_with()], origin=FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)
        first = moments(store, -4210)

        rebuild_from_raw(store, account_map=MAP)

        assert moments(store, -4210) == first
        assert len(first) == len(STATED)


class TestWhoseMomentsAreKept:
    def test_RoundUpLeg_WhenDerivedFromAnItem_HoldsNoMomentsOfItsOwn(self, store):
        land_feed(store, [feed_with(roundUp=round_up_of(90))], origin=FEED_ORIGIN)
        land_feed(store, [], origin=SPACE_FEED_ORIGIN)
        rebuild_from_raw(store, account_map=MAP)

        (leg,) = [
            t for t in store.transactions_for_account(MAIN) if t.source_id.endswith(":round-up")
        ]
        assert store.stated_times_for(leg.entity_id) == []
        assert len(moments(store, -4210)) == len(STATED)

    def test_Row_WhenTheAggregatorAlsoReportedIt_KeepsOnlyTheFeedsMoments(self, store):
        land_feed(store, [feed_with()], origin=FEED_ORIGIN)
        record = {
            "transaction_id": "volatile-1",
            "normalised_provider_transaction_id": "tl-1",
            "timestamp": "2026-09-14T10:00:00Z",
            "description": "ABROAD SHOP",
            "amount": "-42.10",
            "currency": "GBP",
            "transaction_type": "DEBIT",
        }
        store.land_artefact(
            truelayer.artefact_for(
                json.dumps({"results": [record]}).encode(), account_id="tl-main", kind="booked"
            )
        )
        rebuild_from_raw(store, account_map=MAP)

        assert {source for source, _ in moments(store, -4210)} == {"starling"}
        assert "truelayer" in store.sources_for(row_of(store, -4210).entity_id)


class TestReadingMoments:
    def test_StatedTimes_WhenValuesAreNotMoments_AreNotRead(self):
        found = stated_times(
            {
                "a": "hello",
                "b": 5,
                "c": None,
                "d": "2026-09-14",
                "e": "2026-09-14T10:00:00",
                "f": "2026-9-4",
            }
        )

        assert [(t.field, t.kind, t.zone) for t in found] == [
            ("d", "date", ""),
            ("e", "instant", ""),
        ]

    def test_SettlementDays_WhenSettledAtMidnightInSummer_AreBothDaysInEitherZone(self):
        days = settlement_days({"settlementTime": "2026-07-01T23:30:00Z"})

        assert {d.isoformat() for d in days} == {"2026-07-01", "2026-07-02"}

    def test_SettlementDays_WhenSettledAtMidnightInWinter_AreOneDay(self):
        days = settlement_days({"settlementTime": "2026-12-01T23:30:00Z"})

        assert {d.isoformat() for d in days} == {"2026-12-01"}

    @pytest.mark.parametrize(
        ("instant", "local"),
        [
            ("2026-03-29T00:59:00Z", "2026-03-29"),
            ("2026-03-29T23:30:00Z", "2026-03-30"),
            ("2026-10-25T00:30:00Z", "2026-10-25"),
            ("2026-10-25T23:30:00Z", "2026-10-25"),
            ("2026-10-24T23:30:00Z", "2026-10-25"),
        ],
    )
    def test_LondonDate_AtTheClockChanges_FollowsTheLastSundays(self, instant, local):
        from datetime import datetime

        stated = datetime.fromisoformat(instant.replace("Z", "+00:00"))

        assert london_date(stated).isoformat() == local

    def test_SettlementDays_WhenNoneStated_AreEmpty(self):
        assert settlement_days({"transactionTime": "2026-09-14T10:00:00Z"}) == frozenset()
        assert days_of(stated_times({"settlementTime": "2026-09-14T10:00:00Z"})[0])


class TestUpgrade:
    def test_Store_OpenedAtTheVersionBeforeTheTable_GrowsItOnOpen(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE sighting_times")
        connection.execute(
            "UPDATE obdi_meta SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION - 1),),
        )
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert reopened.stated_times_for("no-such-row") == []
