"""What the store keeps and refuses where goals are concerned.

KNOWN ANSWERS, decided before the first run. A goal is declared against an account that is not
declared as held elsewhere (an account that only ever held rows has no declaration, so the store
cannot require one); a debt's target is nil whatever amount it is handed; a fund or a saving
needs a target above nothing and a saving needs a date; every refusal writes nothing; editing
keeps the day and the balance a goal started from; removing hides the goal and keeps its row; the
declared table survives the rebuild from raw; and a store made by the release before this one
(schema 29) opens and gains it. Every account, goal, and amount is invented.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

import pytest

from landing import rebuild_from_raw
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.goal_records import BUILD, CLEAR, SAVE, GoalRefused
from obdi.ingest.store import SCHEMA_VERSION, Store

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "schema_history" / "25-goals.sql"
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
DECLARED = date(2026, 7, 15)
CARD = "card-visa"
SAVINGS = "savings-main"


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        opened.declare_account(
            AccountRecord(ref=AccountRef(CARD), kind="credit card", label="Visa")
        )
        opened.declare_account(
            AccountRecord(ref=AccountRef(SAVINGS), kind="savings", label="Savings")
        )
        yield opened


def clear(store: Store, name: str = "Clear the Visa", **given: Any) -> int:
    arguments: dict[str, Any] = {
        "kind": CLEAR,
        "account": CARD,
        "target_minor": 0,
        "target_date": date(2027, 1, 15),
        "declared_on": DECLARED,
        "start_minor": 50000,
        "now": NOW,
    }
    arguments.update(given)
    return store.declare_goal(name, **arguments)


def save(store: Store, name: str = "Holiday", **given: Any) -> int:
    arguments: dict[str, Any] = {
        "kind": SAVE,
        "account": SAVINGS,
        "target_minor": 120000,
        "target_date": date(2027, 7, 15),
        "declared_on": DECLARED,
        "start_minor": None,
        "now": NOW,
    }
    arguments.update(given)
    return store.declare_goal(name, **arguments)


def rows_of(store: Store) -> int:
    return int(store.connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0])


class TestADeclaredGoal:
    def test_Goal_WhenADebtIsDeclared_ReadsBackWithItsStartAndANilTarget(self, store):
        made = clear(store, target_minor=99999)

        (found,) = store.goals()

        assert (found.id, found.name, found.kind, found.account) == (
            made,
            "Clear the Visa",
            CLEAR,
            CARD,
        )
        assert (found.target_minor, found.start_minor) == (0, 50000)
        assert (found.target_date, found.declared_on) == (date(2027, 1, 15), DECLARED)
        assert found.created_at == NOW.isoformat()

    def test_Goal_WhenAFundHasNoDate_ReadsBackWithNone(self, store):
        store.declare_goal(
            "Rainy day",
            kind=BUILD,
            account=SAVINGS,
            target_minor=300000,
            target_date=None,
            declared_on=DECLARED,
            start_minor=40000,
        )

        (found,) = store.goals()

        assert (found.kind, found.target_minor, found.target_date) == (BUILD, 300000, None)

    def test_Goal_WhenAFundStartedFromAnUnknownBalance_KeepsNoStart(self, store):
        save(store)

        (found,) = store.goals()

        assert found.start_minor is None

    def test_Goals_WhenSeveralAreDeclared_ListOldestFirst(self, store):
        first = save(store, "Holiday")
        second = clear(store)

        assert [g.id for g in store.goals()] == [first, second]

    def test_Name_WhenSpacedOddly_IsTidied(self, store):
        save(store, "  Summer   holiday ")

        assert store.goals()[0].name == "Summer holiday"


class TestAGoalThatIsRefused:
    @pytest.mark.parametrize(
        ("make", "given", "said"),
        [
            (save, {"name": "   "}, "needs a name"),
            (save, {"kind": "hoard"}, "a debt to clear, a fund to build, or a saving"),
            (save, {"account": ""}, "about an account"),
            (save, {"target_date": DECLARED}, "after the day it is declared"),
            (save, {"target_date": date(2026, 1, 1)}, "after the day it is declared"),
            (save, {"target_minor": 0}, "more than nothing"),
            (save, {"target_minor": -5}, "more than nothing"),
            (save, {"target_date": None}, "needs the date"),
            (save, {"start_minor": -1}, "negative"),
            (clear, {"start_minor": None}, "not known"),
            (clear, {"start_minor": 0}, "Nothing is owed"),
        ],
        ids=[
            "no-name",
            "unknown-kind",
            "no-account",
            "date-is-the-day-declared",
            "date-before-declared",
            "nil-target",
            "negative-target",
            "saving-without-date",
            "negative-start",
            "debt-of-unknown-balance",
            "debt-of-nothing",
        ],
    )
    def test_Declaring_WhenMalformed_IsRefusedAndKeepsNothing(self, store, make, given, said):
        if "name" in given:
            name = given.pop("name")
            with pytest.raises(GoalRefused, match=said):
                make(store, name, **given)
        else:
            with pytest.raises(GoalRefused, match=said):
                make(store, **given)

        assert rows_of(store) == 0

    def test_Declaring_WhenTheAccountIsExternal_IsRefused(self, store):
        store.declare_account(
            AccountRecord(
                ref=AccountRef("elsewhere"), kind="savings", label="Elsewhere", external=True
            )
        )

        with pytest.raises(GoalRefused, match="held elsewhere"):
            save(store, account="elsewhere")

        assert rows_of(store) == 0

    def test_Declaring_WhenTheAccountOnlyEverHeldRows_IsKept(self, store):
        # An account that has rows and no declaration is real: Position lists it.
        save(store, account="undeclared-account")

        assert store.goals()[0].account == "undeclared-account"

    def test_Declaring_WhenTheAccountAlreadyHasThatName_IsRefusedWhateverTheCase(self, store):
        save(store, "Holiday")

        with pytest.raises(GoalRefused, match="already has a goal of that name"):
            save(store, "HOLIDAY")

        assert rows_of(store) == 1

    def test_Declaring_WhenAnotherAccountHasThatName_IsKept(self, store):
        save(store, "Holiday")
        save(store, "Holiday", account=CARD)

        assert rows_of(store) == 2


class TestAnEditedGoal:
    def test_Goal_WhenRenamedAndRetargeted_KeepsTheDayAndStartItBeganFrom(self, store):
        made = save(store)

        store.edit_goal(
            made, name="Summer holiday", target_minor=150000, target_date=date(2027, 8, 1)
        )

        (found,) = store.goals()
        assert (found.name, found.target_minor, found.target_date) == (
            "Summer holiday",
            150000,
            date(2027, 8, 1),
        )
        assert (found.declared_on, found.start_minor) == (DECLARED, None)

    def test_Goal_WhenItsDateIsRemovedFromAFund_HasNone(self, store):
        made = store.declare_goal(
            "Rainy day",
            kind=BUILD,
            account=SAVINGS,
            target_minor=300000,
            target_date=date(2027, 1, 1),
            declared_on=DECLARED,
            start_minor=0,
        )

        store.edit_goal(made, no_date=True)

        assert store.goals()[0].target_date is None

    def test_Edit_WhenNothingIsGiven_ChangesNothing(self, store):
        made = save(store)

        store.edit_goal(made)

        assert store.goals()[0].name == "Holiday"

    @pytest.mark.parametrize(
        ("given", "said"),
        [
            ({"name": " "}, "needs a name"),
            ({"target_minor": 0}, "more than nothing"),
            ({"target_date": DECLARED}, "after the day it was declared"),
            ({"no_date": True}, "needs the date"),
            ({"target_date": date(2027, 9, 1), "no_date": True}, "both set and removed"),
        ],
        ids=["no-name", "nil-target", "date-not-after-declared", "saving-loses-date", "both"],
    )
    def test_Edit_WhenMalformed_IsRefusedAndChangesNothingAtAll(self, store, given, said):
        made = save(store)
        before = store.goals()

        with pytest.raises(GoalRefused, match=said):
            # A valid change rides along with the bad one: the whole edit must be refused.
            store.edit_goal(made, **{"target_minor": 99999, **given})

        assert store.goals() == before

    def test_Edit_WhenADebtIsGivenAnAmount_IsRefused(self, store):
        made = clear(store)

        with pytest.raises(GoalRefused, match="has no amount"):
            store.edit_goal(made, target_minor=100)

    def test_Edit_WhenRenamedToAnotherGoalsName_IsRefusedButKeepingItsOwnIsFine(self, store):
        first = save(store, "Holiday")
        save(store, "Boiler")

        with pytest.raises(GoalRefused, match="already has a goal of that name"):
            store.edit_goal(first, name="boiler")
        store.edit_goal(first, name="HOLIDAY")

        assert [g.name for g in store.goals()] == ["HOLIDAY", "Boiler"]

    def test_Edit_WhenTheGoalIsMissingOrRemoved_IsRefused(self, store):
        made = save(store)
        store.remove_goal(made)

        with pytest.raises(GoalRefused, match="no such goal"):
            store.edit_goal(made, name="Late")
        with pytest.raises(GoalRefused, match="no such goal"):
            store.edit_goal(9999, name="Never")


class TestARemovedGoal:
    def test_Goal_WhenRemoved_ListsNoMoreButKeepsItsRow(self, store):
        made = save(store)

        store.remove_goal(made, now=NOW)

        assert store.goals() == []
        stamp = store.connection.execute("SELECT removed_at FROM goals").fetchone()[0]
        assert stamp == NOW.isoformat()

    def test_Goal_WhenRemovedTwiceOrUnknown_IsRefused(self, store):
        made = save(store)
        store.remove_goal(made)

        with pytest.raises(GoalRefused, match="no such goal"):
            store.remove_goal(made)
        with pytest.raises(GoalRefused, match="no such goal"):
            store.remove_goal(4242)

    def test_Goal_WhenRemoved_CanBeDeclaredAgainUnderTheSameName(self, store):
        made = save(store)
        store.remove_goal(made)

        again = save(store)

        assert again != made
        assert [g.id for g in store.goals()] == [again]


class TestGoalsAcrossTheRebuild:
    def test_Goals_WhenTheStoreIsRebuiltFromRaw_AreUntouched(self, store):
        save(store)
        clear(store)

        rebuild_from_raw(store)

        assert [g.name for g in store.goals()] == ["Holiday", "Clear the Visa"]

    def test_Irreplaceable_WhenAGoalIsKept_CountsItAndNotARemovedOne(self, store):
        key = "goals to clear, build, or save for"
        before = store.irreplaceable()[key]
        keep = save(store)
        gone = clear(store)
        store.remove_goal(gone)

        assert store.irreplaceable()[key] == before + 1
        assert keep in [g.id for g in store.goals()]


class TestAStoreFromTheReleaseBeforeThisOne:
    def test_Store_WhenStampedSchema29_OpensGainsTheTableAndKeepsItsCommitment(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as opened:
            assert opened.goals() == []
            save(opened, account="current-main")
            assert [c.name for c in opened.commitments()] == ["Zephyrine Streaming"]
            stamped = opened.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert stamped == str(SCHEMA_VERSION) == "31"
