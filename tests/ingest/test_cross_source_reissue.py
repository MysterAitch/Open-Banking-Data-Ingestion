"""An aggregator's second payment is never folded into its first by a later sighting.

A row carries the source of the LAST writer, so once an export has sighted an
aggregator's payment the aggregator no longer looks like the row's source. A
second payment of the same price from the aggregator, under a different id, then
read as a cross-source candidate and merged on amount and date alone: one
payment gone from every sum, and nothing flagged.

Every scenario states how many payments were really made, and what they sum to,
before the first run. Two payments of 14.99 are two rows totalling -29.98.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

from landing import import_file, rebuild_from_raw
from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.accounts import AccountBinding, AccountMap
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import truelayer
from obdi.ingest.store import Store

ACCOUNT = "halifax-current"
FARES_TOTAL = -2998

CSV_HEADER = (
    b"Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes\n"
)


def _record(tid: str, day: int, *, description: str = "TESCO STORES 4912") -> dict[str, object]:
    return {
        "transaction_id": f"volatile-{tid}",
        "normalised_provider_transaction_id": tid,
        "timestamp": f"2026-03-{day:02}T00:00:00Z",
        "description": description,
        "amount": -14.99,
        "currency": "GBP",
        "transaction_type": "DEBIT",
    }


def _from_aggregator(
    tid: str, day: int, *, pending: bool = False, description: str = "TESCO STORES 4912"
) -> Transaction:
    return truelayer.to_transaction(
        _record(tid, day, description=description), account_id=ACCOUNT, pending=pending
    )


def _export_sighting(store: Store, tmp_path: Path, day: int = 14) -> None:
    path = tmp_path / f"export-{day}.csv"
    row = f"{day:02}/03/2026,Tesco,TESCO STORES,CARD,-14.99,1200.00,GROCERIES,\n"
    path.write_bytes(CSV_HEADER + row.encode())
    import_file(store, path, account_id=ACCOUNT)


def _land(store: Store, *batches: list[Transaction]) -> None:
    for number, batch in enumerate(batches):
        reconcile_batch(store, batch, digest=f"digest-{number}")


def _held(store: Store) -> tuple[int, int]:
    rows = store.connection.execute(
        "SELECT amount_minor FROM transactions WHERE status != 'void'"
    ).fetchall()
    return len(rows), sum(int(row[0]) for row in rows)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


@pytest.mark.parametrize("second_day", [14, 15, 19], ids=["same-day", "next-day", "five-days-on"])
@pytest.mark.parametrize("second_description", ["TESCO STORES 4912", "CORNER SHOP LEEDS"])
class TestASecondAggregatorPaymentAfterAnExportSightedTheFirst:
    def test_AggregatorPaymentsSightedByAnExportBetween_StayTwoRows(
        self, store, tmp_path, second_day, second_description
    ):
        _land(store, [_from_aggregator("tl-1", 14)])
        _export_sighting(store, tmp_path)
        _land(store, [_from_aggregator("tl-2", second_day, description=second_description)])

        assert _held(store) == (2, FARES_TOTAL)

    def test_ExportArrivingFirst_ThenBothAggregatorPayments_StayTwoRows(
        self, store, tmp_path, second_day, second_description
    ):
        _export_sighting(store, tmp_path)
        _land(store, [_from_aggregator("tl-1", 14)])
        _land(store, [_from_aggregator("tl-2", second_day, description=second_description)])

        assert _held(store) == (2, FARES_TOTAL)

    def test_ResponseListingBothIds_StaysTwoRows(
        self, store, tmp_path, second_day, second_description
    ):
        _land(store, [_from_aggregator("tl-1", 14)])
        _export_sighting(store, tmp_path)
        _land(
            store,
            [
                _from_aggregator("tl-2", second_day, description=second_description),
                _from_aggregator("tl-1", 14),
            ],
        )

        assert _held(store) == (2, FARES_TOTAL)


class TestTheRowIsFlaggedWhenTheEvidenceCannotSeparateOnePaymentFromTwo:
    def test_SecondAggregatorPayment_HeldApartFromARowAnExportSighted_IsQueuedForReview(
        self, store, tmp_path
    ):
        _land(store, [_from_aggregator("tl-1", 14)])
        _export_sighting(store, tmp_path)
        _land(store, [_from_aggregator("tl-2", 15)])

        assert len(store.review_queue()) == 1

    def test_SecondAggregatorPayment_OnAnAccountNoExportSighted_IsQueuedAsBefore(self, store):
        _land(store, [_from_aggregator("tl-1", 14)], [_from_aggregator("tl-2", 15)])

        assert _held(store) == (2, FARES_TOTAL)
        assert len(store.review_queue()) == 1


class TestAHandEnteredRowDoesNotAbsorbAnAggregatorsSecondPayment:
    @staticmethod
    def _typed(day: int = 14) -> Transaction:
        when = date(2026, 3, day)
        return Transaction(
            account_id=ACCOUNT,
            amount_minor=-1499,
            value_date=when,
            booking_date=when,
            description="Tesco",
            source="manual",
            source_id=None,
            content_key=content_key(amount_minor=-1499, value_date=when, description="Tesco"),
            tier=SourceTier.MANUAL,
            status=TransactionStatus.BOOKED,
        )

    def test_ManualEntryClaimsTheFirstPayment_SecondAggregatorPaymentStaysSeparate(self, store):
        _land(
            store,
            [_from_aggregator("tl-1", 14)],
            [self._typed()],
            [_from_aggregator("tl-2", 17)],
        )

        assert _held(store) == (2, FARES_TOTAL)

    def test_ManualEntryFirst_ThenOneAggregatorPayment_IsStillOneRow(self, store):
        """The permissive claim a typed record exists for is untouched."""
        _land(store, [self._typed()], [_from_aggregator("tl-1", 17)])

        assert _held(store) == (1, -1499)


class TestTheBanksOwnFeedWasAlreadySafe:
    def test_FeedPaymentsSightedByAnExportBetween_StayTwoRows(self, store, tmp_path):
        from obdi.ingest.providers import starling

        def item(uid: str, day: int) -> Transaction:
            return starling.to_transaction(
                {
                    "feedItemUid": uid,
                    "amount": {"currency": "GBP", "minorUnits": 1499},
                    "direction": "OUT",
                    "transactionTime": f"2026-03-{day:02}T09:15:00.000Z",
                    "source": "MASTER_CARD",
                    "status": "SETTLED",
                    "counterPartyName": "Tesco",
                    "reference": "TESCO STORES",
                },
                account_id=ACCOUNT,
            )

        _land(store, [item("uid-1", 14)])
        _export_sighting(store, tmp_path)
        _land(store, [item("uid-2", 17)])

        assert _held(store) == (2, FARES_TOTAL)


class TestSettlementRenumberingStillMakesOnePayment:
    def test_SecondPaymentPendingThenSettledUnderANewId_AfterAnExportSightedTheFirst_IsOneRowEach(
        self, store, tmp_path
    ):
        _land(store, [_from_aggregator("tl-1", 14)])
        _export_sighting(store, tmp_path)
        _land(
            store,
            [_from_aggregator("pend-2", 17, pending=True)],
            [_from_aggregator("book-2", 17)],
        )

        assert _held(store) == (2, FARES_TOTAL)

    def test_PaymentPendingThenSettledUnderANewId_WithNoExportBetween_IsOneRow(self, store):
        _land(
            store,
            [_from_aggregator("pend-1", 14, pending=True)],
            [_from_aggregator("book-1", 14)],
        )

        assert _held(store) == (1, -1499)


class TestAggregatorSettlementThroughTheBanksOwnFeedOrAnExport:
    """The mainstream path: one card payment, two API sources, ids that change.

    The aggregator names a payment one way while pending and another once
    settled; the bank's own feed (or an export) sights it in between. One
    payment is one row whichever order they arrive in, because the aggregator's
    first id was only ever seen in a pending snapshot. Holding the settled id
    apart from it counted one payment twice.

    Every sequence is pulled as the pulls pull: the artefact landed, then
    resolved against its own digest, pending and settled landing separately.
    """

    @staticmethod
    def _aggregator(
        store: Store,
        tid: str,
        day: int,
        *,
        pending: bool = False,
        fetched: str = "",
        description: str = "TESCO STORES 4912",
    ) -> None:
        body = json.dumps(
            {"results": [_record(tid, day, description=description)], "status": "Succeeded"}
        ).encode()
        artefact = truelayer.artefact_for(
            body,
            account_id="tl-1",
            kind="pending" if pending else "booked",
            account_ref=ACCOUNT,
        )
        if fetched:
            artefact = replace(artefact, fetched_at=datetime.fromisoformat(fetched))
        store.land_artefact(artefact)
        reconcile_batch(
            store,
            [
                truelayer.to_transaction(
                    _record(tid, day, description=description),
                    account_id=ACCOUNT,
                    pending=pending,
                )
            ],
            digest=artefact.digest,
        )

    @staticmethod
    def _bank_feed(store: Store, uid: str, day: int) -> None:
        from obdi.ingest.providers import starling

        item = {
            "feedItemUid": uid,
            "amount": {"currency": "GBP", "minorUnits": 1499},
            "direction": "OUT",
            "transactionTime": f"2026-03-{day:02}T09:15:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "counterPartyName": "Tesco",
            "reference": "TESCO STORES",
        }
        artefact = starling.artefact_for(
            json.dumps({"feedItems": [item]}).encode(),
            account_id=ACCOUNT,
            kind="feed",
            origin=f"https://api.example.com/feed/account/a/category/c?uid={uid}",
        )
        store.land_artefact(artefact)
        reconcile_batch(
            store,
            [starling.to_transaction(item, account_id=ACCOUNT)],
            digest=artefact.digest,
        )

    def _pending_then_bank_feed_then_settled(self, store: Store) -> None:
        self._aggregator(store, "pend-1", 14, pending=True)
        self._bank_feed(store, "uid-1", 14)
        self._aggregator(store, "book-1", 14)

    def _bank_feed_then_pending_then_settled(self, store: Store) -> None:
        self._bank_feed(store, "uid-1", 14)
        self._aggregator(store, "pend-1", 14, pending=True)
        self._aggregator(store, "book-1", 14)

    def test_AggregatorPending_ThenBankFeedSettled_ThenAggregatorSettled_IsOneRow(self, store):
        self._pending_then_bank_feed_then_settled(store)

        assert _held(store) == (1, -1499)
        assert store.review_queue() == []

    def test_BankFeedSettled_ThenAggregatorPending_ThenAggregatorSettled_IsOneRow(self, store):
        self._bank_feed_then_pending_then_settled(store)

        assert _held(store) == (1, -1499)
        assert store.review_queue() == []

    def test_BothAggregatorIdsAndBothSourcesAreRemembered(self, store):
        self._pending_then_bank_feed_then_settled(store)

        remembered = {(source, source_id) for _, source, source_id, _, _ in
                      store.sighted_ids_for_account(ACCOUNT)}
        assert remembered == {
            ("truelayer", "pend-1"),
            ("truelayer", "book-1"),
            ("starling", "uid-1"),
        }

    @pytest.mark.parametrize("sequence", ["_pending_then_bank_feed_then_settled",
                                          "_bank_feed_then_pending_then_settled"])
    def test_Rebuild_OfEitherSequence_AgreesWithTheLiveIngest(self, store, sequence):
        getattr(self, sequence)(store)
        live = _held(store)

        # The feed's origin names its category, which the map binds to the account.
        rebuild_from_raw(
            store, account_map=AccountMap([AccountBinding(ACCOUNT, "starling", "c")])
        )

        assert _held(store) == live == (1, -1499)
        assert store.review_queue() == []

    def test_AggregatorPending_ThenExport_ThenAggregatorSettled_IsOneRow(self, store, tmp_path):
        self._aggregator(store, "pend-1", 14, pending=True, fetched="2026-01-01T09:00:00+00:00")
        _export_sighting(store, tmp_path)
        self._aggregator(store, "book-1", 14, fetched="2099-01-01T09:00:00+00:00")
        live = _held(store)

        rebuild_from_raw(store)

        assert _held(store) == live == (1, -1499)
        assert store.review_queue() == []

    def test_SettledReissue_ThenExport_ThenADifferentSettledPayment_IsTwoRows(
        self, store, tmp_path
    ):
        """A genuinely second payment after a real settlement: the row's
        aggregator id is now a settled one, and a different one is a payment."""
        self._aggregator(store, "pend-1", 14, pending=True)
        self._aggregator(store, "book-1", 14)
        _export_sighting(store, tmp_path)
        self._aggregator(store, "book-2", 17, description="CORNER SHOP LEEDS")

        assert _held(store) == (2, FARES_TOTAL)

    def test_IdSeenPendingAndLaterSettledUnderTheSameId_ThenADifferentOne_IsTwoRows(
        self, store, tmp_path
    ):
        """An id that was pending and then settled unchanged is a settled id."""
        self._aggregator(store, "tl-1", 14, pending=True)
        # Described differently: byte-identical responses land as one artefact,
        # so the settled one would otherwise be recorded as the pending one.
        self._aggregator(store, "tl-1", 14, description="TESCO STORES 4912 GB")
        _export_sighting(store, tmp_path)
        self._aggregator(store, "tl-2", 17, description="CORNER SHOP LEEDS")

        assert _held(store) == (2, FARES_TOTAL)

    def test_SettledAggregatorPayment_ThenExport_ThenADifferentSettledOne_IsTwoRowsAndFlagged(
        self, store, tmp_path
    ):
        self._aggregator(store, "tl-1", 14)
        _export_sighting(store, tmp_path)
        self._aggregator(store, "tl-2", 15)

        assert _held(store) == (2, FARES_TOTAL)
        assert len(store.review_queue()) == 1

    def test_NewPendingPayment_AfterASettledOneWasSightedByAnExport_IsItsOwnRow(
        self, store, tmp_path
    ):
        """A pending record dated after a settled row cannot be its precursor."""
        self._aggregator(store, "tl-1", 14)
        _export_sighting(store, tmp_path)
        self._aggregator(store, "tl-2", 17, pending=True, description="CORNER SHOP LEEDS")

        assert _held(store) == (2, FARES_TOTAL)

    def test_StalePendingRecordOfASettledPayment_AfterAnExportSightedIt_IsStillOneRow(
        self, store, tmp_path
    ):
        """The pending list can lag and still name a payment already settled."""
        self._aggregator(store, "tl-1", 14)
        _export_sighting(store, tmp_path)
        self._aggregator(store, "tl-stale", 14, pending=True)

        assert _held(store) == (1, -1499)


class TestARebuildFromRawAgreesWithTheLiveIngest:
    def test_Rebuild_OfTheSameSightings_HoldsTwoRows(self, store, tmp_path):
        def land_aggregator(records: list[dict[str, object]], fetched: str) -> None:
            body = json.dumps({"results": records, "status": "Succeeded"}).encode()
            artefact = truelayer.artefact_for(
                body, account_id="tl-1", kind="booked", account_ref=ACCOUNT
            )
            # Pinned either side of the export's own (current) timestamp: a
            # rebuild replays by this value, and the export's is local time
            # where the aggregator's is UTC, so leaving them to the clock lets
            # the replay order differ from the live one.
            store.land_artefact(replace(artefact, fetched_at=datetime.fromisoformat(fetched)))

        land_aggregator([_record("tl-1", 14)], "2026-01-01T09:00:00+00:00")
        _export_sighting(store, tmp_path)
        land_aggregator(
            [_record("tl-2", 17, description="CORNER SHOP LEEDS")], "2099-01-01T09:00:00+00:00"
        )

        rebuild_from_raw(store)

        assert _held(store) == (2, FARES_TOTAL)
