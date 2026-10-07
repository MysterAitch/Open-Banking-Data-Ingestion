"""Disregarding names one balance, shows what was disregarded, and never outlives its balance.

REVIEW FINDINGS, held as failing tests. A disregarded balance is a check no longer made, so each
of these leaves a check unmade without the page saying so, or unmakes more than was named.

  not listed    the account page offers "Disregard" for the bank's own balance and for a whole
                account's balance, and its question promises "It stays on the account page,
                marked, and can be used again". Only the account's own statement, stated, and
                aggregator balances are carried in `EffectiveOpening.disregarded`, so a bank or
                family balance vanishes from the page once disregarded and nothing offers it back.
  the premise   the nil opening of an account created with its feed held from then ("opened") is
                a premise and not a known balance, yet disregarding it is accepted, reported as
                done, and changes nothing.
  one key, two  two statements of one layout closing on one day with different figures share the
                key (day, source, basis), so disregarding "one" disregards both and the conflict
                cannot be settled in favour of either.
  outlives      a typed balance is disregarded, then removed, then typed again with another
                figure. The key holds no figure, so the new balance is disregarded on arrival.

THE ACCOUNTS, with the answers decided before the first run.

  the household of `bank_balance_corpus` (a main account and its Bills Space, September 2026),
  with the bank stating its balance at 13:00 on the 20th, at 13:00 and 18:00 on the 22nd (the
  second 7.00 more than the transactions give), and at 13:00 on the 25th, and the export held.
  Disregarding the whole account's balance for 2026-09-12, or the bank's for 2026-09-22: the
  balance leaves the reading AND is listed as disregarded. Disregarding the nil opening of
  2026-08-31: refused.

  `twin`: two Santander statements both closing 2026-05-10, both opening at 100.00 owed on
  2026-04-10, one listing a spend of 10.00 (closing 110.00) and the other that spend and one of
  5.00 (closing 115.00). Before: a conflict on 2026-05-10. After disregarding the layout's
  closing for that day: at most one of the two is out, so one closing for 2026-05-10 is read.

  `card`: one statement closing 2026-05-10 at 110.00 owed. A balance of 120.00 owed is typed for
  that day, disregarded, removed, and 110.00 owed typed in its place. The retyped balance is
  read: two balances for 2026-05-10, equal, none disregarded.
"""

from __future__ import annotations

from datetime import date

import pytest

from bank_balance_corpus import EXPORT_ROWS, at, balance_body, household
from obdi.ingest.family_anchors import OPENED, families_of
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.ledger import opening_view
from obdi.verify.agreement import HELD_CONFLICT, derive_agreement, known_of_opening
from obdi.verify.balance_anchors import (
    BANK,
    FAMILY,
    STATED,
    STATEMENT,
    AnchorRefused,
    anchor_key,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
    remove_stated_anchor,
)
from obdi.verify.bank_balances import BANK_SOURCE
from statement_span_world import Spend, statement
from test_space_attribution import MAIN, MAP

D = date
SOURCE = "santander-cc-pdf"
EXPORT = "starling-csv"


@pytest.fixture
def starling(tmp_path):
    balances = [
        (at(20, 13), balance_body(330500, 362500)),
        (at(22, 13), balance_body(321500, 353500)),
        (at(22, 18), balance_body(321500 + 700, 353500 + 700)),
        (at(25, 13), balance_body(441500, 473500)),
    ]
    store = household(tmp_path, balances, export=EXPORT_ROWS)
    try:
        yield store, families_of(store, MAP)
    finally:
        store.connection.close()


def keys_read(store, families) -> set[tuple[date, str, str]]:
    reading = effective_opening(store, MAIN, families=families)
    return {anchor_key(r.anchor) for r in reading.readings}


def listed_as_disregarded(store, families) -> set[tuple[str, str, str]]:
    view = opening_view(effective_opening(store, MAIN, families=families))
    return {(entry.day, entry.stating, entry.basis) for entry in view.disregarded}


class TestWhatADisregardDoesNotReachIsRefusedLoudly:
    """DECISION, CHANGING THESE TWO TESTS. The review asked that a bank or whole-account balance,
    once disregarded, leave the reading AND be listed as disregarded. Supporting them completely
    means a key that names one per-moment balance (the bank states two on the 22nd) and a
    disregard that reaches the whole-account walk, which reads its balances apart from the
    account's own. That is not done here; the alternative the review allowed is done instead:
    such a disregard is refused, nothing is written, and the page offers no button for it. The
    tests below say so, and the reading is unchanged by the refusal."""

    def test_Disregard_OfAWholeAccountBalance_IsRefusedAndChangesNothing(self, starling):
        store, families = starling
        key = (D(2026, 9, 12), EXPORT, FAMILY)
        assert key in keys_read(store, families)

        with pytest.raises(AnchorRefused):
            disregard_balance(store, MAIN, "2026-09-12", EXPORT, FAMILY, families=families)

        assert key in keys_read(store, families)
        assert store.disregarded_balance_keys(MAIN) == []

    def test_Disregard_OfTheBanksOwnBalance_IsRefusedAndChangesNothing(self, starling):
        store, families = starling
        key = (D(2026, 9, 22), BANK_SOURCE, BANK)
        assert key in keys_read(store, families)

        with pytest.raises(AnchorRefused):
            disregard_balance(store, MAIN, "2026-09-22", BANK_SOURCE, BANK, families=families)

        assert key in keys_read(store, families)
        assert store.disregarded_balance_keys(MAIN) == []

    def test_Page_OffersNoDisregardForABalanceItCannotDisregard(self, starling):
        store, families = starling
        view = opening_view(effective_opening(store, MAIN, families=families))

        offered = {
            (entry.day, entry.stating, entry.basis)
            for day in view.shared_days
            for entry in day.entries
            if entry.can_disregard
        }

        assert ("2026-09-12", EXPORT, FAMILY) not in offered
        assert ("2026-09-22", BANK_SOURCE, BANK) not in offered


class TestTheNilOpeningIsAPremiseAndNotABalance:
    def test_Disregard_OfTheNilOpeningAnAccountWasCreatedWith_IsRefused(self, starling):
        store, families = starling
        assert (D(2026, 8, 31), OPENED, OPENED) in keys_read(store, families)

        with pytest.raises(AnchorRefused):
            disregard_balance(store, MAIN, "2026-08-31", OPENED, OPENED, families=families)

        assert store.disregarded_balance_keys(MAIN) == []


class TestTwoStatementsOfOneLayoutClosingOnOneDay:
    def test_Disregard_WhenTheTwoDiffer_LeavesOneOfThemInTheReading(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        with Store(tmp_path / "store.sqlite3") as store:
            statement(
                store, first, "twin", D(2026, 5, 10), 10000,
                [Spend(D(2026, 5, 4), "Aaa Shop", 1000)],
                received=D(2026, 5, 10), previous_close=D(2026, 4, 10),
            )
            statement(
                store, second, "twin", D(2026, 5, 10), 10000,
                [Spend(D(2026, 5, 4), "Aaa Shop", 1000), Spend(D(2026, 5, 6), "Bbb Shop", 500)],
                received=D(2026, 5, 11), previous_close=D(2026, 4, 10),
            )
            keep_statement_readings(store)
            store.connection.commit()
            before = derive_agreement(known_of_opening(effective_opening(store, "twin")), ())
            assert before.state == HELD_CONFLICT
            assert [c.day for c in before.conflicts] == [D(2026, 5, 10)]

            assert disregard_balance(store, "twin", "2026-05-10", SOURCE, STATEMENT)

            reading = effective_opening(store, "twin")
            closings = [
                r.anchor.balance_minor
                for r in reading.readings
                if r.anchor.day == D(2026, 5, 10) and r.anchor.basis == STATEMENT
            ]
            assert len(closings) == 1, "one of the two closings was named, and both are gone"
            assert len(reading.disregarded) == 1


class TestABalanceTypedAgainAfterItsDisregardedOneWasRemoved:
    def test_StatedBalance_WhenRetypedForADayWhoseEarlierOneWasDisregardedAndRemoved_IsRead(
        self, tmp_path
    ):
        with Store(tmp_path / "store.sqlite3") as store:
            statement(
                store, tmp_path, "card", D(2026, 5, 10), 10000,
                [Spend(D(2026, 5, 4), "Aaa Shop", 1000)],
                received=D(2026, 5, 10), previous_close=D(2026, 4, 10),
            )
            keep_statement_readings(store)
            record_stated_anchor(store, "card", "2026-05-10", "-120.00")
            store.connection.commit()
            assert disregard_balance(store, "card", "2026-05-10", STATED, STATED)
            assert remove_stated_anchor(store, "card", "2026-05-10")

            record_stated_anchor(store, "card", "2026-05-10", "-110.00")

            reading = effective_opening(store, "card")
            on_day = sorted(
                r.anchor.basis for r in reading.readings if r.anchor.day == D(2026, 5, 10)
            )
            assert on_day == [STATED, STATEMENT], "the balance typed last is the one meant"
            assert reading.disregarded == ()
