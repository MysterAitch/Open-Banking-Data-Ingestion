"""The Goals page, and the pages that carry a goals line, over the large invented store: a fixed
number of statements.

THE RULE THE BUDGET HOLDS: the Goals page reads the home page's held position (`home_position`)
and one select of the goals, so it costs the position and one statement more however many goals
there are; a second read, the position held, costs the memo's key and the goals' select. Position
and This month gain one select (the goals) over what they cost without any.

MEASURED: see the constants; the bounds are loose on time and tight on statements, as the
Recurring page's are (`test_recurring_speed`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from large_store_corpus import LargeStore, cached_large_store
from large_store_pages import copy_of, serving
from obdi.ingest.goal_records import BUILD, CLEAR, SAVE
from obdi.ingest.store import Store

#: Statements a first GET of /goals issues over the large store with no goal declared: the held
#: position is read and the (empty) goals. Measured 810 (1.4 s), nearly all of it the position's
#: per-account reads, which this budget does not try to reduce.
PLAIN_FIRST_STATEMENTS = 820
#: The same GET again with the position held: the memo's key and the goals' select. Measured 18.
PLAIN_HELD_STATEMENTS = 25
#: A first GET with a goal of each kind declared on the busiest accounts. Measured 810, the same
#: as with none: the goals are one select however many there are.
GOALS_FIRST_STATEMENTS = 820
#: The same GET again, everything held. Measured 18.
GOALS_HELD_STATEMENTS = 25
#: What a held GET of /position or /this-month gains once goals are declared. Measured 0: the
#: goals' select is made whether or not any is declared (804 and 16 statements both ways), and
#: one statement of allowance is kept for the key. A per-goal statement would exceed it.
HELD_EXTRA_STATEMENTS = 1
SECONDS = 60.0


@pytest.fixture(scope="module")
def large() -> LargeStore:
    return cached_large_store()


def declare_goals(path) -> None:
    today = datetime.now(UTC).date()
    with Store(path) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute(
                "SELECT account_id FROM transactions GROUP BY account_id "
                "ORDER BY COUNT(*) DESC LIMIT 3"
            )
        ]
        kinds = ((CLEAR, "Debt"), (BUILD, "Fund"), (SAVE, "Saving"))
        for ref, (kind, name) in zip(refs, kinds, strict=False):
            store.declare_goal(
                f"Invented {name}",
                kind=kind,
                account=ref,
                target_minor=0 if kind == CLEAR else 500_000,
                target_date=today + timedelta(days=200),
                declared_on=today - timedelta(days=100),
                start_minor=100_000 if kind != SAVE else 0,
            )


@pytest.fixture(scope="module")
def plain(large, tmp_path_factory):
    with serving(large, tmp_path_factory.mktemp("goals-plain")) as served:
        yield served


@pytest.fixture(scope="module")
def with_goals(large, tmp_path_factory):
    copied = copy_of(large, tmp_path_factory.mktemp("goals-store"))
    declare_goals(copied.path)
    with serving(copied, tmp_path_factory.mktemp("goals-declared")) as served:
        yield served


class TestGoalsOverTheLargeStore:
    def test_Goals_WhenNoneIsDeclared_IssuesAFixedFewStatementsAndIsQuick(self, plain):
        first = plain.get("/goals")
        later = plain.get("/goals")

        assert first.status == 200
        assert first.statements <= PLAIN_FIRST_STATEMENTS, first.statements
        assert later.statements <= PLAIN_HELD_STATEMENTS, later.statements
        assert first.seconds < SECONDS

    def test_Goals_WhenOneOfEachKindIsDeclared_CostsTheSameFewStatementsOnceHeld(
        self, with_goals
    ):
        first = with_goals.get("/goals")
        later = with_goals.get("/goals")

        assert first.status == 200
        assert first.statements <= GOALS_FIRST_STATEMENTS, first.statements
        assert later.statements <= GOALS_HELD_STATEMENTS, later.statements
        assert first.seconds < SECONDS

    def test_Goals_OverTheLargeStore_SealsEveryAmountForAGet(self, with_goals):
        body = with_goals.get("/goals").body

        assert "£•••" in body
        assert "5,000.00" not in body

    @pytest.mark.parametrize("route", ["/position", "/this-month"])
    def test_Page_WhenGoalsAreDeclared_GainsFewStatementsOnceHeld(
        self, large, tmp_path_factory, route
    ):
        # One server at a time: the statements are counted by wrapping the connection factory,
        # and two servers up together would leave the count to whichever wrapped last.
        copied = copy_of(large, tmp_path_factory.mktemp("goals-held"))
        with serving(copied, tmp_path_factory.mktemp("goals-held-pages")) as served:
            served.get(route)
            before = served.get(route)
            declare_goals(copied.path)
            served.get(route)
            after = served.get(route)

        assert after.status == 200
        extra = after.statements - before.statements
        assert extra <= HELD_EXTRA_STATEMENTS, (before.statements, after.statements)
