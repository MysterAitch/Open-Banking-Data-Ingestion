"""A disregard names its balance by a place that does not move, and the page's own button undoes it.

SECOND-ROUND REVIEW FINDINGS, held as failing tests. A balance is now named on the page by its
PLACE, by figure, among the balances under one key (day, whoever states it, basis): `which`. The
place is counted among the balances STILL READ, so it moves as soon as one is disregarded.

  sent twice    two statements of one layout close on one day with different figures, and the
                page offers a button each (which 0 and which 1). The answer to pressing one is the
                account page itself, as the response to a POST, so a refresh of that page, or a
                second press, sends the same form again. The balance that was `which` 0 is gone
                from the count, the other one is now `which` 0, and it is disregarded too. Both
                closings are then out and the day checks nothing.
  the same      a statement uploaded twice, in two files whose bytes differ and whose figures do
  figure        not, is one balance in the reading and one disregard in the store. Disregarded, it
                is listed twice, and each entry carries a place (1) no disregard holds, so the
                page's own "use again" button changes nothing and the balance cannot be read
                again from the page.

THE ACCOUNTS, with the answers decided before the first run.

  `twin`  two Santander statements closing 2026-05-10 from 100.00 owed on 2026-04-10, one
          listing a spend of 10.00 and the other that spend and one of 5.00. Disregarding the
          layout's closing for that day at place 0, twice: the second request is refused or
          changes nothing, one closing for 2026-05-10 is still read, and one disregard is held.
  `dup`   one statement closing 2026-05-10 held as two files of different bytes, and the next
          closing 2026-06-10. Its closing disregarded once: for every entry the view lists as
          disregarded, reading it again at the place the entry carries succeeds, and the closing
          for 2026-05-10 is read again.
"""

from __future__ import annotations

import calendar
from datetime import date

import pytest

from landing import import_file
from obdi.core.errors import DataError
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.ingest.synthetic_pdf import build_pdf
from obdi.read.ledger import opening_view
from obdi.verify.balance_anchors import (
    STATEMENT,
    disregard_balance,
    effective_opening,
    use_balance_again,
)
from statement_span_world import MONTHS, Spend, statement

D = date
DAY = D(2026, 5, 10)
SOURCE = "santander-cc-pdf"


def closings_read(store: Store, ref: str) -> list[int]:
    reading = effective_opening(store, ref)
    return [
        r.anchor.balance_minor
        for r in reading.readings
        if r.anchor.day == DAY and r.anchor.basis == STATEMENT
    ]


@pytest.fixture
def twin(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with Store(tmp_path / "store.sqlite3") as store:
        statement(
            store, first, "twin", DAY, 10000, [Spend(D(2026, 5, 4), "Aaa Shop", 1000)],
            received=DAY, previous_close=D(2026, 4, 10),
        )
        statement(
            store, second, "twin", DAY, 10000,
            [Spend(D(2026, 5, 4), "Aaa Shop", 1000), Spend(D(2026, 5, 6), "Bbb Shop", 500)],
            received=D(2026, 5, 11), previous_close=D(2026, 4, 10),
        )
        keep_statement_readings(store)
        store.connection.commit()
        yield store


def _same_statement_in_other_bytes(store: Store, root, ref: str, limit: str) -> None:
    """The statement closing 2026-05-10 at 110.00 owed, in a file whose credit limit line, and
    so whose bytes, depend on `limit`."""

    def ordinal(day: date) -> str:
        suffix = "th" if 10 <= day.day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(
            day.day % 10, "th"
        )
        return f"{day.day}{suffix}"

    spend = Spend(D(2026, 5, 4), "Aaa Shop", 1000)
    lines = [
        "Santander UK plc. Registered Office: 2 Triton Square",
        f"Statement Date: {ordinal(DAY)} {calendar.month_name[DAY.month]} {DAY.year}"
        "      Page No: 1 / 1",
        f"Account credit limit:            {limit}",
        "Previous balance as at 10th April 2026: £100.00",
        "Balance brought forward from previous statement          100.00",
        f"{ordinal(spend.day)} {MONTHS[spend.day.month - 1]} {spend.payee}   10.00",
        "Your new balance:                                        110.00",
    ]
    path = root / f"{ref}-{limit.replace(',', '').replace('.', '')}.pdf"
    path.write_bytes(build_pdf(lines))
    import_file(store, path, account_id=ref)


@pytest.fixture
def dup(tmp_path):
    with Store(tmp_path / "store.sqlite3") as store:
        _same_statement_in_other_bytes(store, tmp_path, "dup", "3,000.00")
        _same_statement_in_other_bytes(store, tmp_path, "dup", "4,000.00")
        statement(
            store, tmp_path, "dup", D(2026, 6, 10), 11000, [Spend(D(2026, 6, 4), "Bbb Shop", 500)],
            received=D(2026, 6, 10), previous_close=DAY,
        )
        keep_statement_readings(store)
        store.connection.commit()
        yield store


class TestTheSameDisregardSentTwice:
    def test_Disregard_WhenTheSameRequestArrivesAgain_DoesNotReachTheOtherStatement(self, twin):
        assert len(closings_read(twin, "twin")) == 2
        assert disregard_balance(twin, "twin", DAY.isoformat(), SOURCE, STATEMENT, which=0)

        try:
            again = disregard_balance(twin, "twin", DAY.isoformat(), SOURCE, STATEMENT, which=0)
        except DataError:
            again = False

        assert not again, "the request named one balance, and that one is already disregarded"
        assert len(closings_read(twin, "twin")) == 1, "the other closing is still read"
        assert len(twin.disregarded_balance_rows("twin")) == 1


class TestAStatementHeldAsTwoFilesOfTheSameFigures:
    def test_Account_WithTheDuplicate_ReadsOneClosingForTheDay(self, dup):
        held = dup.connection.execute("SELECT COUNT(*) FROM raw_artefacts").fetchone()[0]

        assert held == 3, "two files for May and one for June"
        assert len(closings_read(dup, "dup")) == 1

    def test_UseAgain_FromThePlaceThePageCarries_ReadsTheBalanceAgain(self, dup):
        assert disregard_balance(dup, "dup", DAY.isoformat(), SOURCE, STATEMENT)
        assert closings_read(dup, "dup") == []
        listed = opening_view(effective_opening(dup, "dup")).disregarded
        assert listed, "the disregarded closing is listed"

        restored = [
            use_balance_again(dup, "dup", entry.day, entry.stating, entry.basis, which=entry.which)
            for entry in listed
        ]

        assert any(restored), "one of the page's own buttons reads the balance again"
        assert len(closings_read(dup, "dup")) == 1
