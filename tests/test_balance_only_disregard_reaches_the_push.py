"""A stated balance disregarded on a balance-only account is not sent to the budgeting tool either.

A SECOND-ROUND REVIEW FINDING, held as a failing test. An account tracked by its stated balances
alone has its movements derived from them: one "unitemised change" between each consecutive pair.
The account's own reading derives them from the balances still in use
(`effective_opening`, after `_gather` leaves out a disregarded one). The push derives them from
every stated balance the store holds (`unitemised_for_store` reads `stated_anchors`), so a
balance a person has disregarded still shapes what is sent, and the page and the budgeting tool
show different movements for the same account.

THE ACCOUNT, with the answer decided before the first run. `mortgage` is balance-only, with
2,000.00 owed stated for 2026-01-31, 1,992.00 for 2026-02-28, and 1,980.00 for 2026-03-31.
February's is disregarded.

  the account's reading   one change, 20.00 on 2026-03-31.
  the push                the same one change. (Before the disregard both hold two: 8.00 on
                          2026-02-28 and 12.00 on 2026-03-31, a control.)

How a person reaches this was not established: the account page lists a balance for disregarding
only on a day several sources share or where one differs, and whether a balance-only account's
page ever does was not rendered. The domain call and the POST route accept it.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.balance_anchors import (
    STATED,
    disregard_balance,
    effective_opening,
    record_stated_anchor,
    unitemised_for_store,
)
from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.store import Store

TODAY = date(2026, 4, 30)


@pytest.fixture
def mortgage(tmp_path):
    with Store(tmp_path / "store.sqlite3") as store:
        store.declare_account(
            AccountRecord(ref=AccountRef("mortgage"), kind=BALANCE_ONLY_KIND, label="Mortgage")
        )
        for day, figure in (
            ("2026-01-31", "-2000.00"),
            ("2026-02-28", "-1992.00"),
            ("2026-03-31", "-1980.00"),
        ):
            record_stated_anchor(store, "mortgage", day, figure, today=TODAY)
        store.connection.commit()
        yield store


def read_by_the_account(store: Store) -> list[tuple[str, int]]:
    derived = effective_opening(store, "mortgage").unitemised
    return sorted((t.value_date.isoformat(), t.amount_minor) for t in derived)


def sent_by_the_push(store: Store) -> list[tuple[str, int]]:
    return sorted((t.value_date.isoformat(), t.amount_minor) for t in unitemised_for_store(store))


class TestADisregardedStatedBalanceOnABalanceOnlyAccount:
    def test_Push_BeforeAnythingIsDisregarded_SendsWhatTheAccountReads(self, mortgage):
        assert read_by_the_account(mortgage) == [("2026-02-28", 800), ("2026-03-31", 1200)]
        assert sent_by_the_push(mortgage) == read_by_the_account(mortgage)

    def test_Push_WhenTheMiddleBalanceIsDisregarded_SendsWhatTheAccountReads(self, mortgage):
        assert disregard_balance(mortgage, "mortgage", "2026-02-28", STATED, STATED)

        assert read_by_the_account(mortgage) == [("2026-03-31", 2000)]
        assert sent_by_the_push(mortgage) == read_by_the_account(mortgage), (
            "the push still derives a change from the balance a person disregarded"
        )
