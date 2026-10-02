"""One payment is one row, and two payments are two, whoever reports them and when.

Two faults in the merged layer came from one cause: a row carried only the
provider id of the LAST source to observe it.

A payment could be held TWICE. Once a second source had observed a row, the
first source's id was no longer on it, so when the first source re-reported the
payment with a changed amount it found nothing to match and became a second row.
The deployed store showed 4,750 provider ids against 4,758 rows on one account.

A payment could be FOLDED AWAY. A source's pending record was allowed to match
any settled row of the same amount within a week, so a second, different
payment replaced the first. A weekly fixed-price payment collapsed to its
latest instance.

Every scenario below states how many payments were really made. That number is
the number of live rows expected, decided before the first run.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from obdi.identity import content_key
from obdi.identity_health import identity_health
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store

ACCOUNT = "everyday"
PENDING = TransactionStatus.PENDING
BOOKED = TransactionStatus.BOOKED


def payment(
    source: str,
    source_id: str | None,
    minor: int,
    *,
    day: int = 5,
    status: TransactionStatus = BOOKED,
    description: str = "EXAMPLE CAFE",
) -> Transaction:
    when = date(2026, 9, day)
    return Transaction(
        account_id=ACCOUNT,
        amount_minor=minor,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        content_key=content_key(amount_minor=minor, value_date=when, description=description),
        tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
        status=status,
    )


def land(tmp_path, *batches: list[Transaction]) -> list[tuple[str, str | None, int, str]]:
    """Each batch as its own arrival, then the live rows that result."""
    with Store(tmp_path / "s.sqlite3") as store:
        for number, batch in enumerate(batches):
            reconcile_batch(store, batch, digest=f"digest-{number}")
        return live_rows(store)


def live_rows(store: Store) -> list[tuple[str, str | None, int, str]]:
    return [
        (row[0], row[1], row[2], row[3])
        for row in store.connection.execute(
            "SELECT source, source_id, amount_minor, status FROM transactions "
            "WHERE status != 'void' ORDER BY value_date, rowid"
        )
    ]


class TestAPaymentReportedAgainIsStillOneRow:
    def test_FeedAmendsTheAmount_AfterASecondSourceSawThePayment_StillOneRow(self, tmp_path):
        """One payment. The feed reports it, an aggregator reports it, then the
        feed corrects the amount."""
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -1000)],
            [payment("truelayer", "tl-1", -1000)],
            [payment("starling", "uid-1", -1200)],
        )

        assert len(rows) == 1
        assert rows[0][2] == -1200

    def test_FeedAmendsTheAmount_AfterAnExportSawThePayment_StillOneRow(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -1000)],
            [payment("starling-csv", None, -1000)],
            [payment("starling", "uid-1", -1200)],
        )

        assert len(rows) == 1
        assert rows[0][2] == -1200

    def test_PendingSettlesAtAnAmendedAmount_AfterASecondSourceSawIt_StillOneRow(
        self, tmp_path
    ):
        """A tap-in held at a nominal amount, then settled at the real fare."""
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -100, status=PENDING)],
            [payment("truelayer", "tl-1", -100)],
            [payment("starling", "uid-1", -275)],
        )

        assert len(rows) == 1
        assert rows[0][2] == -275 and rows[0][3] == "booked"

    def test_FeedRepeatsThePaymentUnchanged_AfterASecondSourceSawIt_StillOneRow(
        self, tmp_path
    ):
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -1000)],
            [payment("truelayer", "tl-1", -1000)],
            [payment("starling", "uid-1", -1000)],
        )

        assert len(rows) == 1

    def test_FeedAmendsTheAmount_WithNoSecondSource_StillOneRow(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -1000)],
            [payment("starling", "uid-1", -1200)],
        )

        assert len(rows) == 1
        assert rows[0][2] == -1200

    def test_TwoSourcesReportingOnePayment_AreStillMergedIntoOneRow(self, tmp_path):
        """The cross-source merge is the point of holding several sources."""
        rows = land(
            tmp_path,
            [payment("starling", "uid-1", -1000)],
            [payment("truelayer", "tl-1", -1000)],
        )

        assert len(rows) == 1


class TestTwoPaymentsFromOneSourceAreTwoRows:
    def test_SettledPayment_ThenADifferentPendingOneOfTheSamePriceNextDay_BothKept(
        self, tmp_path
    ):
        """Two payments, a day apart, same price, different ids from one feed
        whose ids do not change at settlement."""
        rows = land(
            tmp_path,
            [payment("starling", "fare-1", -250, day=14)],
            [payment("starling", "fare-2", -250, day=15, status=PENDING)],
            [payment("starling", "fare-2", -250, day=15)],
        )

        assert [row[1] for row in rows] == ["fare-1", "fare-2"]

    def test_PendingPayment_ThenADifferentSettledOne_BothKept(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("starling", "fare-2", -250, day=15, status=PENDING)],
            [payment("starling", "fare-1", -250, day=14)],
        )

        assert sorted(row[1] or "" for row in rows) == ["fare-1", "fare-2"]

    def test_SettledPayment_ThenAPendingOneAtADifferentShop_BothKept(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("starling", "cafe-1", -250, day=14)],
            [
                payment(
                    "starling", "shop-2", -250, day=15, status=PENDING,
                    description="EXAMPLE BAKERY",
                )
            ],
        )

        assert len(rows) == 2

    def test_WeeklyFare_EachSeenPendingThenSettled_AllThreeKept(self, tmp_path):
        """Three payments a week apart, replayed from the feed's own responses.
        Before this was fixed the store held one row, the latest."""

        def fare(uid: str, status: str, day: int) -> dict:
            return {
                "feedItemUid": uid,
                "amount": {"currency": "GBP", "minorUnits": 250},
                "direction": "OUT",
                "transactionTime": f"2026-09-{day:02}T09:15:00.000Z",
                "source": "MASTER_CARD",
                "status": status,
                "counterPartyName": "Example Buses",
                "reference": "REF",
            }

        responses = [
            [fare("w-1", "PENDING", 1)],
            [fare("w-1", "SETTLED", 1)],
            [fare("w-2", "PENDING", 8)],
            [fare("w-2", "SETTLED", 8)],
            [fare("w-3", "PENDING", 15)],
            [fare("w-3", "SETTLED", 15)],
        ]
        with Store(tmp_path / "s.sqlite3") as store:
            for cycle, items in enumerate(responses):
                store.land_artefact(
                    starling.artefact_for(
                        json.dumps({"feedItems": items}).encode(),
                        account_id="starling:cat-1",
                        kind="feed",
                        origin=f"https://api.example.com/feed/cat-1?c={cycle}",
                    )
                )
            rebuild_from_raw(store)
            rows = live_rows(store)
            report = identity_health(store)

        assert [row[1] for row in rows] == ["w-1", "w-2", "w-3"]
        assert (report.folded, report.surplus) == (0, 0)


class TestSettlementRunsOneWay:
    """An aggregator names a payment one way while it is pending and another
    once it has settled, so its two ids on one row are legitimate."""

    def test_PendingThenSettledUnderANewId_IsOnePayment(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("truelayer", "pend-1", -1000, status=PENDING)],
            [payment("truelayer", "book-1", -1000)],
        )

        assert rows == [("truelayer", "book-1", -1000, "booked")]

    def test_SettledPayment_ThenItsOwnStalePendingRecord_StaysSettled(self, tmp_path):
        """The pending list can lag: it still names a payment that has already
        settled. That is one payment, and it does not go back to pending."""
        rows = land(
            tmp_path,
            [payment("truelayer", "book-1", -1000)],
            [payment("truelayer", "pend-1", -1000, status=PENDING)],
        )

        assert rows == [("truelayer", "book-1", -1000, "booked")]

    def test_StalePendingRecord_SeenAgainAfterSettlement_StillOneSettledRow(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("truelayer", "pend-1", -1000, status=PENDING)],
            [payment("truelayer", "book-1", -1000)],
            [payment("truelayer", "pend-1", -1000, status=PENDING)],
        )

        assert rows == [("truelayer", "book-1", -1000, "booked")]

    def test_SettledPayment_ThenAPendingOneDatedLater_IsASecondPayment(self, tmp_path):
        """A pending record cannot be the precursor of something that settled
        before it was made."""
        rows = land(
            tmp_path,
            [payment("truelayer", "book-1", -1000, day=5)],
            [payment("truelayer", "pend-2", -1000, day=6, status=PENDING)],
        )

        assert len(rows) == 2
        assert {row[3] for row in rows} == {"booked", "pending"}

    def test_TwoSameDayPayments_SecondSeenPendingAfterTheFirstSettled_EndAsTwoRows(
        self, tmp_path
    ):
        """Two coffees on one day. While the second is pending it cannot be
        told from a stale record of the first; once it settles under its own
        id there are two rows."""
        rows = land(
            tmp_path,
            [payment("truelayer", "book-1", -350)],
            [payment("truelayer", "pend-2", -350, status=PENDING)],
            [payment("truelayer", "book-2", -350)],
        )

        assert sorted(row[1] or "" for row in rows) == ["book-1", "book-2"]

    def test_TwoSettledRecordsWithDifferentIds_AreTwoPayments(self, tmp_path):
        rows = land(
            tmp_path,
            [payment("truelayer", "book-1", -350)],
            [payment("truelayer", "book-2", -350)],
        )

        assert len(rows) == 2


def ids_by_day(tmp_path, *batches: list[Transaction]) -> list[tuple[str | None, str]]:
    """(provider id, value date) of each live row after the batches land."""
    with Store(tmp_path / "s.sqlite3") as store:
        for number, batch in enumerate(batches):
            reconcile_batch(store, batch, digest=f"digest-{number}")
        return [
            (row[0], row[1])
            for row in store.connection.execute(
                "SELECT source_id, value_date FROM transactions "
                "WHERE status != 'void' ORDER BY value_date, rowid"
            )
        ]


class TestTwoIdsListedInOneResponseAreTwoPayments:
    """A provider that lists two ids side by side is saying two payments.

    Found on the deployed store the day rows began to remember every id they
    had been called: one aggregator id had no row of its own, and the
    provider had listed it in one response beside the id holding the row.
    An earlier wrong merge is ordinary and used to heal itself, because the
    row forgot the first id and the first payment made a row again.
    Remembering both ids made the merge permanent: each id found the same row.

    Two payments throughout: the same price, two days apart, from an
    aggregator that gives a payment a new id when it settles.
    """

    FIRST = payment("truelayer", "tl-1", -1000, day=5)
    SECOND = payment("truelayer", "tl-2", -1000, day=7)

    @staticmethod
    def wrongly_merged() -> list[list[Transaction]]:
        return [
            [payment("truelayer", "tl-1", -1000, day=5, status=PENDING)],
            # Looks exactly like the first one settling under a new id, and is not.
            [payment("truelayer", "tl-2", -1000, day=7)],
        ]

    def test_BothListedTogetherAfterAWrongMerge_EndAsTwoRows(self, tmp_path):
        rows = ids_by_day(tmp_path, *self.wrongly_merged(), [self.FIRST, self.SECOND])

        assert rows == [("tl-1", "2026-09-05"), ("tl-2", "2026-09-07")]

    def test_BothListedTogetherInTheOtherOrder_EndAsTwoRows(self, tmp_path):
        rows = ids_by_day(tmp_path, *self.wrongly_merged(), [self.SECOND, self.FIRST])

        assert rows == [("tl-1", "2026-09-05"), ("tl-2", "2026-09-07")]

    def test_ListedTogetherAgainAndAgain_EachRowKeepsItsOwnPayment(self, tmp_path):
        """Stable, not swapping: a later response must not hand each row the
        other payment's facts."""
        together = [self.FIRST, self.SECOND]
        reversed_order = [self.SECOND, self.FIRST]

        once = ids_by_day(tmp_path / "once", *self.wrongly_merged(), together)
        thrice = ids_by_day(
            tmp_path / "thrice", *self.wrongly_merged(), together, together, reversed_order
        )

        assert once == thrice == [("tl-1", "2026-09-05"), ("tl-2", "2026-09-07")]

    def test_OneIdListedTwiceInAResponse_IsStillOnePayment(self, tmp_path):
        """The opposite case: the rule is about DIFFERENT ids."""
        rows = ids_by_day(tmp_path, [self.FIRST, self.FIRST])

        assert rows == [("tl-1", "2026-09-05")]

    def test_AGenuineSettlementUnderANewId_ListedAlone_IsStillOnePayment(self, tmp_path):
        """The merge this rule must not take away: one payment, reissued."""
        rows = ids_by_day(
            tmp_path,
            [payment("truelayer", "tl-1", -1000, day=5, status=PENDING)],
            [payment("truelayer", "tl-2", -1000, day=5)],
            [payment("truelayer", "tl-2", -1000, day=5)],
        )

        assert rows == [("tl-2", "2026-09-05")]

    def test_TwoSourcesListingOnePaymentInTheirOwnResponses_AreStillOneRow(self, tmp_path):
        """Different sources disagree about ids by design; only one source's
        own two ids in one response are two payments."""
        rows = ids_by_day(
            tmp_path,
            [payment("starling", "uid-1", -1000, day=5)],
            [payment("truelayer", "tl-1", -1000, day=5)],
            [payment("starling", "uid-1", -1000, day=5)],
        )

        assert len(rows) == 1


class TestLiveIngestAndARebuildAgree:
    def test_SameArrivals_WhetherHistoryIsCachedOrReloadedPerBatch_GiveTheSameRows(
        self, tmp_path, monkeypatch
    ):
        """A rebuild keeps an account's history in memory across responses and
        a live pull reloads it each time. What the row has been sighted under
        must be the same either way."""
        import obdi.rebuild as rebuild_mod

        def item(uid: str, status: str, day: int, minor: int = 250) -> dict:
            return {
                "feedItemUid": uid,
                "amount": {"currency": "GBP", "minorUnits": minor},
                "direction": "OUT",
                "transactionTime": f"2026-09-{day:02}T09:15:00.000Z",
                "source": "MASTER_CARD",
                "status": status,
                "counterPartyName": "Example Buses",
                "reference": "REF",
            }

        responses = [
            [item("w-1", "PENDING", 1)],
            [item("w-1", "SETTLED", 1)],
            [item("w-2", "PENDING", 3)],
            [item("w-2", "SETTLED", 3), item("w-1", "SETTLED", 1, minor=275)],
            [item("w-3", "SETTLED", 5)],
        ]
        results = {}
        for label in ("cached", "reloading"):
            with Store(tmp_path / f"{label}.sqlite3") as store:
                for cycle, items in enumerate(responses):
                    store.land_artefact(
                        starling.artefact_for(
                            json.dumps({"feedItems": items}).encode(),
                            account_id="starling:cat-1",
                            kind="feed",
                            origin=f"https://api.example.com/feed/cat-1?c={cycle}",
                        )
                    )
                if label == "reloading":
                    original = rebuild_mod.reconcile_batch

                    def per_batch(store_, transactions, _original=original, **kwargs):
                        kwargs.pop("candidate_cache", None)
                        return _original(store_, transactions, **kwargs)

                    monkeypatch.setattr(rebuild_mod, "reconcile_batch", per_batch)
                rebuild_from_raw(store)
                results[label] = live_rows(store)
                monkeypatch.undo()

        assert results["cached"] == results["reloading"]
        assert [row[1] for row in results["cached"]] == ["w-1", "w-2", "w-3"]
        assert results["cached"][0][2] == -275


@pytest.mark.parametrize("source", ["starling"])
def test_SourceWhoseIdsSurviveSettlement_IsDeclaredAsSuch(source):
    """The rule that keeps two of a source's ids apart applies only where a
    different id always means a different payment."""
    from obdi.matching import SETTLEMENT_KEEPS_ID

    assert source in SETTLEMENT_KEEPS_ID
    assert "truelayer" not in SETTLEMENT_KEEPS_ID
