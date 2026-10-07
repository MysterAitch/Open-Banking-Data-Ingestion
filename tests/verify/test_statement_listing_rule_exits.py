"""Every statement fault has an exit the owner can take from the page (`agreement`, R3).

A fault is only ever said of a statement IN USE, whose closing is a known balance, so disregarding
that balance (v0.4.331) takes the statement out of the reading altogether. Decided before the
first run: for each fault kind built below, the fault is there, disregarding the statement's
closing clears it from the standing, and the account's other statements are untouched.

  not-held, other-amount          the first round's accounts (`r-lone-missing`, `r-lone-merged`)
  no-longer-counts                the measurement's household (`history`)
  (held-elsewhere is no fault kind: it is cannot-say, round three, so it needs no exit)

`held-twice`, `listed-twice`, and `does-not-reach` are not built here: the doors cannot make the
state (they deduplicate), so those three are covered by the same exit through the sentence and
by `test_statement_listing_rule` at the agreement level only.
"""

from __future__ import annotations

from datetime import date

import pytest

from listing_rule_reading import app_reading
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import STATEMENT, disregard_balance, effective_opening
from test_statement_listing_measure import FAMILIES
from test_statement_listing_measure import world as listing_world  # noqa: F401 - the fixture
from test_statement_listing_rule_accounts import build

FEB = date(2026, 2, 10)


@pytest.fixture(scope="module")
def mine(tmp_path_factory):
    root = tmp_path_factory.mktemp("exits")
    with Store(root / "store.sqlite3") as store:
        build(store, root)
        yield store


def source_of(store: Store, ref: str, day: date) -> str:
    return next(
        r.anchor.stating
        for r in effective_opening(store, ref, families=FAMILIES).readings
        if r.anchor.basis == STATEMENT and r.anchor.day == day
    )


def kinds(store: Store, ref: str) -> list[str]:
    standing, _ = app_reading(store, ref, FAMILIES)
    return [f.says for f in standing.own.statement_faults]


@pytest.mark.parametrize(
    ("ref", "words"),
    [
        ("r-lone-missing", "is not held"),
        ("r-lone-merged", "different amount"),
    ],
)
def test_Fault_WhenDisregardedAtItsClosing_IsClearedFromTheStanding(mine, ref, words):
    before = kinds(mine, ref)

    disregard_balance(
        mine, ref, FEB.isoformat(), source_of(mine, ref, FEB), STATEMENT, families=FAMILIES
    )

    assert len(before) == 1 and words in before[0]
    assert kinds(mine, ref) == []


@pytest.mark.parametrize(
    ("ref", "words"),
    [("history", "reversed or void")],
)
def test_FaultInTheMeasurementsHousehold_WhenDisregardedAtItsClosing_IsCleared(
    listing_world, ref, words  # noqa: F811
):
    store = listing_world[0]
    before = kinds(store, ref)

    disregard_balance(
        store, ref, FEB.isoformat(), source_of(store, ref, FEB), STATEMENT, families=FAMILIES
    )

    assert len(before) == 1 and words in before[0]
    assert kinds(store, ref) == []
