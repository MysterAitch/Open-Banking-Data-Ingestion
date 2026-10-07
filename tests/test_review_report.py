"""The queue's noise, quantified - declared recurring payments included."""

from __future__ import annotations

import json
from datetime import date

from obdi.core.models import SourceTier, Transaction
from obdi.identity import content_key
from obdi.ingest import reconcile_batch
from obdi.providers.truelayer import artefact_for
from obdi.review_report import FlagClass, review_report
from obdi.store import Store


def _flagged(land, store, description, reason, category=None):
    """One ordinary transaction, landed through the door, then flagged.

    The entity id is whatever the application minted rather than one this file
    chose. Nothing here asserts on it - the rows only need to be distinct - but a
    fixture that invents ids cannot notice the writer and the reader disagreeing
    about identity, which is where this project's expensive defects have been.
    """
    entity = land(
        store,
        description=description,
        raw={"transaction_category": category} if category else {},
        # Distinct amounts so the matcher does not queue these for review by
        # itself and win the one-row-per-transaction slot with its own reason.
        amount_minor=-1200 - (len(description) * 7),
    )
    store.queue_for_review(entity, reason)
    return entity


class TestReviewReport:
    def test_Report_CountsReasons_AndNamesDeclarationMatches(
        self, tmp_path, land_transaction
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            _flagged(
                land_transaction, store, "NETFLIX.COM", "recurring-amount: seen 4 times"
            )
            _flagged(
                land_transaction,
                store,
                "COUNCIL TAX SOUTHWARK",
                "recurring-amount: seen 12 times",
            )
            _flagged(land_transaction, store, "COFFEE CORNER", "fuzzy-match: near miss")
            store.land_artefact(
                artefact_for(
                    json.dumps(
                        {"results": [{"reference": "COUNCIL TAX SOUTHWARK"}]}
                    ).encode(),
                    account_id="halifax-current",
                    kind="direct_debits",
                )
            )

            report = review_report(store)

        assert report.open_flags == 3
        # Reasons are prose with no separator, so they are not a grouping key;
        # the breakdown is by what the evidence proves instead.
        assert report.breakdown.by_class == {FlagClass.NO_LIVE_NEIGHBOUR: 3}
        # The council tax flag matches a DECLARED direct debit: suppressible.
        assert report.declaration_matches == 1
        assert "council tax southwark" in report.declaration_names
        # Clusters name the biggest offenders for eyeballing.
        assert ("NETFLIX.COM", 1) in report.top_clusters

    def test_Report_OnAnEmptyQueue_SaysSoWithoutFuss(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            report = review_report(store)

        assert report.open_flags == 0
        assert report.declaration_matches == 0


class TestBankCategories:
    def test_Report_CountsBankLabelledRecurring_AsCalmCandidates(
        self, tmp_path, land_transaction
    ):
        """Flags the bank itself labels DIRECT_DEBIT or STANDING_ORDER are
        expected payments by definition - the report quantifies how much
        of the queue they explain before any matcher change is made."""
        with Store(tmp_path / "s.sqlite3") as store:
            _flagged(
                land_transaction, store, "COUNCIL TAX", "recurring-amount", "DIRECT_DEBIT"
            )
            _flagged(
                land_transaction,
                store,
                "SAVINGS SWEEP",
                "recurring-amount",
                "STANDING_ORDER",
            )
            _flagged(land_transaction, store, "COFFEE CORNER", "fuzzy-match", "PURCHASE")
            _flagged(land_transaction, store, "MYSTERY SHOP", "fuzzy-match")

            report = review_report(store)

        assert report.bank_recurring == 2
        assert report.bank_categories == {
            "DIRECT_DEBIT": 1,
            "STANDING_ORDER": 1,
            "PURCHASE": 1,
        }
        text = report.describe()
        assert "2 flagged transactions are bank-labelled" in text
        assert "DIRECT_DEBIT: 1" in text


PRIVATE_MINOR = -73913
PRIVATE_PAYEE = "ZEBRA CROSSING CAFE"
TODAY = date(2026, 10, 2)


def _txn(
    account: str,
    source_id: str,
    when: date,
    *,
    source: str = "truelayer",
    description: str = "EXAMPLE BUSES",
    amount: int = -250,
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
        content_key=content_key(amount_minor=amount, value_date=when, description=description),
    )


def _land_one(store: Store, account: str, source_id: str, when: date, **details) -> None:
    reconcile_batch(
        store,
        [_txn(account, source_id, when, **details)],
        digest=f"d-{account}-{source_id}",
    )


def _known_answer_store(store: Store) -> None:
    """Five flags, decided before the first run.

    acct-a: one flag, a payment 10 days old with one neighbour, unproven.
    acct-b: one flag, over a year old, Starling ids that are never reused.
    acct-c: two flags, 109 days old, each with two neighbours, unproven.
    acct-d: one flag, 53 days old, one neighbour, unproven.
    """
    private = {"description": PRIVATE_PAYEE, "amount": PRIVATE_MINOR}
    _land_one(store, "acct-a", "a1", date(2026, 9, 20), **private)
    _land_one(store, "acct-a", "a2", date(2026, 9, 22), **private)
    _land_one(store, "acct-b", "b1", date(2025, 1, 5), source="starling")
    _land_one(store, "acct-b", "b2", date(2025, 1, 5), source="starling")
    for ident in ("c1", "c2", "c3"):
        _land_one(store, "acct-c", ident, date(2026, 6, 15))
    _land_one(store, "acct-d", "d1", date(2026, 8, 10))
    _land_one(store, "acct-d", "d2", date(2026, 8, 10))


class TestTheBreakdownOfOpenFlags:
    def test_Report_OverAKnownStore_CountsEachDimensionToTheKnownAnswer(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _known_answer_store(store)
            breakdown = review_report(store, today=TODAY).breakdown

        assert breakdown.open_flags == 5
        assert breakdown.by_class == {FlagClass.OPEN: 4, FlagClass.IDS_KEPT_FOR_LIFE: 1}
        assert breakdown.by_class_account == {
            (FlagClass.OPEN, "acct-a"): 1,
            (FlagClass.OPEN, "acct-c"): 2,
            (FlagClass.OPEN, "acct-d"): 1,
            (FlagClass.IDS_KEPT_FOR_LIFE, "acct-b"): 1,
        }
        assert breakdown.by_source_pair == {
            ("truelayer", "truelayer"): 4,
            ("starling", "starling"): 1,
        }
        assert breakdown.by_age == {
            "under 30 days": 1,
            "30-90 days": 1,
            "90-365 days": 2,
            "over a year": 1,
        }
        assert breakdown.by_neighbours == {"1": 3, "2-3": 2}


class TestTheMaskedReportShowsNothingPrivate:
    def test_Describe_Masked_HoldsNoAmountDescriptionOrDayLevelDate(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _known_answer_store(store)
            text = review_report(store, today=TODAY).describe(masked=True)

        assert "acct-a" in text and "open: 4" in text
        for private in (
            str(abs(PRIVATE_MINOR)),
            "739.13",
            PRIVATE_PAYEE,
            "2026-09",
            "2025-01",
        ):
            assert private not in text

    def test_Describe_Unmasked_NamesTheLargestClusters(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _known_answer_store(store)
            text = review_report(store, today=TODAY).describe(masked=False)

        assert PRIVATE_PAYEE in text

    def test_Describe_ByDefault_IsTheMaskedRendering(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _known_answer_store(store)
            text = review_report(store, today=TODAY).describe()

        assert PRIVATE_PAYEE not in text
