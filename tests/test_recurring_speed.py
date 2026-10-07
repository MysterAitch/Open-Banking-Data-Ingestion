"""The recurring-payments page over the large invented store: a fixed number of statements, quickly.

THE RULE THE BUDGET HOLDS: the page reads the whole transaction table once, plus the pairing
table, plus the declared accounts for names, and detects in memory. It never asks the store a
question per series or per account, so the statements it issues do not grow with the store.

MEASURED 2026-10-07 on the large store (6,969 transactions, 412 proved transfer pairs) on a
machine busy with another build: reading 0.13 s, detecting 0.06 s, whole page 0.37 s. The
bounds are loose on time (a slow machine must not flake) and tight on statements.
"""

from __future__ import annotations

from datetime import date

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import serving
from obdi.recurring import find_recurring
from obdi.store import Store

#: Statements a GET of the page may issue: the transactions, the pairing table twice over (once
#: for the confirmation set and once for the pairs), the declared accounts, and the connections'
#: own start-up reads, and the one read of which shapes the owner gathered into entities.
#: Measured 26 with that read, which is one select and so took the page past the 25 this budget
#: was; allowing two more. A read per account or per series would add eleven or more.
RECURRING_STATEMENTS = 28
SECONDS = 20.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


@pytest.fixture(scope="module")
def pages(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("recurring")) as served:
        yield served


class TestRecurringPageOverTheLargeStore:
    def test_RecurringPage_OverTheLargeStore_IssuesAFixedFewStatementsAndIsQuick(self, pages):
        pages.get("/recurring")
        later = pages.get("/recurring")

        assert later.status == 200
        assert later.statements <= RECURRING_STATEMENTS, later.statements
        assert later.seconds < SECONDS

    def test_RecurringPage_OverTheLargeStore_SealsEveryAmountForAGet(self, pages):
        page = pages.get("/recurring")

        assert page.status == 200
        assert "£•••" in page.body

    def test_Detector_OverTheLargeStore_FindsEachSeriesOnceAndNoSeriesTwice(self, large):
        with Store(large.path) as store:
            rows = store.all_transactions()
            pairs = store.confirmed_transfer_pairs()

        found = find_recurring(rows, pairs, date(2026, 10, 7))

        # No two series are the same payee shape, direction, and amount in one account: a series
        # reported twice would be the grouping failing to keep one thing as one.
        keys = [(s.account, s.shape, s.direction, s.usual_minor, s.cadence) for s in found]
        assert len(keys) == len(set(keys))
        assert all(s.count >= 3 for s in found)
