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
    PriceOffer,
    apply_press,
    change_price,
    confirm,
    dismiss,
    match_dismissals,
    match_series,
    restore,
    series_index,
    series_refs,
    tally_of,
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


def priced(amounts: list[int], first_month: int = 3) -> list[Transaction]:
    return [
        tx(A, date(2026, first_month + i, 3), -minor, "ZEPHYRINE STREAMING")
        for i, minor in enumerate(amounts)
    ]


class TestAChangedPrice:
    """KNOWN ANSWERS. A subscription on the 3rd: £10.00 from March to June, £12.00 in July and
    August. Confirmed from that history the window is 1000; the newest run (July and August) began
    on 2026-07-03, so the offer is 1200 from then. Pressing it makes windows (2026-03-03 to
    2026-07-02 at 1000) and (2026-07-03 open at 1200). A later rise to 1500 for the payments of
    August and September (the 1200 window then ending 2026-08-02) makes a third; nothing is ever
    merged or overwritten."""

    def test_Offer_ForARiseAfterConfirming_IsTheNewAmountFromTheFirstDayOfTheRun(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        after = find_recurring(priced([1000] * 4 + [1200] * 2), [], TODAY)

        (match,) = match_series(after, store.commitments())

        assert match is not None and match.offer == PriceOffer(1200, date(2026, 7, 3))

    def test_Press_OnTheOffer_ClosesTheOldWindowTheDayBeforeAndKeepsBoth(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        after = find_recurring(priced([1000] * 4 + [1200] * 2), [], TODAY)

        change_price(store, after, series_refs(after)[0], today=TODAY)

        (commitment,) = store.commitments()
        old, new = commitment.windows
        assert (old.amount_minor, old.from_day, old.to_day) == (
            1000,
            date(2026, 3, 3),
            date(2026, 7, 2),
        )
        assert (new.amount_minor, new.from_day, new.to_day) == (1200, date(2026, 7, 3), None)
        assert new.basis == "price changed from 2026-07-03, confirmed on 2026-10-07"

    def test_Offer_AfterThePress_IsGoneEvenWhileTheDetectorStillSaysChanged(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        after = find_recurring(priced([1000] * 4 + [1200] * 2), [], TODAY)
        assert only(after).changed
        change_price(store, after, series_refs(after)[0], today=TODAY)

        (match,) = match_series(after, store.commitments())

        assert match is not None and match.offer is None

    def test_Offer_WhenTheNewPriceHasBecomeTheUsualOne_IsStillMadeUntilTheWindowHoldsIt(
        self, store
    ):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        long_after = find_recurring(priced([1000] * 3 + [1200] * 4), [], TODAY)
        assert not only(long_after).changed

        (match,) = match_series(long_after, store.commitments())

        assert match is not None and match.offer == PriceOffer(1200, date(2026, 6, 3))

    def test_Window_WhenAnotherRiseFollows_BecomesTheThirdAndTheFirstTwoStay(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        second = find_recurring(priced([1000] * 4 + [1200] * 2), [], TODAY)
        change_price(store, second, series_refs(second)[0], today=TODAY)
        third = find_recurring(priced([1200] * 6 + [1500] * 2, 2), [], date(2026, 10, 20))

        change_price(store, third, series_refs(third)[0], today=date(2026, 10, 20))

        (commitment,) = store.commitments()
        assert [(w.amount_minor, w.to_day) for w in commitment.windows] == [
            (1000, date(2026, 7, 2)),
            (1200, date(2026, 8, 2)),
            (1500, None),
        ]

    def test_Offer_WithinTheDetectorsToleranceOfTheWindow_IsNotMade(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        after = find_recurring(priced([1000] * 5 + [1020]), [], TODAY)

        (match,) = match_series(after, store.commitments())

        assert match is not None and match.offer is None

    def test_Offer_ForABillThatVariesEveryTime_IsNotMade(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        varying = find_recurring(priced([1000, 1500, 800, 1900, 700, 1700]), [], TODAY)
        assert not only(varying).steady

        (match,) = match_series(varying, store.commitments())

        assert match is not None and match.offer is None

    def test_Offer_ForACommitmentThatHasEnded_IsNotMade(self, store):
        before = find_recurring(priced([1000] * 4), [], TODAY)
        confirmed(store, before, only(before))
        store.end_commitment(store.commitments()[0].id, date(2026, 6, 3))
        after = find_recurring(priced([1000] * 4 + [1200] * 2), [], TODAY)

        (match,) = match_series(after, store.commitments())

        assert match is not None and match.offer is None

    def test_Press_ForASeriesWhoseWindowAlreadyHoldsThePrice_IsRefused(self, store):
        found = find_recurring(priced([1000] * 6), [], TODAY)
        confirmed(store, found, only(found))

        with pytest.raises(CommitmentRefused, match="no new price"):
            change_price(store, found, series_refs(found)[0], today=TODAY)

        assert len(store.commitments()[0].windows) == 1


class TestADismissedSeries:
    """KNOWN ANSWERS. Three series are found (a gym, a streaming service, a cleaner); the owner
    says the cleaner is not a commitment and confirms the gym. The tally is 3 found, 1 confirmed,
    1 dismissed, 1 to look at, and the detector's measured precision is 1 of 3. The dismissal
    follows the payee into an entity, stays on its own account and cadence, and never covers a
    series that has been confirmed."""

    def rows(self) -> list[Transaction]:
        return [
            *monthly(A, range(4, 10), 15, -1000, "MARMALADE FOUNDRY STUDIO"),
            *monthly(A, range(4, 10), 21, -1200, "WINDOW CLEANER"),
            *monthly(A, range(4, 10), 9, -2500, "CEDARWICK GYM"),
        ]

    def by(self, found: list[Series], fragment: str) -> int:
        return next(i for i, s in enumerate(found) if fragment in s.shape)

    def test_Tally_WithOneConfirmedOneDismissedAndOneOpen_CountsEachOnce(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        refs = series_refs(found)
        confirm(store, found, refs[self.by(found, "cedarwick")], CONFIRM, today=TODAY)
        dismiss(store, found, refs[self.by(found, "window")])

        answers = tally_of(
            match_series(found, store.commitments()),
            match_dismissals(found, store.dismissals()),
        )

        assert (answers.found, answers.confirmed, answers.dismissed, answers.to_look_at) == (
            3,
            1,
            1,
            1,
        )
        assert answers.answered

    def test_Tally_BeforeAnyAnswer_IsNotAnswered(self, store):
        found = find_recurring(self.rows(), [], TODAY)

        answers = tally_of(
            match_series(found, store.commitments()),
            match_dismissals(found, store.dismissals()),
        )

        assert (answers.found, answers.to_look_at, answers.answered) == (3, 3, False)

    def test_Dismissal_CoversOnlyItsOwnSeries(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        dismiss(store, found, series_refs(found)[self.by(found, "window")])

        covered = [d is not None for d in match_dismissals(found, store.dismissals())]

        assert covered == [i == self.by(found, "window") for i in range(3)]

    def test_Dismissal_WhenTheNameIsGatheredIntoAnEntityAfterwards_StillCoversIt(self, store):
        rows = [tx(A, date(2026, m, 15), -999, FIRST_PRINT) for m in range(1, 7)]
        found = find_recurring(rows, [], TODAY)
        dismiss(store, found, series_refs(found)[0])
        entity = store.create_entity(ENTITY, [shape_of(FIRST_PRINT)])

        gathered_again = find_recurring(
            [*rows, tx(A, date(2026, 7, 15), -999, SECOND_PRINT)],
            [],
            TODAY,
            entities=gathered(FIRST_PRINT, SECOND_PRINT),
            entity_ids={ENTITY: entity},
        )

        assert match_dismissals(gathered_again, store.dismissals()) != [None]

    def test_Dismissal_ForTheSamePayeeInAnotherAccount_DoesNotCoverIt(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        dismiss(store, found, series_refs(found)[self.by(found, "window")])
        elsewhere = find_recurring(
            [replace(row, account_id=B) for row in self.rows()], [], TODAY
        )

        assert match_dismissals(elsewhere, store.dismissals()) == [None, None, None]

    def test_Dismissal_ForTheSamePayeeOnAnotherCadence_DoesNotCoverIt(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        dismiss(store, found, series_refs(found)[self.by(found, "window")])
        quarterly = [
            tx(A, date(2025, m, 21), -1200, "WINDOW CLEANER") for m in (1, 4, 7, 10)
        ] + [tx(A, date(2026, m, 21), -1200, "WINDOW CLEANER") for m in (1, 4, 7)]

        again = find_recurring(quarterly, [], TODAY)

        assert only(again).cadence == "quarterly"
        assert match_dismissals(again, store.dismissals()) == [None]

    def test_Dismiss_ForAConfirmedSeries_IsRefusedAndKeepsNothing(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        ref = series_refs(found)[self.by(found, "window")]
        confirm(store, found, ref, CONFIRM, today=TODAY)

        with pytest.raises(CommitmentRefused, match="already a commitment"):
            dismiss(store, found, ref)

        assert store.dismissals() == []

    def test_Restore_PutsTheSeriesBackAndAFurtherRestoreIsRefused(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        ref = series_refs(found)[self.by(found, "window")]
        dismiss(store, found, ref)

        restore(store, found, ref)

        assert store.dismissals() == []
        with pytest.raises(CommitmentRefused, match="not set aside"):
            restore(store, found, ref)

    def test_Dismiss_WhenPutBackAndPressedAgain_KeepsOneLiveDismissal(self, store):
        found = find_recurring(self.rows(), [], TODAY)
        ref = series_refs(found)[0]
        dismiss(store, found, ref)
        restore(store, found, ref)

        dismiss(store, found, ref)

        assert len(store.dismissals()) == 1


class TestTheKindsTheStoreKnows:
    def test_Kinds_AreTheOnesTheDetectorSays(self):
        assert set(COMMITMENT_KINDS) == {PULLED, SCHEDULED, HABIT}
