"""How far an account is trusted: stretches on one shared scale, marks, and the one sentence.

Each household below is invented and every expectation was decided with it, in dates worked out
by hand, before `trust` was run: a fault shows as a disagreement and not as a picture to be
judged. Today is 2026-10-05, so the shared scale begins on 2025-10-06 and a day is
100 / 365 percent wide.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.agreement import (
    AGREES,
    HELD_UNMET,
    NONE,
    UNTESTED,
    Agreement,
    HeldBack,
    ListingTested,
    Standing,
)
from obdi.standing_data import AccountStanding
from obdi.trust import (
    WINDOW_DAYS,
    MarkKind,
    Rung,
    Stretch,
    Trust,
    month_marks,
    place,
    trust_of,
    window_start,
)

TODAY = date(2026, 10, 5)


def d(text: str) -> date:
    return date.fromisoformat(text)


def standing(
    *,
    state: str = AGREES,
    known_from: str | None = None,
    known_to: str | None = None,
    through: str | None = None,
    held: HeldBack | None = None,
    listing: tuple[ListingTested, ...] = (),
    locked: str | None = None,
    broken: bool = False,
) -> AccountStanding:
    own = Agreement(
        state=state,
        known_from=d(known_from) if known_from else None,
        known_to=d(known_to) if known_to else None,
        known_count=2 if known_from else 0,
        tested_count=1 if through else 0,
        through=d(through) if through else None,
        held=held,
        movement_checked=True,
        conflicts=(),
        listing_tested=listing,
    )
    return AccountStanding(Standing(own, None), d(locked) if locked else None, broken)


def shape(found: tuple[Stretch, ...]) -> list[tuple[str, str, str]]:
    return [(s.rung.value, s.start.isoformat(), s.end.isoformat()) for s in found]


class TestTheTrustOfAnOrdinaryAccount:
    """Locked in to April, adds up to July, transactions since then with nothing to test them."""

    def trust(self) -> Trust:
        return trust_of(
            first=d("2025-01-01"),
            newest=TODAY,
            standing=standing(
                known_from="2025-02-01",
                known_to="2026-07-10",
                through="2026-07-10",
                locked="2026-04-10",
            ),
            today=TODAY,
        )

    def test_Account_LockedInAddsUpAndWaiting_StretchesRunRungByRung(self) -> None:
        assert shape(self.trust().stretches) == [
            ("held", "2025-01-01", "2025-01-31"),
            ("locked", "2025-02-01", "2026-04-10"),
            ("adds-up", "2026-04-11", "2026-07-10"),
            ("held", "2026-07-11", "2026-10-05"),
        ]

    def test_Account_LockedInAddsUpAndWaiting_SentenceSaysWhoDidWhatAndWhatIsWaiting(self) -> None:
        assert self.trust().sentence == (
            "Locked in to 2026-04-10. Adds up to the known balances to 2026-07-10. "
            "Nothing to check against since 2026-07-10 (2 months ago)."
        )

    def test_Account_LockedInAddsUpAndWaiting_ShortSentenceDropsTheWaitTheRowSaysElsewhere(
        self,
    ) -> None:
        trust = self.trust()
        assert (
            trust.short == "Locked in to 2026-04-10. Adds up to the known balances to 2026-07-10."
        )
        assert trust.waiting_since == d("2026-07-10")

    def test_Account_HistoryOlderThanTheWindow_IsMarkedAtTheLeftEdge(self) -> None:
        assert self.trust().earlier is True


class TestAnAccountTestedOnlyByItsStatement:
    """One statement's listing tests the days from its start to its closing, and nothing else is
    known: the earlier proof rail drew such an account as wholly unchecked."""

    def trust(self) -> Trust:
        return trust_of(
            first=d("2026-07-01"),
            newest=d("2026-09-30"),
            standing=standing(
                known_from="2026-09-30",
                known_to="2026-09-30",
                through="2026-09-30",
                listing=(ListingTested(d("2026-09-30"), 20, d("2026-07-01")),),
            ),
            today=TODAY,
        )

    def test_Account_TestedOnlyByItsStatementListing_TheListedDaysAddUp(self) -> None:
        assert shape(self.trust().stretches) == [("adds-up", "2026-07-01", "2026-09-30")]

    def test_Account_TestedOnlyByItsStatementListing_SaysItAddsUpAndNothingIsWaiting(self) -> None:
        trust = self.trust()
        assert trust.sentence == "Adds up to the known balances to 2026-09-30."
        assert trust.waiting_since is None
        assert not [m for m in trust.marks if m.kind is MarkKind.FILE_WANTED]

    def test_Account_StatementListingStartsBeforeItsFirstTransaction_BarBeginsAtTheTransaction(
        self,
    ) -> None:
        trust = trust_of(
            first=d("2026-07-05"),
            newest=d("2026-09-30"),
            standing=standing(
                known_from="2026-09-30",
                known_to="2026-09-30",
                through="2026-09-30",
                listing=(ListingTested(d("2026-09-30"), 3, d("2026-07-01")),),
            ),
            today=TODAY,
        )
        assert shape(trust.stretches) == [("adds-up", "2026-07-05", "2026-09-30")]


class TestAnAccountYoungerThanTheWindow:
    def test_Account_FirstTransactionTenWeeksAgo_BarStartsPartWayAlongWithABareLineBefore(
        self,
    ) -> None:
        trust = trust_of(first=d("2026-08-01"), newest=TODAY, standing=None, today=TODAY)
        assert shape(trust.stretches) == [("held", "2026-08-01", "2026-10-05")]
        placed = place(trust.stretches[0].start, trust.stretches[0].end, TODAY)
        assert placed is not None
        # 299 days of the 365 come before 2026-08-01, and 66 days (31 + 30 + 5) are held, today
        # included.
        assert placed.left == pytest.approx(299 / 365 * 100)
        assert placed.width == pytest.approx(66 / 365 * 100)
        assert trust.earlier is False

    def test_Account_NoKnownBalance_SaysThereIsNothingToCheckAgainstAndNothingElse(self) -> None:
        trust = trust_of(first=d("2026-08-01"), newest=TODAY, standing=None, today=TODAY)
        assert trust.sentence == "Nothing to check against."

    def test_Account_OnlyOneKnownBalance_SaysThereIsNothingToCheckAgainst(self) -> None:
        trust = trust_of(
            first=d("2026-08-01"),
            newest=TODAY,
            standing=standing(state=UNTESTED, known_from="2026-09-01", known_to="2026-09-01"),
            today=TODAY,
        )
        assert shape(trust.stretches) == [("held", "2026-08-01", "2026-10-05")]
        assert trust.sentence == "Nothing to check against."


class TestAnAccountHoldingNothing:
    def test_Account_NothingHeld_DrawsNoStretchAndSaysSo(self) -> None:
        trust = trust_of(first=None, newest=None, standing=None, today=TODAY)
        assert trust.stretches == ()
        assert trust.nothing_held is True
        assert trust.sentence == "Nothing held yet."

    def test_Account_NothingHeldButAFileWanted_StillMarksTheDaysWanted(self) -> None:
        trust = trust_of(
            first=None,
            newest=None,
            standing=None,
            wanted=[(d("2026-09-01"), d("2026-09-30"))],
            today=TODAY,
        )
        assert [(m.kind, m.start.isoformat()) for m in trust.marks] == [
            (MarkKind.FILE_WANTED, "2026-09-01")
        ]


class TestAnAccountThatDoesNotAddUp:
    def trust(self, *, locked: str | None = None, broken: bool = False) -> Trust:
        return trust_of(
            first=d("2026-01-01"),
            newest=TODAY,
            standing=standing(
                state=HELD_UNMET,
                known_from="2026-02-01",
                known_to="2026-09-02",
                through="2026-08-18",
                held=HeldBack(HELD_UNMET, d("2026-09-02"), ("starling",), ""),
                locked=locked,
                broken=broken,
            ),
            today=TODAY,
        )

    def test_Account_StopsAddingUpOnADay_MarksThatDayAndSaysSo(self) -> None:
        trust = self.trust()
        assert [(m.kind, m.start.isoformat()) for m in trust.marks] == [
            (MarkKind.NOT_ADDING_UP, "2026-09-02")
        ]
        assert trust.sentence == (
            "Adds up to the known balances to 2026-08-18. Does not add up from 2026-09-02."
        )

    def test_Account_StopsAddingUpOnADay_NothingFurtherIsSaidOfWhatIsWaiting(self) -> None:
        assert "Nothing to check against" not in self.trust().sentence

    def test_Account_ALockedStretchHasChanged_MarksItAndSaysItChanged(self) -> None:
        trust = self.trust(locked="2026-06-30", broken=True)
        assert (MarkKind.LOCK_CHANGED, d("2026-06-30")) in [(m.kind, m.start) for m in trust.marks]
        assert trust.sentence.startswith(
            "Locked in to 2026-06-30, but that stretch has changed since. "
        )
        assert Rung.LOCKED in {s.rung for s in trust.stretches}


class TestAFileWanted:
    def test_Account_AFileIsWantedForSomeDays_ThoseDaysAreMarked(self) -> None:
        trust = trust_of(
            first=d("2026-01-01"),
            newest=TODAY,
            standing=None,
            wanted=[(d("2026-05-11"), d("2026-06-10")), (d("2026-08-01"), d("2026-09-30"))],
            today=TODAY,
        )
        wanted = [m for m in trust.marks if m.kind is MarkKind.FILE_WANTED]
        assert [(m.start.isoformat(), m.end.isoformat()) for m in wanted] == [
            ("2026-05-11", "2026-06-10"),
            ("2026-08-01", "2026-09-30"),
        ]


class TestTheSharedScale:
    def test_Scale_TwoAccountsWithTheSameDates_DrawBarsOfTheSameWidths(self) -> None:
        """One account holds three years and the other six months; both add up for the same last
        month, so the two bars must draw that month the same width at the same place."""
        old = trust_of(
            first=d("2023-10-01"),
            newest=TODAY,
            standing=standing(known_from="2023-11-01", known_to="2026-09-05", through="2026-09-05"),
            today=TODAY,
        )
        young = trust_of(
            first=d("2026-04-01"),
            newest=TODAY,
            standing=standing(known_from="2026-04-30", known_to="2026-09-05", through="2026-09-05"),
            today=TODAY,
        )

        def held_tail(found: tuple[Stretch, ...]) -> tuple[float, float]:
            tail = found[-1]
            assert tail.rung is Rung.HELD
            placed = place(tail.start, tail.end, TODAY)
            assert placed is not None
            return placed.left, placed.width

        assert held_tail(old.stretches) == held_tail(young.stretches)
        # 2026-09-06 to today is 30 days, and the window's last day is today.
        assert held_tail(old.stretches)[1] == pytest.approx(30 / 365 * 100)
        assert sum(1 for _ in old.stretches) > 0

    def test_Scale_AStretchEntirelyBeforeTheWindow_IsNotDrawn(self) -> None:
        assert place(d("2024-01-01"), d("2025-10-05"), TODAY) is None

    def test_Scale_AStretchStraddlingTheWindowsEdge_IsClippedToIt(self) -> None:
        placed = place(d("2025-01-01"), d("2025-10-10"), TODAY)
        assert placed is not None
        assert placed.left == 0.0
        assert placed.width == pytest.approx(5 / 365 * 100)

    def test_Scale_TheWindow_RunsTwelveMonthsEndingToday(self) -> None:
        assert WINDOW_DAYS == 365
        assert window_start(TODAY) == d("2025-10-06")
        placed = place(d("2025-10-06"), TODAY, TODAY)
        assert placed is not None
        assert placed.left == 0.0
        assert placed.width == pytest.approx(100.0)

    def test_Scale_MonthNames_AreTwelvePrintedOnceFromTheWindowsStart(self) -> None:
        marks = month_marks(TODAY)
        assert [name for name, _ in marks] == [
            "Oct",
            "Nov",
            "Dec",
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
        ]
        assert marks[0][1] == 0.0
        assert marks[1][1] == pytest.approx(26 / 365 * 100)
        assert marks[-1][1] == pytest.approx(330 / 365 * 100)

    def test_Scale_WindowStartingJustBeforeAMonthsEnd_DoesNotPrintTwoNamesOnTopOfEachOther(
        self,
    ) -> None:
        # Starts 2025-10-29, so November begins three days in: only November is named there.
        marks = month_marks(d("2026-10-28"))
        assert marks[0][0] == "Nov"
        assert marks[0][1] == pytest.approx(3 / 365 * 100)


class TestAnAccountWithNothingToTest:
    def test_Trust_StandingMissing_SaysNothingToCheckAgainstRatherThanSomethingFalse(self) -> None:
        trust = trust_of(first=d("2026-09-01"), newest=TODAY, standing=None, today=TODAY)
        assert trust.sentence == "Nothing to check against."
        assert trust.adds_up_to is None

    def test_Trust_NoKnownBalanceAtAll_IsTheSameAsAMissingStanding(self) -> None:
        trust = trust_of(
            first=d("2026-09-01"), newest=TODAY, standing=standing(state=NONE), today=TODAY
        )
        assert trust.sentence == "Nothing to check against."
