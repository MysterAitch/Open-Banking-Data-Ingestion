"""A fault in an export's window is narrowed to the first row after which the balances part.

The export states a balance after every row, so within a window whose difference
changes the rows can be walked in the file's own sequence against what the store
holds for each. The household is `bank_balance_corpus` with four payments added on
the 6th and a Space payment a day from the 7th to the 27th (so a fault or two does not
change how the export's balance is read). The export lists them in date order, so the
third payment of the 6th is the sixth row of 33 in the export's own sequence:

    position  what                                  feed time
       1      Deposit                               1st
       2      Salary                                1st
       3      Cafe                                  4th
       4      A   -10.00                            6th 08:00
       5      B   -20.00                            6th 09:00
       6      C   -33.00                            6th 10:00
       7      D   -40.00                            6th 11:00
       8..33   the Space payments, Water, Grocer, Gym, Garage, and Refund

KNOWN ANSWERS, decided before the first run. Each scenario changes ONE thing:

    the store lacks C                       parts after row 6; the store holds nothing there
    the store holds C at another size       parts after row 6; a row of another size
    the store holds a surplus 09:30 row     parts after row 6; C is held as listed, and the
                                            surplus row sits between rows 5 and 6
    the store holds a surplus 23:00 row     the rows never part inside the window: the surplus
                                            falls after the last of them (row 7)
    nothing is wrong                        no change, so no parting
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from typing import ClassVar

import pytest

from bank_balance_corpus import EXPORT_ROWS, at, bills_items, household, item, main_items
from obdi.balance_anchors import effective_opening
from obdi.export_parting import (
    ANOTHER_SIZE,
    HELD,
    NOTHING,
    Counterpart,
    Surplus,
    first_parting,
)
from obdi.family_anchors import ExportRow, families_of
from test_export_cuts import Row
from test_export_dating import render as render_escaped
from test_space_attribution import MAIN, MAP

DAY_SIX = [
    ("A", -1000, 8),
    ("B", -2000, 9),
    ("C", -3300, 10),
    ("D", -4000, 11),
]

C_MINOR = -3300
CAFE_MINOR = -2500


#: One Space payment a day from the 7th to the 27th, so that a fault or two does not flip the
#: export's reading from the whole account's (the steps that tell the readings apart).
SPACE_DAYS = range(7, 28)


def feed(*extra):
    items = [
        item(f"m-{name.lower()}", minor, at(6, hour), name=name) for name, minor, hour in DAY_SIX
    ]
    return [*main_items(), *items, *extra]


def bills() -> list:
    return [*bills_items(), *(item(f"b-s{d}", -(100 + d), at(d), name=f"S{d}") for d in SPACE_DAYS)]


def lose(store, minor: int, day: int) -> None:
    """Remove the one row of this size dated that day and its sightings, as a bad merge might."""
    marks = (minor, f"2026-09-{day:02}")
    store.connection.execute(
        "DELETE FROM transaction_sources WHERE entity_id IN "
        "(SELECT entity_id FROM transactions WHERE amount_minor = ? AND value_date = ?)",
        marks,
    )
    store.connection.execute(
        "DELETE FROM transactions WHERE amount_minor = ? AND value_date = ?", marks
    )
    store.connection.commit()


def export() -> list[Row]:
    """Every row in date order, the day's rows in the order of the household's table."""
    rows = [
        *EXPORT_ROWS,
        *(Row(name, minor, 6, 6) for name, minor, _ in DAY_SIX),
        *(Row(f"S{d}", -(100 + d), d, d, space=True) for d in SPACE_DAYS),
    ]
    return sorted(rows, key=lambda r: r.export_day)


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(main):
        store = household(tmp_path, [], main=main, bills=bills(), export=export())
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


def explained(store):
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    assert opening.family.explanation is not None
    return opening.family.explanation


def only_parting(store):
    (change,) = explained(store).changes
    assert change.parting is not None
    return change.parting


class TestAnExportsFirstPartingFromTheStore:
    def test_Parting_WhenTheStoreLacksTheThirdRowOfTheDay_NamesThatRowAndSaysNothingIsHeld(
        self, make
    ):
        store = make(feed())
        lose(store, C_MINOR, 6)

        found = only_parting(store)

        assert (found.position, found.total, found.day, found.direction) == (
            6, 33, date(2026, 9, 6), "out",
        )
        assert (found.holds, found.surplus_before, found.after_last) == (NOTHING, 0, False)

    def test_Parting_WhenTheStoreHoldsTheRowAtAnotherSize_NamesThatRowAndSaysAnotherSize(
        self, make
    ):
        store = make(feed())
        store.connection.execute(
            "UPDATE transactions SET amount_minor = ? WHERE amount_minor = ?", (-3100, C_MINOR)
        )
        store.connection.commit()

        found = only_parting(store)

        assert (found.position, found.holds, found.surplus_before) == (6, ANOTHER_SIZE, 0)

    def test_Parting_WhenTheStoreHoldsASurplusRowBetweenTheSecondAndThird_SaysWhereItFalls(
        self, make
    ):
        surplus = item("m-x", -500, at(6, 9, 30), name="X")
        found = only_parting(make(feed(surplus)))

        assert (found.position, found.holds, found.surplus_before, found.after_last) == (
            6, HELD, 1, False,
        )

    def test_Parting_WhenTheSurplusRowFallsAfterTheLastRowOfTheWindow_SaysTheyNeverPartInside(
        self, make
    ):
        surplus = item("m-x", -500, at(6, 23), name="X")
        found = only_parting(make(feed(surplus)))

        assert (found.position, found.after_last, found.surplus_before) == (7, True, 1)

    def test_Parting_WhenNothingIsWrong_NoChangeAndNoPartingAreSaid(self, make):
        assert explained(make(feed())).changes == ()

    def test_Parting_WhenAnEarlierFaultIsInAnEarlierWindow_EachWindowNamesItsOwnRow(self, make):
        store = make(feed())
        lose(store, CAFE_MINOR, 4)
        lose(store, C_MINOR, 6)

        changes = explained(store).changes

        parted = [(c.day.day, c.parting.position) for c in changes if c.parting is not None]
        assert parted == [(4, 3), (6, 6)]


class TestThePartingOnThePage:
    @staticmethod
    def page(store) -> str:
        return html.unescape(render_escaped(store))

    def test_Page_WhenTheStoreLacksARow_SaysWhichRowByPositionDateAndDirection(self, make):
        store = make(feed())
        lose(store, C_MINOR, 6)
        page = self.page(store)

        assert (
            "first part after row 6 of 33 in the export's own sequence, dated "
            '<span class="mono nowrap">2026-09-06</span>, a payment out. '
            "There the store holds no row in its place."
        ) in page

    def test_Page_WhenTheStoreHoldsASurplusRow_SaysItFallsBetweenTwoRows(self, make):
        surplus = item("m-x", -500, at(6, 9, 30), name="X")
        page = self.page(make(feed(surplus)))

        assert (
            "There the store holds that row, as the export lists it, and 1 surplus row the "
            "export does not list between the row before it and this one."
        ) in page

    def test_Page_NeverCarriesAFigureOrADescription(self, make):
        surplus = item("m-x", -4321, at(6, 9, 30), name="Distinctive Payee")
        page = self.page(make(feed(surplus)))

        assert "Distinctive Payee" not in page
        assert "43.21" not in page
        assert "4321" not in page


class TestThePartingRuleOnItsOwn:
    """The rule without a store: three rows, the second missing.

    A surplus row of the missing row's size does not hide the parting at the second,
    wherever the store places it after that row: the running sum has parted by then.
    """

    ROWS: ClassVar[list[tuple[int, ExportRow]]] = [
        (n + 1, ExportRow(date(2026, 9, 6), minor, 0, 0))
        for n, minor in enumerate((-100, -200, -300))
    ]

    def test_FirstParting_WhenALaterSurplusReplacesTheMissingRow_StillPartsAtTheMissingRow(self):
        held = {
            1: Counterpart(HELD, -100),
            2: Counterpart(NOTHING),
            3: Counterpart(HELD, -300),
        }
        late = Surplus(date(2026, 9, 6), -200, None)

        found = first_parting(self.ROWS, held, [late], 3)

        assert found is not None
        assert (found.position, found.holds) == (2, NOTHING)

    def test_FirstParting_WhenASurplusRowIsBeforeAHeldRowAndEqualsTheMissingOne_PartsAtTheHeldRow(
        self
    ):
        stamp = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)
        held = {
            1: Counterpart(HELD, -100, at=datetime(2026, 9, 6, 8, 0, tzinfo=UTC)),
            2: Counterpart(NOTHING),
            3: Counterpart(HELD, -300, at=datetime(2026, 9, 6, 10, 0, tzinfo=UTC)),
        }
        between = Surplus(date(2026, 9, 6), -200, stamp)

        found = first_parting(self.ROWS, held, [between], 3)

        assert found is not None
        assert found.position == 2

    def test_FirstParting_WhenTheMissingRowIsNotReplaced_PartsAtThatRow(self):
        held = {
            1: Counterpart(HELD, -100),
            2: Counterpart(NOTHING),
            3: Counterpart(HELD, -300),
        }

        found = first_parting(self.ROWS, held, [], 3)

        assert found is not None
        assert (found.position, found.holds) == (2, NOTHING)
