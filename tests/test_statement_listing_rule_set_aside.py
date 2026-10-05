"""One meaning of "listed" (round four, finding 3): a disregarded statement lists nothing ANYWHERE,
the placement the chain of known balances uses included.

Decided before the first run:

  * with no disregard anywhere, the placement of every transaction (`_gather_all(...).placed`) is
    exactly `statement_membership` over every held statement, as it was before;
  * a transaction listed only by a disregarded statement is placed by its date like any other
    source's: the account keeps the explained day it had and adds up through it
    (the reviewer's `y-set-aside` is in `test_statement_listing_rule_review_three`);
  * a disregard made and then undone returns every answer to what it was.
"""

from __future__ import annotations

from datetime import date

from listing_rule_reading import app_reading
from obdi.balance_anchors import (
    STATEMENT,
    _gather_all,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
    use_balance_again,
)
from obdi.statement_membership import statement_membership
from obdi.statement_terms import statement_balances
from obdi.store import Store
from statement_span_world import Spend
from test_statement_listing_measure import FAMILIES, chain

D = date
JAN, FEB, MAR = D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10)


def test_Placement_WhenNothingIsDisregarded_IsExactlyTheMembershipOfEveryHeldStatement(tmp_path):
    with Store(tmp_path / "s.sqlite3") as store:
        chain(
            store, tmp_path, "p-one", [JAN, FEB],
            [[Spend(D(2026, 1, 5), "One p", 419)], [Spend(D(2026, 1, 8), "Two p", 307)]],
        )
        statements, _ = statement_balances(store, "p-one")

        placed = _gather_all(store, "p-one", FAMILIES).placed

        assert dict(placed) == dict(statement_membership(store, "p-one", statements).placed)
        assert placed


def _answers(store: Store, ref: str):
    standing, verdict = app_reading(store, ref, FAMILIES)
    opening = effective_opening(store, ref, families=FAMILIES)
    return (
        verdict,
        standing.own.state,
        standing.own.through,
        standing.own.tested,
        tuple(c.day for c in standing.own.closed_before),
        opening.opening_minor,
        dict(_gather_all(store, ref, FAMILIES).placed),
    )


def test_Disregard_WhenMadeAndThenUndone_ReturnsEveryAnswerToWhatItWas(tmp_path):
    with Store(tmp_path / "s.sqlite3") as store:
        owed = chain(
            store, tmp_path, "p-undo", [JAN, FEB, MAR],
            [
                [Spend(D(2026, 1, 5), "One u", 419)],
                [Spend(D(2026, 1, 20), "Two u", 717)],
                [Spend(FEB, "Pending u", 777), Spend(D(2026, 2, 20), "Later u", 331)],
            ],
        )
        record_stated_anchor(
            store, "p-undo", FEB.isoformat(), f"{-(owed[1] + 777) / 100:.2f}", today=D(2026, 9, 1)
        )
        before = _answers(store, "p-undo")
        march = next(
            r.anchor
            for r in effective_opening(store, "p-undo", families=FAMILIES).readings
            if r.anchor.basis == STATEMENT and r.anchor.day == MAR
        )

        disregard_balance(
            store, "p-undo", MAR.isoformat(), march.stating, STATEMENT, families=FAMILIES
        )
        during = _answers(store, "p-undo")
        assert use_balance_again(
            store, "p-undo", MAR.isoformat(), march.stating, STATEMENT, families=FAMILIES
        )
        after = _answers(store, "p-undo")

    assert during != before
    assert after == before
