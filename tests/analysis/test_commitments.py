"""A series the owner confirmed is found again on later reads: matched to its commitment.

KNOWN ANSWERS, decided before the first run. Today is 2026-10-07. Every payee and amount is
invented.

  - A monthly £10.00 on the 15th, April to September: one series. Confirming it keeps one
    commitment whose one window is from 2026-04-15, open, at 1000, monthly, the 15th, four days'
    tolerance. A read a fortnight on, after another occurrence on 2026-10-15, finds the series
    again (seven occurrences) and it belongs to the same commitment.
  - Two products to one payee on the 3rd, £7.99 and £1.99: two series, two commitments, and each
    series belongs to the commitment of its own amount whichever order they are read in.
  - The same payee in another account is not that commitment's series; neither is the same
    payee on another cadence, nor the same name coming in instead of going out.
  - A name confirmed on its own and gathered into an entity afterwards still finds its series;
    a commitment confirmed under an entity still finds it when none of the old prints is left.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from obdi.analysis.commitments import (
    CONFIRM,
    ENDED,
    MISSING,
    apply_press,
    confirm,
    match_series,
    series_index,
    series_refs,
)
from obdi.analysis.entities import shape_of
from obdi.analysis.recurring import HABIT, PULLED, SCHEDULED, Series, find_recurring
from obdi.core.models import SourceTier, Transaction
from obdi.ingest.commitment_records import COMMITMENT_KINDS, CommitmentRefused
from obdi.ingest.entity_records import DESCRIPTION
from obdi.ingest.store import Store

TODAY = date(2026, 10, 7)
A, B = "acct-a", "acct-b"

_counter = [0]


def tx(account: str, day: date, minor: int, description: str) -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source="synthetic",
        tier=SourceTier.SYNTHETIC,
        entity_id=f"c{_counter[0]:06d}",
    )


def monthly(account: str, months: range, day: int, minor: int, text: str) -> list[Transaction]:
    return [tx(account, date(2026, m, day), minor, text) for m in months]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "store.sqlite3") as opened:
        yield opened


def only(found: list[Series]) -> Series:
    (series,) = found
    return series


def confirmed(store: Store, found: list[Series], series: Series, how: str = CONFIRM) -> str:
    return confirm(store, found, series_refs(found)[found.index(series)], how, today=TODAY)


class TestConfirmingASeries:
    def rows(self) -> list[Transaction]:
        return monthly(A, range(4, 10), 15, -1000, "MARMALADE FOUNDRY STUDIO")

    def test_Confirm_WhenAMonthlySeries_KeepsOneCommitmentWithTheWindowFromItsHistory(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        confirmed(store, found, only(found))

        (commitment,) = store.commitments()
        assert (commitment.account, commitment.direction, commitment.kind) == (A, "out", PULLED)
        (window,) = commitment.windows
        assert (window.from_day, window.to_day) == (date(2026, 4, 15), None)
        assert (window.amount_minor, window.currency, window.cadence) == (1000, "GBP", "monthly")
        assert (window.usual_day, window.usual_month, window.tolerance_days) == (15, 0, 4)
        assert window.basis == "confirmed from the series on 2026-10-07"

    def test_Confirm_WhenTheSeriesIsNamed_PrefillsTheNameFromTheSeries(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        name = confirmed(store, found, only(found))

        assert name == only(found).shape
        assert [c.name for c in store.commitments()] == [name]

    def test_Match_WhenAnotherOccurrenceFollows_TheSeriesStillBelongsToTheCommitment(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        confirmed(store, found, only(found))
        later = date(2026, 10, 20)

        again = find_recurring(
            [*self.rows(), tx(A, date(2026, 10, 15), -1000, "MARMALADE FOUNDRY STUDIO")],
            [],
            later,
        )

        series = only(again)
        assert series.count == 7
        (match,) = match_series(again, store.commitments())
        assert match is not None and match.commitment.id == store.commitments()[0].id

    def test_Match_BeforeAnythingIsConfirmed_BelongsToNothing(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        assert match_series(found, store.commitments()) == [None]

    def test_Confirm_WhenPressedTwice_KeepsOneCommitmentAndRefusesTheSecond(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        confirmed(store, found, only(found))

        with pytest.raises(CommitmentRefused, match="already"):
            confirmed(store, found, only(found))

        assert len(store.commitments()) == 1

    def test_Confirm_WhenTheWayIsUnknown_IsRefusedAndKeepsNothing(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        with pytest.raises(CommitmentRefused):
            confirm(store, found, series_refs(found)[0], "maybe", today=TODAY)

        assert store.commitments() == []


class TestAStoppedSeriesAskedOnce:
    def rows(self) -> list[Transaction]:
        # An unrelated purchase on 2026-09-30 is what shows the account was still being read.
        return [
            *monthly(A, range(1, 6), 8, -2500, "CEDARWICK GYM"),
            tx(A, date(2026, 9, 30), -320, "CORNER BAKERY"),
        ]

    def test_Confirm_AsEnded_ClosesTheWindowAtTheLastOccurrence(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        series = only(found)
        assert series.stopped and series.last_seen == date(2026, 5, 8)

        confirmed(store, found, series, ENDED)

        (commitment,) = store.commitments()
        assert commitment.ended == date(2026, 5, 8)
        assert commitment.windows[0].from_day == date(2026, 1, 8)

    def test_Confirm_AsMissing_LeavesTheWindowOpenSoItCanBeOverdue(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        confirmed(store, found, only(found), MISSING)

        (commitment,) = store.commitments()
        assert commitment.ended is None and commitment.current is not None

    def test_Match_AfterEitherAnswer_TheSeriesIsConfirmedAndNotAskedAgain(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        confirmed(store, found, only(found), ENDED)

        (match,) = match_series(found, store.commitments())

        assert match is not None


class TestTwoProductsToOnePayee:
    def rows(self) -> list[Transaction]:
        return [
            *monthly(A, range(3, 10), 3, -799, "MSFT *SUBSCRIPTION 5512"),
            *monthly(A, range(3, 10), 3, -199, "MSFT *SUBSCRIPTION 5512"),
        ]

    def test_Match_WithTwoPricesToOnePayee_EachSeriesBelongsToTheCommitmentOfItsOwnPrice(
        self, store
    ):
        found = find_recurring(self.rows(), [], TODAY)
        assert sorted(s.usual_minor for s in found) == [199, 799]
        for series in found:
            confirmed(store, found, series)

        commitments = store.commitments()
        matches = match_series(found, commitments)

        for series, match in zip(found, matches, strict=True):
            assert match is not None
            assert match.commitment.windows[0].amount_minor == series.usual_minor

    def test_Match_WhenTheSeriesAreReadInTheOtherOrder_EachStillMeetsItsOwn(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        for series in found:
            confirmed(store, found, series)

        flipped = list(reversed(found))
        for series, match in zip(flipped, match_series(flipped, store.commitments()), strict=True):
            assert match is not None
            assert match.commitment.windows[0].amount_minor == series.usual_minor

    def test_Match_WhenOnlyOneProductWasConfirmed_TheOtherSeriesBelongsToNothing(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        dearer = max(found, key=lambda s: s.usual_minor)
        confirmed(store, found, dearer)

        matches = match_series(found, store.commitments())

        by_amount = {s.usual_minor: m is not None for s, m in zip(found, matches, strict=True)}
        assert by_amount == {799: True, 199: False}


class TestWhatDoesNotBelongToACommitment:
    def rows(self) -> list[Transaction]:
        return monthly(A, range(4, 10), 15, -1000, "MARMALADE FOUNDRY STUDIO")

    def confirmed_store(self, store) -> list[Series]:
        found = find_recurring(self.rows(), [], TODAY)
        confirmed(store, found, only(found))
        return found

    def test_Match_ForTheSamePayeeInAnotherAccount_IsNotThatCommitment(self, store):
        self.confirmed_store(store)
        elsewhere = [replace(row, account_id=B) for row in self.rows()]

        found = find_recurring(elsewhere, [], TODAY)

        assert match_series(found, store.commitments()) == [None]

    def test_Match_ForTheSamePayeeOnAnotherCadence_IsNotThatCommitment(self, store):
        found = self.confirmed_store(store)
        quarterly = [
            tx(A, date(2025, m, 15), -1000, "MARMALADE FOUNDRY STUDIO") for m in (1, 4, 7, 10)
        ] + [tx(A, date(2026, m, 15), -1000, "MARMALADE FOUNDRY STUDIO") for m in (1, 4, 7)]

        again = find_recurring(quarterly, [], TODAY)

        assert only(again).cadence == "quarterly"
        assert match_series(again, store.commitments()) == [None]
        assert only(found).cadence == "monthly"

    def test_Match_ForMoneyComingInFromTheSameName_IsNotThatCommitment(self, store):
        found = self.confirmed_store(store)
        coming_in = replace(only(found), direction="in", is_income=True)

        assert match_series([coming_in], store.commitments()) == [None]

    def test_Match_ForAnotherPayeeAtTheSamePriceOnTheSameDay_IsNotThatCommitment(self, store):
        self.confirmed_store(store)
        other = monthly(A, range(4, 10), 15, -1000, "COBBLESTONE LAUNDRY")

        found = find_recurring(other, [], TODAY)

        assert match_series(found, store.commitments()) == [None]


FIRST_PRINT, SECOND_PRINT, THIRD_PRINT = (
    "FERNHOLLOW PLUS 1041",
    "FERNHOLLOW PLUS EU 2209",
    "FERNHOLLOW PLUS ONE 3300",
)
ENTITY = "Fernhollow Plus"


def gathered(*prints: str) -> dict[tuple[str, str], str]:
    return {(DESCRIPTION, shape_of(p)): ENTITY for p in prints}


class TestAGatheredName:
    def first_print(self) -> list[Transaction]:
        return [tx(A, date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 7)]

    def test_Match_WhenTheNameIsGatheredIntoAnEntityAfterwards_TheCommitmentStillFindsIt(
        self, store
    ):
        found = find_recurring(self.first_print(), [], TODAY)
        confirmed(store, found, only(found))
        entity = store.create_entity(ENTITY, [shape_of(FIRST_PRINT)])
        rows = [*self.first_print(), tx(A, date(2026, 7, 15), -999, SECOND_PRINT)]

        again = find_recurring(
            rows,
            [],
            TODAY,
            entities=gathered(FIRST_PRINT, SECOND_PRINT),
            entity_ids={ENTITY: entity},
        )

        series = only(again)
        assert (series.shape, series.party_entity, series.count) == (ENTITY, entity, 7)
        (match,) = match_series(again, store.commitments())
        assert match is not None

    def test_Match_WhenConfirmedUnderAnEntity_StillFindsItAfterTheOldPrintsAreGone(self, store):
        entity = store.create_entity(ENTITY, [shape_of(FIRST_PRINT)])
        old = [tx(A, date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 5)]
        found = find_recurring(
            old, [], TODAY, entities=gathered(FIRST_PRINT), entity_ids={ENTITY: entity}
        )
        confirmed(store, found, only(found), ENDED)
        assert store.commitments()[0].entity_id == entity

        later = [tx(A, date(2026, m, 15), -999, THIRD_PRINT) for m in range(5, 10)]
        again = find_recurring(
            later,
            [],
            TODAY,
            entities=gathered(THIRD_PRINT),
            entity_ids={ENTITY: entity},
        )

        assert only(again).name_keys.isdisjoint(old_keys(old))
        (match,) = match_series(again, store.commitments())
        assert match is not None

    def test_Match_WhenTheEntityIsAnotherOne_IsNotThatCommitmentByEntity(self, store):
        entity = store.create_entity(ENTITY, [shape_of(FIRST_PRINT)])
        other = store.create_entity("Someone Else", [shape_of("UNRELATED PAYEE 9")])
        old = [tx(A, date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 5)]
        found = find_recurring(
            old, [], TODAY, entities=gathered(FIRST_PRINT), entity_ids={ENTITY: entity}
        )
        confirmed(store, found, only(found), ENDED)
        later = [tx(A, date(2026, m, 15), -999, THIRD_PRINT) for m in range(5, 10)]

        again = find_recurring(
            later,
            [],
            TODAY,
            entities={(DESCRIPTION, shape_of(THIRD_PRINT)): "Someone Else"},
            entity_ids={"Someone Else": other},
        )

        assert match_series(again, store.commitments()) == [None]


def old_keys(rows: list[Transaction]) -> frozenset[str]:
    return only(find_recurring(rows, [], TODAY)).name_keys


class TestAPressNamesItsSeriesWithoutNamingThePayeeOrTheAmount:
    def two(self) -> list[Series]:
        rows = [
            *monthly(A, range(3, 10), 3, -799, "MSFT *SUBSCRIPTION 5512"),
            *monthly(A, range(3, 10), 3, -199, "MSFT *SUBSCRIPTION 5512"),
        ]
        return find_recurring(rows, [], TODAY)

    def test_Refs_ForEverySeries_AreDistinctAndHoldNeitherNameNorAmount(self):
        found = self.two()

        refs = series_refs(found)

        assert len(set(refs)) == len(found) == 2
        for ref in refs:
            assert "msft" not in ref.casefold() and "799" not in ref and "199" not in ref

    def test_Refs_WhenTheSameStoreIsReadAgain_AreTheSame(self):
        assert series_refs(self.two()) == series_refs(self.two())

    def test_Press_ForARefNoSeriesHas_IsRefusedNotAppliedToAnother(self, store):
        found = self.two()

        with pytest.raises(CommitmentRefused, match="Reload"):
            series_index(found, "0000000000000000.0")

        with pytest.raises(CommitmentRefused):
            apply_press(store, found, "confirm", {"ref": ["0000000000000000.0"]}, today=TODAY)
        assert store.commitments() == []

    def test_Press_ForAnActionThePageDoesNotHave_IsRefused(self, store):
        found = self.two()

        with pytest.raises(CommitmentRefused):
            apply_press(store, found, "explode", {"ref": [series_refs(found)[0]]}, today=TODAY)


class TestTheKindsTheStoreKnows:
    def test_Kinds_AreTheOnesTheDetectorSays(self):
        assert set(COMMITMENT_KINDS) == {PULLED, SCHEDULED, HABIT}
