"""The queue's noise, quantified - declared recurring payments included."""

from __future__ import annotations

import json

from obdi.providers.truelayer import artefact_for
from obdi.review_report import review_report
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
        assert report.by_reason == {"recurring-amount": 2, "fuzzy-match": 1}
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
        assert "2 flagged transaction(s) are bank-labelled" in text
        assert "DIRECT_DEBIT: 1" in text
