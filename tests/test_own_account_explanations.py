"""An account without Spaces gets the same per-change explanation as a whole-account walk.

A current account's anchors are few: the bank's own balance defines the opening
and a held statement is the check. When the statement's balance differs from
what the rows predict, the page said only that rows are missing, duplicated, or
mis-dated. Each scenario adds ONE fault to the invented account in
`own_account_corpus`, where the statement and the bank state the truth and the
feed is what is wrong, and says which row the explanation must name.
"""

from __future__ import annotations

import html
import re
from dataclasses import replace
from datetime import date

import pytest

from bank_balance_corpus import at, balance_body, item
from obdi.balance_anchors import STATEMENT, Anchor, derive_opening, effective_opening, own_walk
from obdi.balance_chart import build_balance_chart
from obdi.family_anchors import families_of
from obdi.fault_explanation import (
    COUNTED_NOT_LISTED,
    LISTED_NOT_COUNTED,
    ONE_ROW,
    TIMING_PAIR,
    explain_walk,
)
from obdi.fault_structure import select_explained
from obdi.ledger import build_ledger
from obdi.models import TransactionStatus
from obdi.sighting_placement import SightingPlacement
from obdi.store import Store
from obdi.web_ledger import render_ledger
from own_account_corpus import BANK_ON_THE_2ND, OWN_MAP, own_household
from test_space_attribution import FEED, MAIN, Household, pay

STATEMENT_SOURCE = "starling-statement-pdf"

GHOST = item("o-ghost", -4000, at(10), name="Ghost")


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(**kwargs):
        store = own_household(tmp_path, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


def opening_of(store):
    return effective_opening(store, MAIN, families=families_of(store, OWN_MAP))


def only_change(store):
    opening = opening_of(store)
    assert opening.explanation is not None
    (change,) = opening.explanation.changes
    return change


def page(store) -> str:
    ledger = build_ledger(store, MAIN, None, bound=False, families=families_of(store, OWN_MAP))
    return html.unescape(
        re.sub(r"<[^>]+>", "", render_ledger(ledger, unmasked=False).decode("utf-8"))
    )


#: A statement that also lists a payment of the ghost's size two days after it, which the
#: feed holds as a payment of its own: 3775.00 - 18.00 - 40.00 - 70.00 - 90.00 + 1200.00 = 4757.00.
STATEMENT_WITH_PARKING = [
    "SUMMARY|05/09/2026 - 30/09/2026|3775.00|1200.00|218.00|4757.00",
    "HEAD",
    "OPENING|3775.00",
    "ROW|06/09/2026|FASTER PAYMENT|Bakery||18.00|3757.00",
    "ROW|12/09/2026|FASTER PAYMENT|Parking||40.00|3717.00",
    "ROW|15/09/2026|FASTER PAYMENT|Grocer||70.00|3647.00",
    "ROW|22/09/2026|FASTER PAYMENT|Garage||90.00|3557.00",
    "ROW|25/09/2026|DIRECT CREDIT|Refund|1200.00||4757.00",
    "END",
]
PARKING = item("o-parking", -4000, at(12), name="Parking")


class TestAHealthyAccount:
    def test_Explanation_WhenNothingDiffers_ThereIsNothingToExplain(self, make):
        opening = opening_of(make())

        assert opening.differing == []
        assert opening.explanation is None


class TestAStatementThatDisagreesWithTheRows:
    def test_Explanation_WhenStatementIsShortByOneFeedOnlyPayment_NamesThatPayment(self, make):
        change = only_change(make(extra=[GHOST]))

        assert change.source == STATEMENT_SOURCE
        # The window runs from the bank balance that defined the opening to the statement.
        assert change.after == date(2026, 9, 2)
        assert change.day == date(2026, 9, 30)
        assert COUNTED_NOT_LISTED in change.holds
        assert ONE_ROW in change.holds
        (note,) = change.counted_not_listed.named
        assert note.direction == "out"
        assert ("starling", "2026-09-10") in note.dates
        assert note.sources == ("starling",)
        assert note.status == "booked"

    def test_Explanation_WhenStatementListsARowTheStoreDoesNotCount_NamesItAndWhy(self, make):
        store = make()
        Household(store, OWN_MAP).arrive(
            replace(pay(MAIN, FEED, "o-garage", -9000, 22, "Garage"), status=TransactionStatus.VOID)
        )

        change = only_change(store)

        assert LISTED_NOT_COUNTED in change.holds
        (note,) = change.listed_not_counted.named
        assert note.why == "void"
        assert STATEMENT_SOURCE in note.sources
        assert ("starling-statement-pdf", "2026-09-22") in note.dates

    def test_Explanation_WhenTheStatementListsAPaymentOfTheSameSizeNearby_SaysSoWithoutAFigure(
        self, make
    ):
        change = only_change(make(extra=[GHOST, PARKING], statement=STATEMENT_WITH_PARKING))

        (note,) = change.counted_not_listed.named
        assert note.lookalike is not None
        assert note.lookalike.found is True
        assert note.lookalike.days_away == 2
        assert note.lookalike.sighted_on == "another row"
        assert note.lookalike.source == STATEMENT_SOURCE

    def test_Explanation_WhenNoPaymentOfThatSizeIsListedNearby_SaysTheStatementListsNone(
        self, make
    ):
        change = only_change(make(extra=[GHOST]))

        (note,) = change.counted_not_listed.named
        assert note.lookalike is not None
        assert note.lookalike.found is False


class TestABankBalanceFetchedMidDay:
    def test_Explanation_WhenAPendingRowIsHeldAtTheFetch_ItIsNotMistakenForTheCause(self, make):
        pending = item("o-card", -1500, at(23, 7), status="PENDING", name="Card")
        store = make(
            extra=[GHOST, pending],
            balances=[
                (at(2, 11), balance_body(BANK_ON_THE_2ND, BANK_ON_THE_2ND)),
                # 380000 - 2500 - 1800 - 7000 - 9000, without the ghost the bank never counted.
                (at(23, 8), balance_body(359700, 359700, pending=-1500)),
            ],
        )

        opening = opening_of(store)
        assert opening.explanation is not None
        bank = next(c for c in opening.explanation.changes if c.source == "starling")

        # The cleared figure leaves the pending row out, so the 4000 is the ghost alone.
        assert ONE_ROW in bank.holds
        assert bank.one_row is not None
        assert ("starling", "2026-09-10") in bank.one_row.dates
        assert LISTED_NOT_COUNTED not in bank.holds


class TestATimingDifferenceBetweenTwoStatements:
    """At account level a statement's own placement of the rows it lists already absorbs a
    payment the feed dates the day after the closing (the anchor agrees, so there is nothing to
    say). The explanation is for a judgement that did not apply that dating, as here."""

    def test_Explanation_WhenAPaymentIsOnDifferentSidesOfTwoClosingsByTheTwoDatings_SaysOnePair(
        self, tmp_path
    ):
        first, second = date(2026, 9, 10), date(2026, 9, 20)
        straddler = pay(MAIN, FEED, "o-late", -5000, 11, "Late")
        anchors = [
            Anchor(date(2026, 9, 1), 100000, STATEMENT, stated_by=STATEMENT_SOURCE),
            Anchor(first, 95000, STATEMENT, stated_by=STATEMENT_SOURCE),
            Anchor(second, 95000, STATEMENT, stated_by=STATEMENT_SOURCE),
        ]
        # The store dates the payment the 11th, after the first closing; the statement the 10th.
        opening = derive_opening(MAIN, anchors, [straddler])
        placement = SightingPlacement({STATEMENT_SOURCE: {straddler.entity_id: first}})

        with Store(tmp_path / "unit.sqlite3") as store:
            walk = own_walk(MAIN, opening)
            explanation = explain_walk(
                store,
                MAIN,
                walk,
                {MAIN: [straddler]},
                placement,
                selection=select_explained(walk),
            )

        (pair,) = explanation.changes
        assert pair.day == first
        assert pair.undone_on == second
        assert TIMING_PAIR in pair.holds
        assert pair.sides.count == 1
        assert pair.sides_negated is False


class TestWhatThePageAndTheChartShow:
    def test_Page_WhenTheStatementDiffers_ExplainsTheChangeAsAWholeAccountWalkDoes(self, make):
        text = page(make(extra=[GHOST]))

        assert (
            "The change at the end of 2026-09-30 (after 2026-09-02, stated by "
            "starling-statement-pdf" in text
        )
        assert "the store counts in the window that starling-statement-pdf does not list" in text
        assert "out row dated starling 2026-09-10" in text
        assert "seen by starling; booked" in text

    def test_Page_WhenTheStatementListsAPaymentOfTheSameSize_SaysWhichRowItIsSightedOn(self, make):
        text = page(make(extra=[GHOST, PARKING], statement=STATEMENT_WITH_PARKING))

        assert (
            "starling-statement-pdf lists a row of the same size and direction, to a "
            "different recipient, 2 days away, sighted on another stored row" in text
        )

    def test_Page_WhenNothingIsListedNearby_SaysTheStatementListsNoneWithinThirtyDays(self, make):
        text = page(make(extra=[GHOST]))

        assert (
            "starling-statement-pdf lists no row of the same size and direction within "
            "thirty days" in text
        )

    def test_Page_NeverCarriesAFigureADescriptionOrAPayee(self, make):
        text = page(make(extra=[item("o-ghost", -4321, at(10), name="Distinct Payee")]))

        assert "The change at the end of" in text
        assert "Distinct Payee" not in text
        for figure in ("43.21", "4,321", "4321"):
            assert figure not in text

    def test_Page_WhenNothingDiffers_SaysNothingOfChanges(self, make):
        assert "The change at the end of" not in page(make())

    def test_Chart_WhenTheStatementDiffers_ShowsTheStructureAndCountsTheChangeExplained(self, make):
        store = make(extra=[GHOST])

        chart = build_balance_chart(store, MAIN, families=families_of(store, OWN_MAP))

        assert chart.scope == "own"
        (step,) = chart.structure.whole.steps
        assert step.explained is True
        assert chart.structure.whole.permanent_explained == (step,)
