"""A statement's own rows belong to its period, whatever date another source gave them.

A card statement lists a purchase by its transaction date and the feed by its
posting date, and when the two are merged into one row the stored date is the
feed's. A purchase made on a statement's last day and posted the next day then
falls, by its stored date, in the NEXT statement's period: both periods differ
by the purchase, in opposite directions, although every row is held exactly
once. Which side of a statement's closing a row falls on is therefore decided by
which statement LISTS it, where one does.

Every expectation was worked out from the construction below before the first
run. The card's statements (11 February, 11 March, 11 April 2026) chain from an
opening of 100.00 owed; spending raises what is owed. Spending rows, as printed:

    S1  closes 11 Feb   20 Jan  Alpha Grocer   12.37     11 Feb  Bravo Fuel   20.19
    S2  closes 11 Mar   12 Feb  Charlie Cafe    5.43     11 Mar  Delta Books  15.61
    S3  closes 11 Apr   12 Mar  Echo Rail      40.73     11 Apr  Foxtrot Gym   8.29

so the movements are S1 -32.56, S2 -21.04, S3 -49.02 in the store's sign.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from obdi.core.models import SourceTier, Transaction
from obdi.ingest.pipeline import import_file
from obdi.ingest.statement_membership import statement_membership
from obdi.ingest.statement_terms import statement_balances
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import STATED, STATEMENT, Anchor, derive_opening, effective_opening
from obdi.verify.period_reconciliation import PeriodKind, period_reconciliation
from test_period_reconciliation import (
    ACCOUNT,
    P2,
    P3,
    FeedRow,
    Row,
    World,
    _statement,
    build_world,
    periods_of,
)

#: The first statement's period starts at its own first row.
E1 = "2026-01-20 to 2026-02-11"

#: Each statement's rows sit ON the statement date or the day after the last, the
#: shape that crosses a boundary when a feed re-dates them.
EDGE_STATEMENTS: list[tuple[str, list[Row]]] = [
    ("11th Feb 2026", [("20th Jan", "Alpha Grocer", 1237), ("11th Feb", "Bravo Fuel", 2019)]),
    ("11th Mar 2026", [("12th Feb", "Charlie Cafe", 543), ("11th Mar", "Delta Books", 1561)]),
    ("11th Apr 2026", [("12th Mar", "Echo Rail", 4073), ("11th Apr", "Foxtrot Gym", 829)]),
]

#: The feed's posting dates for the same purchases, each on the OTHER side of the
#: statement date from the statement's own date for it.
EDGE_FEED: list[FeedRow] = [
    (date(2026, 1, 20), -1237, "Alpha Grocer"),
    (date(2026, 2, 12), -2019, "Bravo Fuel"),
    (date(2026, 2, 11), -543, "Charlie Cafe"),
    (date(2026, 3, 12), -1561, "Delta Books"),
    (date(2026, 3, 11), -4073, "Echo Rail"),
    (date(2026, 4, 12), -829, "Foxtrot Gym"),
]


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "membership.sqlite3") as opened:
        yield opened


class TestEveryRowMergedOntoAFeedRowDatedOnTheOtherSide:
    """Six rows, all merged, three of them (Bravo, Delta, Foxtrot) posted the day
    after the statement that lists them and two (Charlie, Echo) the day before
    the statement that lists them. Held once each.

    Expected: by stored date P1 would lack Bravo (+20.19) and P2 would hold it
    (a surplus of -20.19 each way) and so on; by membership every period agrees
    and so does every anchor."""

    def _built(self, store: Store, tmp_path: Path) -> None:
        build_world(store, tmp_path, World(statements=EDGE_STATEMENTS, feed=EDGE_FEED))

    def test_Periods_WhenEveryRowIsMergedButRedatedByTheFeed_AllAgree(self, store, tmp_path):
        self._built(store, tmp_path)

        report = period_reconciliation(store, sibling_accounts={})

        periods = periods_of(report)
        assert list(periods) == [E1, P2, P3]
        assert [p.agrees for p in periods.values()] == [True, True, True]
        assert [p.held_minor for p in periods.values()] == [-3256, -2104, -4902]

    def test_Anchors_WhenEveryRowIsMergedButRedatedByTheFeed_AllAgree(self, store, tmp_path):
        self._built(store, tmp_path)

        opening = effective_opening(store, ACCOUNT)

        assert len(opening.readings) == 3
        assert opening.differing == []

    def test_Rows_AreHeldOnceEachSoTheFaultWasTheirDatesAlone(self, store, tmp_path):
        self._built(store, tmp_path)

        rows = store.transactions_for_account(ACCOUNT)

        assert len(rows) == 6
        assert {r.value_date for r in rows} >= {date(2026, 2, 12), date(2026, 4, 12)}


class TestARowNoStatementListsIsPlacedByItsDate:
    def test_Period_WhenOnlyTheFeedHoldsARow_TheRowFallsInThePeriodItsDateNames(
        self, store, tmp_path
    ):
        """A 3.33 charge only the feed holds, dated 20 February: no statement
        lists it, so its date places it in P2. Expected: P2 surplus -3.33, P1 and
        P3 agree, and the anchors for 11 March and 11 April both differ by the
        same 3.33 (it is missing from the statements' arithmetic from P2 on)."""
        world = World(
            statements=EDGE_STATEMENTS,
            feed=[*EDGE_FEED, (date(2026, 2, 20), -333, "Surprise Charge")],
        )
        build_world(store, tmp_path, world)

        periods = periods_of(period_reconciliation(store, sibling_accounts={}))

        assert periods[E1].agrees and periods[P3].agrees
        assert periods[P2].surplus_minor == -333
        differing = effective_opening(store, ACCOUNT).differing
        assert [r.difference_minor for r in differing] == [333, 333]


class TestARowTwoOverlappingStatementsBothListCountsOnce:
    """S1 (closing 11 Feb, 13,256 owed) lists 20 Jan 12.37 and 11 Feb 20.19. A
    longer statement closing 11 Mar opens at the balance BEFORE the 11 Feb row
    (11,237) and lists it again with 14 Feb 5.43 and 28 Feb 15.61, closing at
    15,360. The 11 Feb row is one row with two statement sightings, and the feed
    then posts it on 12 February, after S1's closing, and its date wins.

    Expected: it is placed at S1's closing (the earliest statement listing it),
    so P1 holds it and P2 does not; P2 is the 21.04 the two closings differ by."""

    def _built(self, store: Store, tmp_path: Path) -> None:
        for name, day, opening, rows in (
            (
                "one",
                "11th Feb 2026",
                10000,
                [("20th Jan", "Alpha Grocer", 1237), ("11th Feb", "Bravo Fuel", 2019)],
            ),
            (
                "long",
                "11th Mar 2026",
                11237,
                [
                    ("11th Feb", "Bravo Fuel", 2019),
                    ("14th Feb", "Charlie Cafe", 543),
                    ("28th Feb", "Delta Books", 1561),
                ],
            ),
        ):
            payload, _ = _statement(day, opening, rows)
            path = tmp_path / f"{name}.pdf"
            path.write_bytes(payload)
            import_file(store, path, account_id=ACCOUNT)
        # The posting date moves the shared row after the earlier statement's closing.
        posted: list[FeedRow] = [(date(2026, 2, 12), -2019, "Bravo Fuel")]
        build_world(store, tmp_path, World(statements=[], feed=posted))
        [bravo] = [t for t in store.transactions_for_account(ACCOUNT) if t.amount_minor == -2019]
        assert bravo.value_date == date(2026, 2, 12)

    def test_Row_IsPlacedAtTheEarliestClosingThatListsIt(self, store, tmp_path):
        self._built(store, tmp_path)
        balances, _ = statement_balances(store, ACCOUNT)

        membership = statement_membership(store, ACCOUNT, balances)

        [bravo] = [t for t in store.transactions_for_account(ACCOUNT) if t.amount_minor == -2019]
        assert membership.placed[bravo.entity_id] == date(2026, 2, 11)
        assert len(membership.statements) == 2

    def test_Periods_BothAgreeAndTheSharedRowCountsOnlyInTheFirst(self, store, tmp_path):
        self._built(store, tmp_path)

        [item] = period_reconciliation(store, sibling_accounts={}).accounts
        chain = [p for p in item.periods if p.kind is not PeriodKind.INSIDE]

        assert [p.agrees for p in chain] == [True, True]
        assert [p.held_minor for p in chain] == [-3256, -2104]

    def test_Anchors_BothAgree(self, store, tmp_path):
        self._built(store, tmp_path)

        assert effective_opening(store, ACCOUNT).differing == []


class TestAnAccountWithOnlyStatedAnchorsIsUnchanged:
    def _row(self, minute: int, day: date, entity: str) -> Transaction:
        return Transaction(
            account_id="solo",
            amount_minor=minute,
            currency="GBP",
            value_date=day,
            booking_date=day,
            description=entity,
            source="csv",
            source_id=entity,
            tier=SourceTier.AUTHORITATIVE,
            content_key=entity,
            entity_id=entity,
        )

    def test_DeriveOpening_WhenAnchorsAreStated_IgnoresWhichStatementListsARow(self):
        """Row `a` is dated 5 March and `b` 20 March; a map claims `b` belongs to a
        statement closing 10 March. A STATED anchor of 10 March is about the
        account as the person sees it on that date, so only dates place rows:
        expected balance at 31 March = opening + both rows either way."""
        rows = [self._row(-100, date(2026, 3, 5), "a"), self._row(-200, date(2026, 3, 20), "b")]
        anchors = [Anchor(date(2026, 3, 10), 900, STATED), Anchor(date(2026, 3, 31), 700, STATED)]

        plain = derive_opening("solo", anchors, rows)
        placed = derive_opening("solo", anchors, rows, placed={"b": date(2026, 3, 10)})

        assert plain == placed
        assert plain.opening_minor == 1000
        assert plain.differing == []

    def test_DeriveOpening_WhenAnchorsAreStatements_PlacementDecidesWhichSideARowFallsOn(self):
        """The same rows, with statement anchors: the map moves `b` to the 10 March
        statement, so the 10 March balance includes it (opening 1,200 - 300) and
        the 31 March check agrees; by date alone it would not."""
        rows = [self._row(-100, date(2026, 3, 5), "a"), self._row(-200, date(2026, 3, 20), "b")]
        anchors = [
            Anchor(date(2026, 3, 10), 900, STATEMENT),
            Anchor(date(2026, 3, 31), 900, STATEMENT),
        ]

        by_date = derive_opening("solo", anchors, rows)
        by_membership = derive_opening("solo", anchors, rows, placed={"b": date(2026, 3, 10)})

        assert [r.agrees for r in by_date.readings] == [None, False]
        assert [r.agrees for r in by_membership.readings] == [None, True]
        assert by_membership.opening_minor == 1200

    def test_DeriveOpening_WhenARowIsNotPlaced_FallsBackToItsDate(self):
        rows = [self._row(-100, date(2026, 3, 5), "a")]
        anchors = [Anchor(date(2026, 3, 10), 900, STATEMENT)]

        placed = derive_opening("solo", anchors, rows, placed={"other": date(2026, 1, 1)})

        assert placed.opening_minor == 1000
