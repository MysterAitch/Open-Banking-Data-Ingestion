"""The queue's noise, quantified - declared recurring payments included."""

from __future__ import annotations

import json

from obdi.providers.truelayer import artefact_for
from obdi.review_report import review_report
from obdi.store import Store


def _flag_transaction(store, entity_id, description, reason, category=None):
    raw = json.dumps({"transaction_category": category}) if category else "{}"
    store.connection.execute(
        "INSERT INTO transactions (entity_id, account_id, amount_minor, "
        "value_date, booking_date, description, source, currency, tier, "
        "status, content_key, occurrence, first_seen_at, last_seen_at, raw) "
        "VALUES (?, 'halifax-current', -1200, '2026-07-01', '2026-07-01', "
        "?, 'truelayer', 'GBP', 'authoritative', 'booked', ?, 0, "
        "'2026-07-01T00:00:00', '2026-07-01T00:00:00', ?)",
        (entity_id, description, f"key-{entity_id}", raw),
    )
    store.connection.commit()
    store.queue_for_review(entity_id, reason)


class TestReviewReport:
    def test_Report_CountsReasons_AndNamesDeclarationMatches(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _flag_transaction(
                store, "e-1", "NETFLIX.COM", "recurring-amount: seen 4 times"
            )
            _flag_transaction(
                store, "e-2", "COUNCIL TAX SOUTHWARK", "recurring-amount: seen 12 times"
            )
            _flag_transaction(store, "e-3", "COFFEE CORNER", "fuzzy-match: near miss")
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
    def test_Report_CountsBankLabelledRecurring_AsCalmCandidates(self, tmp_path):
        """Flags the bank itself labels DIRECT_DEBIT or STANDING_ORDER are
        expected payments by definition - the report quantifies how much
        of the queue they explain before any matcher change is made."""
        with Store(tmp_path / "s.sqlite3") as store:
            _flag_transaction(
                store, "e-1", "COUNCIL TAX", "recurring-amount", "DIRECT_DEBIT"
            )
            _flag_transaction(
                store, "e-2", "SAVINGS SWEEP", "recurring-amount", "STANDING_ORDER"
            )
            _flag_transaction(store, "e-3", "COFFEE CORNER", "fuzzy-match", "PURCHASE")
            _flag_transaction(store, "e-4", "MYSTERY SHOP", "fuzzy-match")

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
