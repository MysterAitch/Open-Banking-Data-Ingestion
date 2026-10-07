"""A Space-blind export's balances are tested only where its own sequence is cut cleanly.

The export's balance column follows its FILE order, and file order is the one
valid sequence of its rows (every step follows). A balance after row k is the
sum of the first k rows of that sequence, which equals "every row dated on or
before day D" only where no row dated after D comes before a row dated on or
before it. Every household here lists the same payments in the feed and in the
export and nothing is missing, so the KNOWN ANSWER is that every anchor the page
shows agrees, and a day with no clean cut shows no anchor rather than a wrong one.

The truth is `export_balance`: the sum of the rows by the day the export dates
them, which is what an anchor on that day must state.
"""

from __future__ import annotations

import pathlib
import random
from dataclasses import dataclass, replace
from datetime import date

import pytest

from obdi.balance_anchors import FAMILY, OPENED, effective_opening
from obdi.core.models import Transaction, TransactionStatus
from obdi.fault_explanation import Lookalike, _nearest, explain_walk
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import import_file, pair_transfers_across_store
from obdi.ingest.sighting_placement import SightingPlacement
from obdi.ingest.store import Store
from test_export_dating import render
from test_family_anchors import CSV_HEADER, land_evidence, leg
from test_space_attribution import BILLS, FEED, MAIN, MAP, Household, pay


@dataclass(frozen=True)
class Row:
    """One payment: the day the feed dates it and the day the export dates it."""

    name: str
    minor: int
    feed_day: int
    export_day: int
    space: bool = False
    #: The figure the export lists where it differs from the feed's.
    export_minor: int | None = None

    @property
    def listed(self) -> int:
        return self.minor if self.export_minor is None else self.export_minor


def export_lines(
    rows: list[Row], *, balanced_as: list[Row] | None = None, opening_minor: int = 0
) -> list[str]:
    """The export listing `rows` in the order given, its balance moving with every
    row in the order of `balanced_as` (the listing's own order unless said), from
    `opening_minor`."""
    balance = opening_minor
    after: dict[int, int] = {}
    for item in balanced_as or rows:
        balance += item.listed
        after[id(item)] = balance
    lines = [CSV_HEADER]
    for item in rows:
        lines.append(
            f"{item.export_day:02}/09/2026,{item.name},{item.name},FASTER PAYMENT,"
            f"{item.listed / 100:.2f},{after[id(item)] / 100:.2f},"
        )
    return lines


def feed_rows(rows: list[Row]) -> list[Transaction]:
    return [
        pay(BILLS if r.space else MAIN, FEED, f"f-{r.name}", r.minor, r.feed_day, r.name)
        for r in rows
    ]


def build(
    tmp_path: pathlib.Path,
    rows: list[Row],
    *,
    file_order: list[Row] | None = None,
    balanced_as: list[Row] | None = None,
    created: str = "2026-09-01T09:30:00Z",
    feed_only: list[Transaction] | None = None,
    export_opens_at: int = 0,
) -> Store:
    """The feed and the export both hold `rows`; the export lists them in `file_order`."""
    store = Store(tmp_path / "household.sqlite3")
    home = Household(store, MAP)
    land_evidence(store, created=created)
    path = tmp_path / "export.csv"
    path.write_text(
        "\n".join(
            export_lines(file_order or rows, balanced_as=balanced_as, opening_minor=export_opens_at)
        )
        + "\n",
        encoding="utf-8",
    )
    import_file(store, path, account_id=MAIN, account_map=MAP)
    home.arrive(*feed_rows(rows), *(feed_only or []))
    return store


def export_balance(rows: list[Row], day: int) -> int:
    """The balance at the end of `day`: every row the export dates on or before it."""
    return sum(r.listed for r in rows if r.export_day <= day)


def stated(store: Store):
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    return opening, opening.family


def stated_days(walk) -> dict[int, int]:
    return {r.day.day: r.balance_minor for r in walk.readings}


#: Enough Space payments, one every other day from the 20th, for the export's
#: steps to tell "whole account" from "main only" however its first days are shaped.
TAIL = [
    Row(f"Tail{d}", -(300 + d), d, d, space=d % 2 == 0) for d in range(20, 28)
]


@pytest.fixture
def make(tmp_path):
    opened: list[Store] = []

    def made(rows: list[Row], **kwargs) -> Store:
        store = build(tmp_path, rows, **kwargs)
        opened.append(store)
        return store

    yield made
    for store in opened:
        store.close()


#: Days 2, 3 and 4, each with one row, except that the export lists the row it
#: dates the 3rd BEFORE the second row it dates the 2nd: its sequence runs
#: 2, 3, 2, 4. KNOWN ANSWER: the 2nd has no clean cut (the 3rd's row sits between
#: its two), so it states nothing. The 3rd is cut cleanly after the straggler
#: (everything before that point is dated on or before it), and a figure taken
#: from the 3rd's own single row would omit the straggler. The 4th and the day
#: before the first row are clean.
INTERLEAVED_FIRST = Row("Salary", 100000, 2, 2)
INTERLEAVED_LATE = Row("Rent", -40000, 3, 3, space=True)
INTERLEAVED_STRAGGLER = Row("Shop", -2500, 2, 2)
INTERLEAVED_LAST = Row("Cafe", -700, 4, 4)
INTERLEAVED = [INTERLEAVED_FIRST, INTERLEAVED_LATE, INTERLEAVED_STRAGGLER, INTERLEAVED_LAST]


class TestAnExportWhoseSequenceInterleavesItsDates:
    def test_Anchors_WhenExportDatesInterleave_NoAnchorStatesAFigureTheRowsCannotReach(
        self, make
    ):
        rows = [*INTERLEAVED, *TAIL]
        store = make(rows)

        opening, walk = stated(store)

        assert opening.readings[0].anchor.basis == OPENED
        assert walk.differing == []
        for reading in walk.readings:
            assert reading.balance_minor == export_balance(rows, reading.day.day)

    def test_Anchors_WhenExportDatesInterleave_TheDaysWithoutACleanCutShowNoAnchor(self, make):
        rows = [*INTERLEAVED, *TAIL]
        store = make(rows)

        _, walk = stated(store)

        days = stated_days(walk)
        assert 2 not in days
        assert {1, 3, 4} <= set(days)
        assert days[3] == 57500
        assert days[4] == 56800
        assert {r.agrees for r in walk.readings} == {True}


def january(
    *, first_in: bool, equal: bool, day_before: bool
) -> tuple[list[Row], list[Row]]:
    """The January shape on the 10th to 13th: two rows on the 10th (one in, one out),
    a row the feed dates the 11th and the export the 12th, two the feed dates the
    12th and the export the 13th, and one both date the 12th. The export lists
    nothing on the 11th. Returns (every row, the export's file order)."""
    inflow = Row("In", 5000, 10, 10)
    outflow = Row("Out", -5000 if equal else -1200, 10, 10)
    rows = [
        Row("Early", 2000, 9, 9) if day_before else None,
        inflow, outflow,
        Row("Late", -300, 11, 12),
        Row("Same", -450, 12, 12),
        Row("Twin1", -125, 12, 13),
        Row("Twin2", 700, 12, 13),
    ]
    held = [r for r in rows if r is not None]
    order = [r for r in held if r not in (inflow, outflow)]
    at = next(i for i, r in enumerate(order) if r.export_day > 10)
    order[at:at] = [inflow, outflow] if first_in else [outflow, inflow]
    return held, order


class TestAnExportWhoseSequenceIsInDateOrder:
    def test_Anchors_WhenExportIsInDateOrder_EveryDayHasAnAnchor(self, make):
        rows = [*sorted(INTERLEAVED, key=lambda r: r.export_day), *TAIL]
        store = make(rows)

        _, walk = stated(store)

        assert {2, 3, 4} <= set(stated_days(walk))
        assert walk.differing == []

    def test_Opening_WhenNothingIsMissing_IsNilAndFamily(self, make):
        store = make([*sorted(INTERLEAVED, key=lambda r: r.export_day), *TAIL])

        opening, _ = stated(store)

        assert opening.readings[0].anchor.basis == OPENED
        assert {r.anchor.basis for r in opening.readings[1:]} == {FAMILY}
        assert date(2026, 8, 31) == opening.readings[0].anchor.day


#: Two rows on the 5th, which the export's balance column takes in one order and
#: its file lists in the other.
FUEL = Row("Fuel", -900, 5, 5)
BREAD = Row("Bread", -300, 5, 5)


class TestAnExportListedInADifferentOrderFromItsBalances:
    def test_Anchors_WhenListedNewestFirst_EveryAnchorAgreesAndNoDayIsLost(self, make):
        rows = [*sorted(INTERLEAVED, key=lambda r: r.export_day), *TAIL]
        store = make(rows, file_order=list(reversed(rows)), balanced_as=rows)

        _, walk = stated(store)

        assert {2, 3, 4} <= set(stated_days(walk))
        assert walk.differing == []

    def test_Anchors_WhenOneDaysRowsAreListedAgainstTheirBalances_ThatDayStatesNothing(
        self, make
    ):
        """KNOWN ANSWER: which of the 5th's rows is last is contradicted by the
        balances, so the 5th has no anchor; every other day's agrees."""
        truth = [SALARY, FUEL, BREAD, *STEADY]
        store = make(truth, file_order=[SALARY, BREAD, FUEL, *STEADY], balanced_as=truth)

        _, walk = stated(store)

        days = stated_days(walk)
        assert 5 not in days
        assert {1, 2, 8} <= set(days)
        assert walk.differing == []

    def test_Anchors_WhenTheFirstDaysRowsAreListedAgainstTheirBalances_NoOpeningIsStated(
        self, make
    ):
        """The sequence's first row is the wrong one, so the balance before it is not
        the export's opening, and nothing is stated for the day before the 5th."""
        truth = [FUEL, BREAD, *STEADY]
        store = make(truth, file_order=[BREAD, FUEL, *STEADY], balanced_as=truth)

        _, walk = stated(store)

        days = stated_days(walk)
        assert not {4, 5} & set(days)
        assert 8 in days
        assert walk.differing == []

    def test_Anchors_WhenInterleavedAndListedNewestFirst_TheSameDaysAreCut(self, make):
        rows = [*INTERLEAVED, *TAIL]
        store = make(rows, file_order=list(reversed(rows)), balanced_as=rows)

        _, walk = stated(store)

        assert 2 not in stated_days(walk)
        assert stated_days(walk)[3] == 57500
        assert walk.differing == []


class TestTheJanuaryShape:
    """Two rows on one day, one in and one out, a row the feed and export date a day
    apart, and no balance for the day between. KNOWN ANSWER: nothing is missing, so
    every anchor agrees and the anchor list and the walk say the same of each; a
    day whose in and out are equal still states its balance, because the file's
    order says which came last."""

    @pytest.mark.parametrize("day_before", [False, True], ids=["no-day-before", "day-before"])
    @pytest.mark.parametrize("equal", [False, True], ids=["unequal", "equal"])
    @pytest.mark.parametrize("first_in", [True, False], ids=["in-first", "out-first"])
    def test_Anchors_WhenOneDayHoldsAnInAndAnOut_ListAndWalkAgreeOnEveryBalance(
        self, make, first_in, equal, day_before
    ):
        held, order = january(first_in=first_in, equal=equal, day_before=day_before)
        store = make([*held, *TAIL], file_order=[*order, *TAIL], created="2026-09-06T09:30:00Z")

        opening, walk = stated(store)

        assert [(r.anchor.day.day, r.agrees) for r in opening.readings[1:]] == [
            (r.day.day, r.agrees) for r in walk.readings
        ]
        assert walk.differing == []
        assert 10 in stated_days(walk)
        assert stated_days(walk)[10] == export_balance([*held, *TAIL], 10)

    def test_Anchors_WhenExportStatesNothingForTheDayBetween_NoBalanceIsInvented(self, make):
        held, order = january(first_in=True, equal=False, day_before=False)
        store = make([*held, *TAIL], file_order=[*order, *TAIL], created="2026-09-06T09:30:00Z")

        _, walk = stated(store)

        assert 11 not in stated_days(walk)


def fuzzed_household(tmp_path: pathlib.Path, seed: int, *, missing_rows: bool = True) -> Store:
    """A household of invented payments, shaped at random.

    Dates the export gives a day or two either side of the feed's, a listing that
    interleaves its dates, and transfers in both legs that the export never lists;
    with `missing_rows`, also rows only the feed counts and rows only the export lists.
    """
    rng = random.Random(seed)  # noqa: S311 - invented amounts, not security material
    rows = [
        Row(
            f"P{n}",
            rng.choice([-1, 1]) * rng.randrange(100, 9000),
            feed := rng.randrange(2, 26),
            feed + rng.choice([-1, 0, 0, 0, 1, 2]),
            space=rng.random() < 0.3,
        )
        for n in range(rng.randrange(8, 28))
    ]
    rows = [Row(r.name, r.minor, r.feed_day, max(1, r.export_day), r.space) for r in rows]
    listing = sorted(rows, key=lambda r: r.export_day)
    for _ in range(rng.randrange(0, 6)):
        at = rng.randrange(len(listing) - 1)
        listing[at], listing[at + 1] = listing[at + 1], listing[at]
    absent = rng.randrange(0, 3) if missing_rows else 0
    export_only = [
        Row(f"X{n}", -rng.randrange(100, 900), 3, rng.randrange(2, 20)) for n in range(absent)
    ]
    for extra in export_only:
        listing.insert(rng.randrange(len(listing) + 1), extra)
    feed_only = [
        pay(MAIN, FEED, f"f-only{n}", -rng.randrange(100, 900), rng.randrange(2, 26), f"Only{n}")
        for n in range(absent)
    ]
    for day in rng.sample(range(2, 26), rng.randrange(0, 3)):
        amount = rng.randrange(1000, 5000)
        feed_only.append(pay(MAIN, FEED, f"to-{day}", -amount, day, "To Bills", internal=True))
        feed_only.append(pay(BILLS, FEED, f"in-{day}", amount, day, "From Main", internal=True))
    return build(
        tmp_path / f"seed{seed}",
        rows,
        file_order=listing,
        feed_only=feed_only,
    )


class TestARandomHouseholdWithNothingMissing:
    """KNOWN ANSWER: every row is counted and listed, however the export dates and
    orders them, so no anchor may differ. Over 40 households, at least some have
    an interleaved listing (otherwise this would prove nothing)."""

    @pytest.mark.parametrize("seed", range(40))
    def test_Anchors_WhenNothingIsMissingAndTheListingInterleaves_NoneDiffer(
        self, tmp_path, seed
    ):
        (tmp_path / f"seed{seed}").mkdir()
        store = fuzzed_household(tmp_path, seed, missing_rows=False)
        try:
            opening, walk = stated(store)
        finally:
            store.close()

        assert walk.differing == []
        assert opening.differing == []
        assert walk.readings


class TestTheAnchorListAndTheWalkOverRandomHouseholds:
    @pytest.mark.parametrize("seed", range(40))
    def test_AnchorListAndWalk_WhenTheHouseholdIsFuzzed_GiveTheSameVerdictForEveryBalance(
        self, tmp_path, seed
    ):
        (tmp_path / f"seed{seed}").mkdir()
        store = fuzzed_household(tmp_path, seed)
        try:
            opening, walk = stated(store)
        finally:
            store.close()

        assert [(r.anchor.day, r.agrees) for r in opening.readings[1:]] == [
            (r.day, r.agrees) for r in walk.readings
        ]
        assert [r.difference_minor for r in opening.readings[1:]] == [
            r.difference_minor for r in walk.readings
        ]


class TestAnExportRowTheStoreHoldsAsADifferentFigure:
    """The feed has the payment at 30.00 out, the export lists 29.00 out (a fee split
    out). They are not one row, so the store counts both, and the stated balance is
    HIGHER than the rows by the feed's 30.00 from the 6th on."""

    def test_Walk_WhenTheExportListsAnotherFigure_OneChangeOnItsDayAndConstantAfter(self, make):
        fee = Row("Fee", -3000, 6, 6, export_minor=-2900)
        rows = [fee, *TAIL]
        store = make(rows)

        _, walk = stated(store)

        assert {r.difference_minor for r in walk.readings if r.day.day >= 6} == {3000}
        assert [c.day.day for c in walk.changes] == [6]


class TestTwoIdenticalExportRowsOnOneDay:
    """Two payments of the same figure, on the same day, with the same description,
    which the export has no id to tell apart. KNOWN ANSWER: both are counted and
    both are listed, so every anchor agrees."""

    def test_Anchors_WhenTwoIdenticalPaymentsShareADay_EveryAnchorAgrees(self, tmp_path):
        twin_a = Row("Coffee", -350, 6, 6)
        twin_b = Row("Coffee", -350, 6, 6)
        rows = [twin_a, twin_b, *TAIL]
        store = Store(tmp_path / "twin.sqlite3")
        try:
            home = Household(store, MAP)
            land_evidence(store)
            path = tmp_path / "export.csv"
            path.write_text("\n".join(export_lines(rows)) + "\n", encoding="utf-8")
            import_file(store, path, account_id=MAIN, account_map=MAP)
            home.arrive(
                pay(MAIN, FEED, "f-coffee-1", -350, 6, "Coffee"),
                pay(MAIN, FEED, "f-coffee-2", -350, 6, "Coffee"),
                *feed_rows(TAIL),
            )
            _, walk = stated(store)
        finally:
            store.close()

        assert walk.differing == []


class TestAMergedRowWhoseExportSightingBelongsToANearbyRowOfTheSameFigure:
    """Two payments of the same figure two days apart, the export dating each a day
    LATER than the feed. Whichever export row each merges with, both are counted
    and both are listed, so every anchor agrees."""

    def test_Anchors_WhenEqualPaymentsAreDatedADayLaterByTheExport_EveryAnchorAgrees(self, make):
        first = Row("Parking", -500, 6, 7)
        second = Row("Parking", -500, 8, 9)
        rows = [first, second, *TAIL]
        store = make(rows)

        _, walk = stated(store)

        assert walk.differing == []


#: A household with nothing wrong, to which each test below adds exactly one fault.
#: Salary, a Space payment, and the 6th's Garage, then the tail's Space payments.
SALARY = Row("Salary", 100000, 2, 2)
RENT = Row("Rent", -40000, 3, 3, space=True)
GARAGE = Row("Garage", -9000, 6, 6)
#: A main payment and a Space payment every day from the 8th, enough steps that one
#: broken step does not flip the export's reading from the whole account's.
STEADY = [
    Row(f"Steady{d}{kind}", -(300 + d + k), d, d, space=bool(k))
    for d in range(8, 28)
    for k, kind in enumerate("ab")
]
HEALTHY = [SALARY, RENT, GARAGE, *STEADY]


def explained(store: Store):
    _, walk = stated(store)
    assert walk.explanation is not None
    return walk.explanation


def only_change(store: Store):
    (change,) = explained(store).changes
    return change


def arrive_again(store: Store, row: Row, *, status: TransactionStatus) -> None:
    """The feed reports the payment again with another status, as a later pull would."""
    Household(store, MAP).arrive(
        replace(pay(MAIN, FEED, f"f-{row.name}", row.minor, row.feed_day, row.name), status=status)
    )


class TestEachFaultIsNamedByItsOwnArithmetic:
    """Every case adds ONE fault to the healthy household, so the KNOWN ANSWER is a
    single change on the day of the fault, explained by exactly the test named,
    naming the row by its date per source, direction, sources, and status."""

    @pytest.mark.parametrize(
        ("status", "why"),
        [
            (TransactionStatus.VOID, "void"),
            (TransactionStatus.FOLDED, "folded as the same money as another row"),
        ],
    )
    def test_Change_WhenAListedRowIsNotCounted_NamesItAndWhyNot(self, make, status, why):
        store = make(HEALTHY)
        arrive_again(store, GARAGE, status=status)

        change = only_change(store)

        assert change.day == date(2026, 9, 6)
        assert change.holds[0] == "listed-not-counted"
        (note,) = change.listed_not_counted.named
        assert note.why == why
        assert note.direction == "out"
        assert ("starling-csv", "2026-09-06") in note.dates
        assert "starling-csv" in note.sources
        assert change.counted_not_listed.count == 0

    def test_Change_WhenTheStoreCountsARowTheExportDoesNotList_NamesItAndItsSource(self, make):
        store = make(HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")])

        change = only_change(store)

        assert change.day == date(2026, 9, 6)
        assert change.holds == ("counted-not-listed", "one-row")
        (note,) = change.counted_not_listed.named
        assert (note.direction, note.sources, note.status) == ("out", ("starling",), "booked")
        assert ("starling", "2026-09-06") in note.dates
        assert change.one_row_negated is True
        assert change.listed_not_counted.count == 0

    def test_Change_WhenAListedRowIsNotCountedAndAnotherIsNotListed_SaysTheTwoTogetherEqualIt(
        self, make
    ):
        store = make(HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")])
        arrive_again(store, GARAGE, status=TransactionStatus.VOID)

        change = only_change(store)

        assert change.holds[0] == "combined"
        assert (change.listed_not_counted.count, change.counted_not_listed.count) == (1, 1)

    def test_Change_WhenARowTheExportListsIsNotHeldAtAll_SaysSoAndCountsItUnsighted(self, make):
        store = make(HEALTHY)
        store.connection.execute(
            "DELETE FROM transaction_sources WHERE entity_id IN "
            "(SELECT entity_id FROM transactions WHERE amount_minor = ?)",
            (GARAGE.minor,),
        )
        store.connection.execute("DELETE FROM transactions WHERE amount_minor = ?", (GARAGE.minor,))
        store.connection.commit()

        explanation = explained(store)

        (change,) = explanation.changes
        assert change.holds[0] == "listed-not-counted"
        (note,) = change.listed_not_counted.named
        assert note.why == "not held at all"
        assert note.dates == (("starling-csv", "2026-09-06"),)
        assert explanation.facts is not None
        assert explanation.facts.unsighted == 1

    def test_Change_WhenAMergedRowsFigureDiffersFromTheExportsOwn_NamesTheDifferentFigure(
        self, make
    ):
        store = make(HEALTHY)
        store.connection.execute(
            "UPDATE transactions SET amount_minor = ? WHERE amount_minor = ?",
            (GARAGE.minor - 250, GARAGE.minor),
        )
        store.connection.commit()

        change = only_change(store)

        assert change.holds[0] == "different-figure"
        (note,) = change.different_figure.named
        assert note.figure_differs is True
        assert ("starling-csv", "2026-09-06") in note.dates

    def test_Change_WhenTheExportOpensAtAFigureOtherThanNil_SaysSoAtItsFirstBalance(self, make):
        store = make(HEALTHY, export_opens_at=12300)

        explanation = explained(store)

        assert explanation.changes[0].holds == ("export-opening",)
        assert explanation.changes[0].day == date(2026, 9, 1)
        assert len(explanation.changes) == 1

    def test_Change_WhenALegGoesToASpaceWhoseRowsAreNotHeld_SaysItCoincides(self, make):
        store = make(HEALTHY, feed_only=[leg(MAIN, -4700, 6, "cat-closed", "f-gone")])

        change = only_change(store)

        assert "unheld-space" in change.holds
        assert change.counted_not_listed.count == 1
        assert change.counted_not_listed.named[0].transfer is True

    def test_Change_WhenTheStoreSightsTheExportsRowOnADifferentDay_CountsTheRowsEachSide(
        self, make
    ):
        """The export lists the 6th's row and the store holds its sighting on the 7th:
        the 6th lists one row where the store holds none, and the next balance holds
        the sighting where the export lists nothing. The two changes undo each other,
        so they are one explanation, given at the first, which says where it is undone."""
        store = make(HEALTHY)
        store.connection.execute(
            "UPDATE transaction_sources SET observed_date = '2026-09-07' "
            "WHERE source = 'starling-csv' AND entity_id IN "
            "(SELECT entity_id FROM transactions WHERE amount_minor = ?)",
            (GARAGE.minor,),
        )
        store.connection.commit()

        (first,) = explained(store).changes

        assert first.day == date(2026, 9, 6)
        assert first.undone_on == date(2026, 9, 8)
        assert (first.export_rows, first.store_sightings) == (1, 0)
        assert "row-counts" in first.holds
        assert "timing-pair" in first.holds
        assert first.sides.count == 1

    def test_Export_WhenItListsTwoIdenticalLines_TheStoreHoldsBothAsSeparateRows(self, tmp_path):
        """Identical lines do not collapse into one sighting: a fault from that would
        show as a difference, and this household has none."""
        store = Store(tmp_path / "twice.sqlite3")
        try:
            land_evidence(store)
            path = tmp_path / "export.csv"
            twin = replace(GARAGE)
            path.write_text(
                "\n".join(export_lines([SALARY, RENT, GARAGE, twin, *STEADY])) + "\n",
                encoding="utf-8",
            )
            import_file(store, path, account_id=MAIN, account_map=MAP)
            held = store.connection.execute(
                "SELECT COUNT(*) FROM transactions WHERE amount_minor = ?", (GARAGE.minor,)
            ).fetchone()[0]
        finally:
            store.close()

        assert held == 2

    def test_Facts_WhenTheExportsSequenceInterleaves_CountsTheOutOfOrderRowsAndUncutDays(
        self, make
    ):
        store = make([*INTERLEAVED, *TAIL], feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "G")])

        facts = explained(store).facts

        assert facts is not None
        assert (facts.exports, facts.out_of_order, facts.uncut_days, facts.unsighted) == (
            1, 1, 1, 0,
        )
        assert facts.rows == len(INTERLEAVED) + len(TAIL)


class TestTheExplanationsOnThePage:
    def test_Page_WhenARowIsSurplus_SaysWhichRowByDateSourceDirectionAndStatus(self, make):
        store = make(HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")])

        page = render(store)

        assert "The change at the end of" in page
        assert "the store counts in the window that starling-csv does not list" in page
        assert "out row dated" in page
        assert "seen by <code>starling</code>; booked" in page
        assert "the negative of a single counted row" in page

    def test_Page_WhenARowIsVoid_SaysItIsListedButNotCountedAndWhy(self, make):
        store = make(HEALTHY)
        arrive_again(store, GARAGE, status=TransactionStatus.VOID)

        page = render(store)

        assert "starling-csv lists in the window that the store does not count" in page
        assert "; void" in page

    def test_Page_NeverCarriesAFigureADescriptionOrAPayee(self, make):
        store = make(
            [*HEALTHY, Row("Distinctive Payee", -4321, 12, 12)],
            feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost Description")],
        )

        page = render(store)

        assert "The change at the end of" in page
        for secret in ("Ghost Description", "Distinctive Payee", "Garage", "Salary", "Rent"):
            assert secret not in page
        for figure in ("12.34", "1,234", "1234", "43.21", "4,321", "4321", "90.00", "9,000"):
            assert figure not in page

    def test_Page_WhenMoreThanTwentyPermanentChanges_ExplainsEveryOneAndSaysNoneWasLeftOut(
        self, tmp_path
    ):
        """Twenty-five unrelated surplus rows on days 3 to 27: the export states a balance
        on the 3rd, the 6th, and every day from the 8th, so the rows of the 4th and 5th
        fall into the 6th's window and the 7th's into the 8th's. KNOWN ANSWER: 22
        changes, all of one sign so none pairs, all permanent, all explained. The page
        once explained twenty and left the last two bare."""
        ghosts = [pay(MAIN, FEED, f"f-g{d}", -(7 + d), d, f"Ghost{d}") for d in range(3, 28)]
        store = build(tmp_path, [*HEALTHY], feed_only=ghosts)
        try:
            page = render(store)
            explanation = explained(store)
        finally:
            store.close()

        assert len(explanation.changes) == 22
        assert explanation.omitted == 0
        assert page.count("The change at the end of") == 22
        assert "22 explanations follow: 22 for permanent changes, and 0 for timing pairs" in page
        assert "no explanation here" not in page

    def test_Page_WhenTheBoundIsReached_KeepsPermanentChangesAndSaysHowManyWereLeftOut(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("obdi.fault_structure.EXPLAINED_CHANGES", 5)
        ghosts = [pay(MAIN, FEED, f"f-g{d}", -(7 + d), d, f"Ghost{d}") for d in range(3, 28)]
        store = build(tmp_path, [*HEALTHY], feed_only=ghosts)
        try:
            page = render(store)
            explanation = explained(store)
        finally:
            store.close()

        assert len(explanation.changes) == 5
        assert (explanation.omitted, explanation.bound) == (17, 5)
        assert page.count("The change at the end of") == 5
        assert (
            "17 changes have no explanation here: the page works out at most 5 explanations "
            "for one account."
        ) in page

    def test_Page_WhenTwoChangesUndoEachOther_GivesOneExplanationForThePair(self, tmp_path):
        """A surplus of 1234 on the 10th and an equal and opposite one on the 15th are
        one timing fault. A third, unrelated surplus on the 20th is permanent.
        KNOWN ANSWER: three changes, one pair; two explanations, the pair's at the 10th."""
        feed_only = [
            pay(MAIN, FEED, "f-out", -1234, 10, "Ghost out"),
            pay(MAIN, FEED, "f-back", 1234, 15, "Ghost back"),
            pay(MAIN, FEED, "f-lone", -77, 20, "Ghost lone"),
        ]
        store = build(tmp_path, [*HEALTHY], feed_only=feed_only)
        try:
            page = render(store)
            explanation = explained(store)
            walk_days = [change.day for change in stated(store)[1].changes]
        finally:
            store.close()

        assert walk_days == [date(2026, 9, 10), date(2026, 9, 15), date(2026, 9, 20)]
        first, lone = explanation.changes
        assert (first.day, first.undone_on) == (date(2026, 9, 10), date(2026, 9, 15))
        assert (lone.day, lone.undone_on) == (date(2026, 9, 20), None)
        assert page.count("The change at the end of") == 2
        assert "The change at the end of <span class=\"mono nowrap\">2026-09-15" not in page
        assert "2 explanations follow: 1 for permanent changes, and 1 for timing pairs" in page
        assert "undone by an opposite change at the end of" in page
        for figure in ("1234", "12.34", "1,234"):
            assert figure not in page

    def test_Pair_WhenTwoUnrelatedChangesAreEqualAndOpposite_IsStillExplainedByItsFirstChange(
        self, tmp_path
    ):
        feed_only = [
            pay(MAIN, FEED, "f-out", -1234, 10, "Ghost out"),
            pay(MAIN, FEED, "f-back", 1234, 15, "Ghost back"),
        ]
        store = build(tmp_path, [*HEALTHY], feed_only=feed_only)
        try:
            (first,) = explained(store).changes
        finally:
            store.close()

        assert "counted-not-listed" in first.holds
        assert first.counted_not_listed.count == 1

    def test_Page_WhenNothingDiffers_ExplainsNothing(self, make):
        assert "The change at the end of" not in render(make(HEALTHY))


def put_walk(readings, rows, placement=None):
    """A walk written by hand, for the tests that need a source other than the export."""
    from obdi.balance_anchors import FamilyReading, FamilyWalk
    from obdi.ingest.family_anchors import FamilyAnchor, OpeningEvidence

    nil = FamilyAnchor(date(2026, 9, 1), 0, "opened")
    walk = FamilyWalk(
        "main",
        (),
        tuple(
            FamilyReading(date(2026, 9, day), 0, (source,), False, 0, difference)
            for day, source, difference in readings
        ),
        evidence=OpeningEvidence(date(2026, 9, 2), nil, "", frozenset()),
    )
    return walk, {"main": rows}, placement or SightingPlacement()


class TestExplanationsThatNeedAnotherSourceOrADefect:
    """Written at the level of the walk, because no ingest path builds them: a
    second source that disagrees with the first, and a placement that was not applied."""

    STATEMENT_SOURCE = "starling-statement-pdf"

    def explain(self, tmp_path, readings, rows, placement=None):
        walk, members, placed = put_walk(readings, rows, placement)
        with Store(tmp_path / "walk.sqlite3") as store:
            return explain_walk(store, "main", walk, members, placed)

    def test_Change_WhenAnotherSourceDisagreesAndThisOnesDifferenceIsUnmoved_SaysSourcesDiffer(
        self, tmp_path
    ):
        readings = [
            (10, "starling-csv", 0),
            (11, self.STATEMENT_SOURCE, 500),
            (12, "starling-csv", 0),
        ]

        explanation = self.explain(tmp_path, readings, [])

        last = explanation.changes[-1]
        assert last.day == date(2026, 9, 12)
        assert last.holds == ("source-disagreement",)
        assert last.previous_source == self.STATEMENT_SOURCE

    def test_Change_WhenTheChangeEqualsRowsOnDifferentSidesByTheSourcesDating_SaysItIsADefect(
        self, tmp_path
    ):
        row = pay(MAIN, FEED, "f-straddler", 700, 13, "Straddler")
        placed = SightingPlacement({self.STATEMENT_SOURCE: {row.entity_id: date(2026, 9, 11)}})
        readings = [(10, self.STATEMENT_SOURCE, 0), (12, self.STATEMENT_SOURCE, 700)]

        explanation = self.explain(tmp_path, readings, [row], placed)

        (change,) = explanation.changes
        assert "straddling" in change.holds
        (note,) = change.straddling.named
        assert (self.STATEMENT_SOURCE, "2026-09-11") in note.dates
        assert ("ledger", "2026-09-13") in note.dates

    def test_Change_WhenNothingAccountsForIt_SaysSoWithTheRowsOnEachSide(self, tmp_path):
        rows = [
            pay(MAIN, FEED, "f-before", 100, 9, "B"),
            pay(MAIN, FEED, "f-inside-1", 200, 11, "I1"),
            pay(MAIN, FEED, "f-inside-2", -300, 12, "I2"),
            pay(MAIN, FEED, "f-after", 400, 14, "A"),
        ]
        readings = [(10, self.STATEMENT_SOURCE, 0), (12, self.STATEMENT_SOURCE, 333)]

        explanation = self.explain(tmp_path, readings, rows)

        (change,) = explanation.changes
        assert change.holds == ("none",)
        assert (change.rows_before, change.rows_inside, change.rows_after) == (1, 2, 1)


def lookalike_of(note) -> Lookalike:
    assert note.lookalike is not None
    return note.lookalike


def twin_ghosts() -> list[Transaction]:
    """Two counted rows of the Garage's figure on the 9th that the export does not list.

    Two, so the change on the 9th is twice the figure and does not undo the 6th's,
    and is explained in its own right instead of as half of a timing pair.
    """
    return [pay(MAIN, FEED, f"f-ghost-{n}", GARAGE.minor, 9, f"Ghost{n}") for n in (1, 2)]


class TestWhatTheOtherSideHoldsThatIsLikeAMissingRow:
    """A row one side lacks is either absent or present under another day or row.

    The deployed page said three payments the feed and the aggregator both hold were
    "not listed" by the export, and could not say whether the export listed a row of
    that size elsewhere, so every reading stayed open. Each scenario adds ONE row to
    the healthy household, in pence, with the answer decided before the first run
    and no figure on any sentence.

    KNOWN ANSWERS (the ghost is a counted row the export does not list):
        a ghost of 1234 on the 6th, and a payment of 1234 both sources hold on the 16th
            the export lists a row of that size 10 days away, sighted on that other
            stored row, which is seen by the feed and the export on the 16th
        a ghost of 1234 on the 6th and nothing else of that size
            the export lists no row of that size within thirty days
        the export's Garage of 9000 on the 6th held nowhere, and two ghosts of 9000 on the 9th
            on the 6th the store counts a row of that size 3 days away that the export
            does not list; on the 9th the export lists a row 3 days away that no
            stored row carries
        the Garage voided on the 6th and another payment of 9000 both sources hold on the 9th
            the store counts a row of that size 3 days away, which the export lists
            as another row
    """

    def test_Change_WhenTheExportListsARowOfThatSizeBeyondTheMatchersReach_NamesTheRowItIsSightedOn(
        self, make
    ):
        store = make(
            [*HEALTHY, Row("Twin", -1234, 16, 16)],
            feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")],
        )

        (note,) = only_change(store).counted_not_listed.named

        found = lookalike_of(note)
        assert (found.side, found.found, found.days_away, found.sighted_on) == (
            "export",
            True,
            10,
            "another row",
        )
        assert found.other is not None
        assert found.other.sources == ("starling", "starling-csv")
        assert found.other.account == "the main account"
        assert ("starling", "2026-09-16") in found.other.dates

    def test_Change_WhenNoRowOfThatSizeIsListedNearby_SaysTheExportListsNone(self, make):
        store = make(HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")])

        (note,) = only_change(store).counted_not_listed.named

        assert lookalike_of(note) == Lookalike("export", False)

    def test_Change_WhenAnExportRowAndAStoreRowLieThreeDaysApart_EachNamesTheOther(self, make):
        store = make(HEALTHY)
        store.connection.execute(
            "DELETE FROM transaction_sources WHERE entity_id IN "
            "(SELECT entity_id FROM transactions WHERE amount_minor = ?)",
            (GARAGE.minor,),
        )
        store.connection.execute("DELETE FROM transactions WHERE amount_minor = ?", (GARAGE.minor,))
        store.connection.commit()
        Household(store, MAP).arrive(*twin_ghosts())

        first, second = explained(store).changes

        (missing,) = first.listed_not_counted.named
        assert missing.why == "not held at all"
        mirror = lookalike_of(missing)
        assert (mirror.side, mirror.found, mirror.days_away, mirror.sighted_on) == (
            "store",
            True,
            3,
            "unlisted",
        )
        assert mirror.other is not None
        assert mirror.other.sources == ("starling",)
        assert second.counted_not_listed.count == 2
        for ghost in second.counted_not_listed.named:
            # No stored row carries the export's row, so there is no recipient to compare.
            assert lookalike_of(ghost) == Lookalike(
                "export", True, 3, "nothing", recipient="unknown"
            )

    def test_Change_WhenTheCountedRowOfThatSizeIsListedAsAnotherRow_SaysSo(self, make):
        store = make([*HEALTHY, Row("Garage Again", GARAGE.minor, 9, 9)])
        arrive_again(store, GARAGE, status=TransactionStatus.VOID)

        change = only_change(store)

        (voided,) = change.listed_not_counted.named
        mirror = lookalike_of(voided)
        assert (mirror.side, mirror.found, mirror.days_away, mirror.sighted_on) == (
            "store",
            True,
            3,
            "listed",
        )

    def test_Page_WhenARowIsMissing_SaysWhatTheOtherSideHoldsWithoutAFigure(self, make):
        store = make(
            [*HEALTHY, Row("Twin", -1234, 16, 16)],
            feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")],
        )

        page = render(store)

        assert (
            "the export lists a row of the same size and direction (whose recipient could "
            "not be compared), 10 days away, reported on another stored row (out row dated"
        ) in page
        assert "1234" not in page
        assert "12.34" not in page

    def test_Page_WhenNoRowOfThatSizeLiesNearby_SaysNoneIsListedWithinThirtyDays(self, make):
        store = make(HEALTHY, feed_only=[pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost")])

        page = render(store)

        assert "the export lists no row of the same size and direction within thirty days" in page

    def test_Page_WhenTheExportRowIsNotHeld_SaysNoStoredRowCarriesIt(self, make):
        store = make(HEALTHY)
        store.connection.execute(
            "DELETE FROM transaction_sources WHERE entity_id IN "
            "(SELECT entity_id FROM transactions WHERE amount_minor = ?)",
            (GARAGE.minor,),
        )
        store.connection.execute("DELETE FROM transactions WHERE amount_minor = ?", (GARAGE.minor,))
        store.connection.commit()
        Household(store, MAP).arrive(*twin_ghosts())

        page = render(store)

        assert "3 days away, that no stored row carries" in page
        assert (
            "the store counts a row of the same size and direction (whose recipient could not "
            "be compared), 3 days away, which the export does not list (out row dated"
        ) in page

    def test_Lookalike_WhenTheNearestRowIsThirtyOneDaysAway_IsNotOffered(self):
        day = date(2026, 9, 1)
        within = (date(2026, 10, 1), "thirty")
        beyond = (date(2026, 10, 2), "thirty-one")

        assert _nearest([beyond], day) is None
        assert _nearest([beyond, within], day) == within
        assert _nearest([(date(2026, 8, 30), "before"), (date(2026, 9, 3), "after")], day) == (
            date(2026, 8, 30),
            "before",
        )


class TestWhatATransferLegsPartnerIs:
    """A leg is "confirmed paired with the Space" and the page now says with what.

    The deployed page showed the main account's IN leg paired with the Space beside
    the Space's own leg "with no pair" and an ordinary payment of the same size, and
    nothing on the leg said its partner was the payment. KNOWN ANSWERS, with a
    ghost of 1234 on the 6th so the change names the rows:

        an IN leg of 4700 in the main account naming a Space no account holds, and the
        Space's payment of 4700 the same day
            the leg's partner is an out row, dated the 6th, an ordinary payment
        the same with the Space's own OUT leg, naming the main account, instead
            the leg's partner is an out row, dated the 6th, an internal leg
    """

    @staticmethod
    def household(make, space_row: Transaction, main_names: str) -> Store:
        store = make(
            HEALTHY,
            feed_only=[
                pay(MAIN, FEED, "f-ghost", -1234, 6, "Ghost"),
                leg(MAIN, 4700, 6, main_names, "f-in"),
                space_row,
            ],
        )
        pair_transfers_across_store(store, MAP)
        return store

    def test_Page_WhenALegIsPairedWithAnOrdinaryPayment_SaysItsPartnerIsAPayment(self, make):
        store = self.household(make, pay(BILLS, FEED, "s-pay", -4700, 6, "Pay"), "cat-unknown")

        page = render(store)

        assert (
            "confirmed paired with the Space starling-space-bills (out row dated "
            '<span class="mono nowrap">2026-09-06</span>, an ordinary payment)'
        ) in page

    def test_Page_WhenALegIsPairedWithTheSpacesOwnLeg_SaysItsPartnerIsALeg(self, make):
        space_leg = leg(BILLS, -4700, 6, "cat-main", "s-out")
        store = self.household(make, space_leg, "cat-bills")

        page = render(store)

        assert (
            "confirmed paired with the Space starling-space-bills (out row dated "
            '<span class="mono nowrap">2026-09-06</span>, an internal leg)'
        ) in page
