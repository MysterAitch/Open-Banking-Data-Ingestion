"""A payment only a statement covers is told pulled or scheduled by the method it printed.

KNOWN ANSWERS, decided before the first run. Six monthly rows of one amount, read from a statement
layout that separates the method from the party:

  - "Direct debit" rows: one monthly series, PULLED, and the page's basis names the type
    ("by type: Direct Debit"), as it does for a feed's coded type.
  - "Standing order" rows: SCHEDULED, by type.
  - "Payment to" rows (a method that says nothing about who started it): the kind is told by the
    shape, so the basis is not "by type".
  - The rows state their party, so the series is named by it, whatever the reference says.
"""

from __future__ import annotations

from datetime import date

from obdi.analysis.recurring import PULLED, SCHEDULED, Series, find_recurring
from obdi.ingest.parsers.pdf_statements import NationwideStatementPdfParser
from obdi.ingest.parsers.statement_reading import StatementReading, StatementRow

TODAY = date(2026, 10, 7)
MONTHS = [(2026, m) for m in range(4, 10)]


def series_for(method: str, party: str = "EXAMPLE UTILITY", reference: str = "ref") -> Series:
    reading = StatementReading(
        transactions=[
            StatementRow(
                value_date=date(y, m, 15),
                description=f"{reference} {m}",
                amount_minor=-4500,
                counterparty=party,
                method=method,
                printed=f"{method} {party} {reference} {m}",
            )
            for y, m in MONTHS
        ]
    )
    rows = [
        _entity(t, index)
        for index, t in enumerate(NationwideStatementPdfParser().rows_of(reading, "current"))
    ]
    found = find_recurring(rows, pairs=(), today=TODAY)
    assert len(found) == 1, [(s.shape, s.cadence) for s in found]
    return found[0]


def _entity(row, index: int):
    from dataclasses import replace

    return replace(row, entity_id=f"e{index}")


class TestAStatementMethodDecidesWhoStartedAPayment:
    def test_DirectDebitRows_AreAPulledMonthlySeriesByType(self):
        found = series_for("Direct debit")

        assert (found.kind, found.cadence) == (PULLED, "monthly")
        assert found.basis.startswith("by type")

    def test_StandingOrderRows_AreAScheduledSeriesByType(self):
        found = series_for("Standing order")

        assert found.kind == SCHEDULED
        assert found.basis.startswith("by type")

    def test_PaymentToRows_SayNothingOfWhoStartedItSoTheTypeDoesNotDecide(self):
        found = series_for("Payment to")

        assert not found.basis.startswith("by type")

    def test_Series_IsNamedByTheStatedPartyNotTheChangingReference(self):
        found = series_for("Direct debit", reference="invoice")

        assert found.shape == "example utility"
