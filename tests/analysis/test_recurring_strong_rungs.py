"""A series is keyed on the party an account or id names, and shown by its readable name.

KNOWN ANSWERS, decided before the first run. Invented rent, twelve months, one landlord:

  - Twelve monthly transfers to one account with twelve different references and two stated
    spellings: ONE monthly series of 12, shown "alex rowan", whose label is not the account.
  - Six statement rows (description only) before six feed rows stating the account and the same
    description: ONE series of 12, because a payment seen under both says the description is that
    account's party (the mixed-source series).
  - The same twelve rows split between two accounts: TWO series of 6.
"""

from __future__ import annotations

from datetime import date

from obdi.analysis.recurring import Series, find_recurring
from obdi.core.models import SourceTier, Transaction

TODAY = date(2026, 10, 7)
ACCOUNT = "current-main"


def row(
    n: int,
    when: date,
    description: str,
    *,
    counterparty: str = "",
    party_account: str = "",
    source: str = "starling",
) -> Transaction:
    return Transaction(
        account_id=ACCOUNT,
        amount_minor=-85000,
        value_date=when,
        booking_date=when,
        description=description,
        counterparty=counterparty,
        party_account=party_account,
        source=source,
        tier=SourceTier.SYNTHETIC,
        entity_id=f"e{n:03}",
    )


def series_of(rows: list[Transaction]) -> list[Series]:
    return find_recurring(rows, pairs=(), today=TODAY)


def months(count: int, start: int = 1) -> list[date]:
    return [date(2026, start + i, 5) for i in range(count)]


class TestARentSeriesKeyedByAccount:
    def test_TwelveReferencesAndTwoSpellings_AreOneMonthlySeriesShownByTheName(self):
        names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        rows = [
            row(
                i, when, f"{names[i]} RENT",
                counterparty="Alex Rowan" if i < 7 else "A Rowan",
                party_account="201234-55667788",
            )
            for i, when in enumerate(months(12))
        ]

        (found,) = series_of(rows)

        assert (found.cadence, found.count) == ("monthly", 12)
        assert found.shape == "alex rowan"
        assert "55667788" not in found.shape and "acct-" not in found.shape

    def test_StatementMonthsAndFeedMonths_OfOnePayment_AreOneSeries(self):
        statement = [
            row(i, when, "RENT PAYMENT", source="nationwide-statement-pdf")
            for i, when in enumerate(months(6))
        ]
        feed = [
            row(10 + i, when, "RENT PAYMENT", counterparty="Alex Rowan",
                party_account="201234-55667788")
            for i, when in enumerate(months(6, start=7))
        ]

        (found,) = series_of([*statement, *feed])

        assert (found.cadence, found.count) == ("monthly", 12)
        assert found.shape == "alex rowan"

    def test_ExportMonthsThatStateOnlyTheName_AndFeedMonthsThatAlsoStateAnId_AreOneSeries(self):
        # The measured split: an export states "Gym" for the years the feed does not, the feed
        # states the same name with the merchant's uid, and the two were a stopped series and a
        # new one.
        export = [
            row(i, when, "GYM MEMBERSHIP", counterparty="Gym", source="starling-csv")
            for i, when in enumerate(months(6))
        ]
        feed = [
            Transaction(
                account_id=ACCOUNT, amount_minor=-85000, value_date=when, booking_date=when,
                description="GYM MEMBERSHIP", counterparty="Gym",
                party_source_id="starling:uid-gym", source="starling",
                tier=SourceTier.SYNTHETIC, entity_id=f"f{i:03}",
            )
            for i, when in enumerate(months(6, start=7))
        ]

        (found,) = series_of([*export, *feed])

        assert (found.cadence, found.count) == ("monthly", 12)
        assert found.shape == "gym"

    def test_TheSameRentToTwoAccountsInTurn_IsTwoSeriesNotOne(self):
        # The landlord's account changed halfway: the reference is the same all year, and the
        # two accounts are two parties however alike the rows look.
        rows = [
            row(i, when, "RENT", party_account="201234-55667788" if i < 6 else "304050-11223344")
            for i, when in enumerate(months(12))
        ]

        found = series_of(rows)

        assert [(s.cadence, s.count) for s in found] == [("monthly", 6), ("monthly", 6)]
