"""What the store keeps and refuses where commitments are concerned.

KNOWN ANSWERS, decided before the first run. A commitment is declared with its first window; a
price change closes that window the day before the new one opens and keeps both; ending closes the
open window on the day given; removing hides the commitment and keeps its rows; the declared tables
survive the rebuild from raw and a store made by the release before this one (schema 28) opens and
gains them. Every payee, account, and amount is invented.
"""

from __future__ import annotations

import pathlib
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

import pytest

from landing import rebuild_from_raw
from obdi.ingest.commitment_records import CommitmentRefused, WindowTerms
from obdi.ingest.store import SCHEMA_VERSION, Store

SNAPSHOT = pathlib.Path(__file__).resolve().parent.parent / "schema_history" / "24-commitments.sql"
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
TERMS = WindowTerms(
    amount_minor=4137,
    currency="GBP",
    cadence="monthly",
    usual_day=3,
    usual_month=0,
    tolerance_days=4,
    basis="confirmed from the series on 2026-10-08",
)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def declare(store: Store, name: str = "Zephyrine Streaming", **given: Any) -> int:
    arguments: dict[str, Any] = {
        "kind": "pulled",
        "account": "current-main",
        "direction": "out",
        "entity_id": None,
        "name_key": "zephyrine quokka",
        "from_day": date(2026, 3, 3),
        "to_day": None,
        "terms": TERMS,
        "now": NOW,
    }
    arguments.update(given)
    return store.declare_commitment(name, **arguments)


class TestADeclaredCommitment:
    def test_Commitment_WhenDeclared_ReadsBackWithItsFirstWindow(self, store):
        made = declare(store)

        (found,) = store.commitments()

        assert (found.id, found.name, found.kind, found.account) == (
            made,
            "Zephyrine Streaming",
            "pulled",
            "current-main",
        )
        (window,) = found.windows
        assert (window.from_day, window.to_day, window.amount_minor) == (
            date(2026, 3, 3),
            None,
            4137,
        )
        assert (window.cadence, window.usual_day, window.tolerance_days, window.basis) == (
            "monthly",
            3,
            4,
            "confirmed from the series on 2026-10-08",
        )
        assert found.current == window and found.ended is None

    def test_Commitment_WhenDeclaredClosedAtTheLastOccurrence_ReadsAsEnded(self, store):
        declare(store, to_day=date(2026, 7, 3))

        (found,) = store.commitments()

        assert found.ended == date(2026, 7, 3)

    def test_Commitment_WhenNoneIsDeclared_ListsNothing(self, store):
        assert store.commitments() == []

    def test_Commitment_WhenTheNameHasStraySpacing_IsKeptTidied(self, store):
        declare(store, "  Zephyrine   Streaming ")

        assert [c.name for c in store.commitments()] == ["Zephyrine Streaming"]


class TestACommitmentTheStoreRefuses:
    @pytest.mark.parametrize(
        "given",
        [
            {"kind": "optional"},
            {"direction": "sideways"},
            {"account": ""},
            {"name_key": ""},
            {"entity_id": 999, "name_key": ""},
            {"to_day": date(2026, 1, 1)},
        ],
        ids=["kind", "direction", "account", "no-payee", "missing-entity", "ends-before-begins"],
    )
    def test_Commitment_WhenMalformed_IsRefusedAndKeptNowhere(self, store, given):
        with pytest.raises(CommitmentRefused):
            declare(store, **given)

        assert store.commitments() == []

    def test_Commitment_WhenTheNameIsEmpty_IsRefused(self, store):
        with pytest.raises(CommitmentRefused):
            declare(store, "   ")

    @pytest.mark.parametrize("amount", [0, -5])
    def test_Commitment_WhenTheAmountIsNotPositive_IsRefusedAndLeavesNoRow(self, store, amount):
        terms = WindowTerms(amount, "GBP", "monthly", 3, 0, 4, "edited")

        with pytest.raises(CommitmentRefused):
            declare(store, terms=terms)

        assert store.commitments() == []
        assert store.connection.execute("SELECT COUNT(*) FROM commitments").fetchone()[0] == 0

    def test_Commitment_WhenTheSamePaymentIsDeclaredTwice_IsKeptOnce(self, store):
        declare(store)

        with pytest.raises(CommitmentRefused, match="already"):
            declare(store)

        assert len(store.commitments()) == 1

    def test_Commitments_WhenTwoPricesShareAPayee_AreBothKept(self, store):
        declare(store, "Plan", terms=TERMS)
        declare(store, "Storage", terms=WindowTerms(199, "GBP", "monthly", 3, 0, 4, "edited"))

        assert [c.name for c in store.commitments()] == ["Plan", "Storage"]

    def test_Commitment_WhenTheEntityExists_IsKeptAgainstIt(self, store):
        entity = store.create_entity("Zephyrine", ["zephyrine quokka"], now=NOW)

        declare(store, entity_id=entity)

        assert [c.entity_id for c in store.commitments()] == [entity]


class TestAChangedPrice:
    def test_Window_WhenThePriceChanges_ClosesTheOldTheDayBeforeAndOpensTheNew(self, store):
        made = declare(store)
        new = WindowTerms(4791, "GBP", "monthly", 3, 0, 4, "price changed on 2026-07-03")

        store.change_commitment_window(made, date(2026, 7, 3), new)

        (found,) = store.commitments()
        old, current = found.windows
        assert (old.from_day, old.to_day, old.amount_minor) == (
            date(2026, 3, 3),
            date(2026, 7, 2),
            4137,
        )
        assert (current.from_day, current.to_day, current.amount_minor) == (
            date(2026, 7, 3),
            None,
            4791,
        )
        assert found.current == current

    def test_Window_WhenTheNewStartIsNotAfterTheOldStart_IsRefusedAndNothingChanges(self, store):
        made = declare(store)
        new = WindowTerms(4791, "GBP", "monthly", 3, 0, 4, "edited")

        with pytest.raises(CommitmentRefused):
            store.change_commitment_window(made, date(2026, 3, 3), new)

        (found,) = store.commitments()
        assert len(found.windows) == 1 and found.windows[0].to_day is None

    def test_Window_WhenTheNewAmountIsNotPositive_LeavesTheOldWindowOpen(self, store):
        made = declare(store)

        with pytest.raises(CommitmentRefused):
            store.change_commitment_window(
                made, date(2026, 7, 3), WindowTerms(0, "GBP", "monthly", 3, 0, 4, "edited")
            )

        (found,) = store.commitments()
        assert found.windows[0].to_day is None

    def test_Window_WhenTheCommitmentHasEnded_IsRefused(self, store):
        made = declare(store, to_day=date(2026, 7, 3))

        with pytest.raises(CommitmentRefused, match="ended"):
            store.change_commitment_window(made, date(2026, 8, 3), TERMS)

    def test_Window_WhenTheCommitmentIsUnknown_IsRefused(self, store):
        with pytest.raises(CommitmentRefused):
            store.change_commitment_window(42, date(2026, 8, 3), TERMS)

    def test_Windows_WhenThePriceChangesTwice_KeepAllThreeInOrderWithOneOpen(self, store):
        made = declare(store)
        store.change_commitment_window(
            made, date(2026, 5, 3), WindowTerms(4500, "GBP", "monthly", 3, 0, 4, "edited")
        )
        store.change_commitment_window(
            made, date(2026, 8, 3), WindowTerms(4791, "GBP", "monthly", 3, 0, 4, "edited")
        )

        (found,) = store.commitments()

        assert [(w.amount_minor, w.to_day) for w in found.windows] == [
            (4137, date(2026, 5, 2)),
            (4500, date(2026, 8, 2)),
            (4791, None),
        ]


class TestAnEndedOrRemovedCommitment:
    def test_Commitment_WhenEnded_ClosesItsOpenWindowOnTheDayGiven(self, store):
        made = declare(store)

        store.end_commitment(made, date(2026, 7, 3))

        (found,) = store.commitments()
        assert found.ended == date(2026, 7, 3)

    def test_Commitment_WhenEndedBeforeItBegan_IsRefused(self, store):
        made = declare(store)

        with pytest.raises(CommitmentRefused):
            store.end_commitment(made, date(2026, 1, 1))

        (found,) = store.commitments()
        assert found.ended is None

    def test_Commitment_WhenEndedTwice_IsRefusedTheSecondTime(self, store):
        made = declare(store)
        store.end_commitment(made, date(2026, 7, 3))

        with pytest.raises(CommitmentRefused):
            store.end_commitment(made, date(2026, 8, 3))

    def test_Commitment_WhenRemoved_ListsNoMoreButKeepsItsRows(self, store):
        made = declare(store)

        store.remove_commitment(made, now=NOW)

        assert store.commitments() == []
        rows = store.connection.execute(
            "SELECT removed_at FROM commitments WHERE id = ?", (made,)
        ).fetchall()
        assert [r[0] for r in rows] == [NOW.isoformat()]
        windows = store.connection.execute("SELECT COUNT(*) FROM commitment_windows").fetchone()
        assert windows[0] == 1

    def test_Commitment_WhenRemovedTwice_IsRefusedTheSecondTime(self, store):
        made = declare(store)
        store.remove_commitment(made)

        with pytest.raises(CommitmentRefused):
            store.remove_commitment(made)

    def test_Commitment_WhenRemoved_CanBeDeclaredAgain(self, store):
        store.remove_commitment(declare(store))

        declare(store)

        assert len(store.commitments()) == 1


class TestAnEditedCommitment:
    def test_Commitment_WhenRenamedAndRekinded_ReadsBackEdited(self, store):
        made = declare(store)

        store.edit_commitment(made, name="Streaming plan", kind="scheduled")

        (found,) = store.commitments()
        assert (found.name, found.kind) == ("Streaming plan", "scheduled")

    def test_Commitment_WhenGivenAKindOutsideTheThree_ChangesNothingAtAll(self, store):
        made = declare(store)

        with pytest.raises(CommitmentRefused):
            store.edit_commitment(made, name="Renamed", kind="optional")

        (found,) = store.commitments()
        assert (found.name, found.kind) == ("Zephyrine Streaming", "pulled")


def dismiss(store: Store, **given: Any) -> int:
    arguments: dict[str, Any] = {
        "entity_id": None,
        "name_key": "zephyrine quokka",
        "account": "current-main",
        "direction": "out",
        "cadence": "monthly",
        "now": NOW,
    }
    arguments.update(given)
    return store.dismiss_series(**arguments)


class TestASeriesSaidNotToBeACommitment:
    def test_Dismissal_WhenKept_ReadsBack(self, store):
        made = dismiss(store)

        (found,) = store.dismissals()

        assert (found.id, found.name_key, found.account, found.direction, found.cadence) == (
            made,
            "zephyrine quokka",
            "current-main",
            "out",
            "monthly",
        )
        assert found.entity_id is None and found.dismissed_at == NOW.isoformat()

    @pytest.mark.parametrize(
        "given",
        [
            {"name_key": ""},
            {"account": ""},
            {"cadence": ""},
            {"direction": "sideways"},
            {"entity_id": 999},
        ],
        ids=["no-payee", "no-account", "no-cadence", "direction", "missing-entity"],
    )
    def test_Dismissal_WhenMalformed_IsRefusedAndKeptNowhere(self, store, given):
        with pytest.raises(CommitmentRefused):
            dismiss(store, **given)

        assert store.dismissals() == []

    def test_Dismissal_WhenKeptTwice_IsRefusedTheSecondTime(self, store):
        dismiss(store)

        with pytest.raises(CommitmentRefused, match="already"):
            dismiss(store)

        assert len(store.dismissals()) == 1

    def test_Dismissals_ForTwoCadencesOfOnePayee_AreBothKept(self, store):
        dismiss(store)
        dismiss(store, cadence="yearly")

        assert [d.cadence for d in store.dismissals()] == ["monthly", "yearly"]

    def test_Dismissal_WhenPutBack_ListsNoMoreButKeepsItsRow(self, store):
        made = dismiss(store)

        store.restore_dismissal(made, now=NOW)

        assert store.dismissals() == []
        rows = store.connection.execute("SELECT removed_at FROM series_dismissals").fetchall()
        assert [r[0] for r in rows] == [NOW.isoformat()]

    def test_Dismissal_WhenPutBackTwiceOrUnknown_IsRefused(self, store):
        made = dismiss(store)
        store.restore_dismissal(made)

        with pytest.raises(CommitmentRefused):
            store.restore_dismissal(made)
        with pytest.raises(CommitmentRefused):
            store.restore_dismissal(404)

    def test_Dismissal_WhenPutBack_CanBeKeptAgain(self, store):
        store.restore_dismissal(dismiss(store))

        dismiss(store)

        assert len(store.dismissals()) == 1

    def test_Dismissal_ForAnEntity_IsKeptAgainstIt(self, store):
        entity = store.create_entity("Zephyrine", ["zephyrine quokka"], now=NOW)

        dismiss(store, entity_id=entity, name_key="")

        assert [d.entity_id for d in store.dismissals()] == [entity]

    def test_Dismissals_WhenTheStoreIsRebuiltFromRaw_AreUntouched(self, store):
        dismiss(store)

        rebuild_from_raw(store)

        assert len(store.dismissals()) == 1

    def test_Irreplaceable_WhenASeriesIsSetAside_CountsIt(self, store):
        key = "recurring payments said not to be commitments"
        before = store.irreplaceable()[key]
        dismiss(store)

        assert store.irreplaceable()[key] == before + 1


class TestDeclaredCommitmentsAcrossTheRebuild:
    def test_Commitments_WhenTheStoreIsRebuiltFromRaw_AreUntouched(self, store):
        made = declare(store)
        store.change_commitment_window(
            made, date(2026, 7, 3), WindowTerms(4791, "GBP", "monthly", 3, 0, 4, "edited")
        )

        rebuild_from_raw(store)

        (found,) = store.commitments()
        assert [w.amount_minor for w in found.windows] == [4137, 4791]

    def test_Irreplaceable_WhenACommitmentIsKept_CountsIt(self, store):
        before = store.irreplaceable()["recurring payments confirmed as commitments"]
        declare(store)

        assert store.irreplaceable()["recurring payments confirmed as commitments"] == before + 1


class TestAStoreFromTheReleaseBeforeThisOne:
    def test_Store_WhenStampedSchema28_OpensGainsTheTablesAndKeepsItsAccount(self, tmp_path):
        path = tmp_path / "old.sqlite3"
        legacy = sqlite3.connect(path)
        legacy.executescript(SNAPSHOT.read_text(encoding="utf-8"))
        legacy.commit()
        legacy.close()

        with Store(path) as opened:
            assert opened.commitments() == []
            declare(opened)
            assert [r.ref for r in opened.declared_accounts()] == ["current-main"]
            stamped = opened.connection.execute(
                "SELECT value FROM obdi_meta WHERE key = 'schema_version'"
            ).fetchone()[0]

        assert stamped == str(SCHEMA_VERSION) == "30"
