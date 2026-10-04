"""A row's "Dates and joins" says each source's statements once, however often it was fetched.

The feed is fetched again on every pull and each fetch re-sights a row by its own id, stating the
same fields. The page used to list the founding sighting and then the same fields again as "the
same source's own id again", and a source fetched forty times said so forty-one ways.

KNOWN ANSWERS, decided before the first run. The payment is made 14 September 10:00 UTC (11:00 on
London's summer clock), settles 15 September 03:00 UTC (04:00), and its record was last touched
15 September 05:30 UTC (06:30); the feed item lists the touch first, to show the order is not the
item's. Every fetch carries one other payment too, so that each fetch is a different artefact.

    one fetch
        one line, "founded this row", fields in the order events happen
    the same item in six fetches
        one line, saying it was sighted again in 5 later fetches
    a re-fetch that states a settlement time it did not and a later touch
        its own line saying what changed, the unchanged first line kept, and the identical
        fetch after it counted on the changed line
    the aggregator's item in three fetches
        one line, saying it was sighted again in 2 later fetches
    the account's counts of rows by join
        the same with one fetch or six: a repeat sighting by a source's own id is not a join
"""

from __future__ import annotations

import html
import re

import pytest

from late_settlement_corpus import Payment, aggregator_item
from obdi.family_anchors import families_of
from obdi.ledger import build_ledger
from obdi.store import Store
from obdi.web_ledger import render_ledger
from round_up_corpus import card_payment
from test_family_anchors import land_evidence
from test_id_tier import aggregator_artefact, arrive, feed_artefact
from test_space_attribution import MAIN, MAP

PAYMENT = Payment("f-a", "Garage", 2000, "2026-09-14T10:00:00.000Z", None, None, 14)
FIRST_TOUCH = "2026-09-15T05:30:00.000Z"
SECOND_TOUCH = "2026-09-16T08:15:00.000Z"
SETTLED = "2026-09-15T03:00:00.000Z"

MADE = "transactionTime 2026-09-14 11:00"
SETTLEMENT = "settlementTime 2026-09-15 04:00"
FIRST = "updatedAt 2026-09-15 06:30"
SECOND = "updatedAt 2026-09-16 09:15"


@pytest.fixture
def bare(tmp_path):
    with Store(tmp_path / "lines.sqlite3") as store:
        land_evidence(store)
        yield store


def item(*, settled: bool, touch: str) -> dict:
    """The payment as the feed states it, the touch first."""
    stated = {"updatedAt": touch, **card_payment("f-a", "Garage", 2000, 14)}
    if settled:
        stated["settlementTime"] = SETTLED
    return stated


def other(number: int) -> dict:
    return card_payment(f"f-other-{number}", "Other", 100 + number, 1 + number)


def fetch(store: Store, number: int, *, settled: bool = True, touch: str = FIRST_TOUCH) -> None:
    arrive(store, feed_artefact([item(settled=settled, touch=touch), other(number)]), MAIN)


def page_of(store: Store) -> str:
    ledger = build_ledger(store, MAIN, "2026-09", bound=True, families=families_of(store, MAP))
    return render_ledger(ledger, unmasked=False).decode()


def lines_of(page: str, source: str) -> list[str]:
    """The sighting lines of the payment's row for one source, without markup."""
    found = re.findall(rf'<p class="muted"><strong>{source}</strong> - (.*?)</p>', page)
    return [html.unescape(line) for line in found if "2026-09-14 11:00" in html.unescape(line)]


class TestTheFeedFetchedAgain:
    def test_Row_WhenFetchedOnce_HasOneLineWithTheFieldsInTheOrderEventsHappen(self, bare):
        fetch(bare, 1)

        assert lines_of(page_of(bare), "starling") == [
            f"founded this row: {MADE}, {SETTLEMENT}, {FIRST}"
        ]

    def test_Row_WhenFetchedSixTimesIdentically_HasOneLineCountingTheFiveLaterFetches(self, bare):
        for number in range(1, 7):
            fetch(bare, number)

        assert lines_of(page_of(bare), "starling") == [
            "founded this row, sighted again by its own id in 5 later fetches: "
            f"{MADE}, {SETTLEMENT}, {FIRST}"
        ]

    def test_Row_WhenFetchedTwice_SaysOneLaterFetchInTheSingular(self, bare):
        fetch(bare, 1)
        fetch(bare, 2)

        (line,) = lines_of(page_of(bare), "starling")
        assert "sighted again by its own id in 1 later fetch:" in line

    def test_Row_WhenAReFetchStatesSomethingNew_KeepsItAsItsOwnLineAndSaysWhatChanged(self, bare):
        fetch(bare, 1, settled=False)
        fetch(bare, 2, settled=True, touch=SECOND_TOUCH)
        fetch(bare, 3, settled=True, touch=SECOND_TOUCH)

        assert lines_of(page_of(bare), "starling") == [
            f"founded this row: {MADE}, {FIRST}",
            "the same source's own id again (settlementTime appeared; updatedAt moved from "
            "2026-09-15 06:30 to 2026-09-16 09:15), sighted again by its own id in 1 later "
            f"fetch: {MADE}, {SETTLEMENT}, {SECOND}",
        ]

    def test_Row_WhenAReFetchStopsStatingAField_SaysItIsNoLongerStated(self, bare):
        fetch(bare, 1, settled=True)
        fetch(bare, 2, settled=False)

        lines = lines_of(page_of(bare), "starling")
        assert len(lines) == 2
        assert "(settlementTime is no longer stated)" in lines[1]


class TestTheAggregatorFetchedAgain:
    def test_Row_WhenTheAggregatorStatesTheSameItemInThreeFetches_HasOneLine(self, bare):
        fetch(bare, 1)
        for number in range(1, 4):
            filler = Payment(f"f-agg-{number}", "Agg", 100 + number, "", None, None, 1 + number)
            arrive(
                bare,
                aggregator_artefact(
                    [aggregator_item(PAYMENT, link=True), aggregator_item(filler)]
                ),
                MAIN,
            )

        assert lines_of(page_of(bare), "truelayer") == [
            "joined to the row by id, sighted again by its own id in 2 later fetches: "
            "timestamp 2026-09-14 11:00"
        ]


class TestTheAccountsCountOfJoins:
    @pytest.mark.parametrize("fetches", [1, 6])
    def test_Account_WhateverTheNumberOfFetches_CountsTheRowByItsWeakestJoinOnly(
        self, bare, fetches
    ):
        for number in range(1, fetches + 1):
            fetch(bare, number)
            filler = Payment(f"f-agg-{number}", "Agg", 100, "", None, None, 1)
            arrive(
                bare,
                aggregator_artefact(
                    [aggregator_item(PAYMENT, link=True), aggregator_item(filler)]
                ),
                MAIN,
            )

        sentence = re.search(
            r"How the rows were joined \([^)]*\)</summary><p>(.*?)\.</p>", page_of(bare)
        )
        assert sentence is not None
        # The payment is joined by id once whatever the fetches; each fetch adds two other
        # payments (one from the feed, one from the aggregator) that no second source reached.
        assert html.unescape(sentence.group(1)) == (
            f"1 row joined by id, {2 * fetches} rows with no join"
        )
