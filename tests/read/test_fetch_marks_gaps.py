"""What a decision does to the files still to fetch and to Today, and what it must not do.

Known answers, decided before the first run (over `fetch_marks_world`, TODAY 2026-10-05):

  * A supported mark over card-virgin's whole hole (06-05..07-04) takes its one gap out of the
    list and the account moves to those needing nothing; the household goes from 12 things to
    fetch for 10 accounts to 11 for 9 (13 for 11 to 12 for 10 while a lone statement still
    counted as one known balance that tested nothing).
  * The same mark over a hole the feed lists two rows in is contradicted: the gap stays.
  * A mark over card-hole's 03-11..03-31 splits its inferred hole 03-11..04-10: what remains is
    04-01..04-10, which still names the closing day 04-10 and still ends on the inferred day. A
    mark over 04-01..04-10 leaves 03-11..03-31, whose end is now a day he named.
  * A known gap with a day to look again on hides the gap until that day and then returns it,
    saying so.
  * Rolling scope of 7 months on main: on 2026-10-05 the scope begins 2026-03-05, so March's
    missing export (03-01..03-31) is trimmed to 03-05..03-31; on 2026-11-02 it begins 2026-04-02
    and the whole gap is out of scope, having left on 2026-11-01.
  * Verification: every account's standing is equal before and after any decision, and Today's
    items are equal but for the one that asks for the next statement.
"""

from __future__ import annotations

import pytest

from fetch_gaps_world import TODAY, D
from fetch_marks_world import (
    add_feed_rows_in_virgin_hole,
    household,
    mark,
    marks_read,
    read_at,
    report_with,
    scope,
    standings_of,
)
from obdi.ingest.store import Store
from obdi.read.fetch_gaps import GapKind
from obdi.read.fetch_marks import MarkKind, remove_mark
from obdi.read.overview import standing_items_from

VIRGIN = (D(2026, 6, 5), D(2026, 7, 4))


@pytest.fixture
def db(tmp_path):
    return household(tmp_path)


def gaps_of(report, account):
    return [g for g in report.gaps if g.account == account]


def marked_virgin(db):
    return mark(db, account="card-virgin", kind="nothing-to-fetch",
                first_day=VIRGIN[0], last_day=VIRGIN[1])


class TestAGapInsideASupportedMarkLeavesTheList:
    def test_Report_WhenTheHoleIsMarkedNothingToFetch_TheGapLeavesAndIsSetAsideWithItsDates(
        self, db
    ):
        before = report_with(db, None)
        marked_virgin(db)

        after = report_with(db, marks_read(db))

        assert (len(before.gaps), len(before.needing)) == (12, 10)
        assert (len(after.gaps), len(after.needing)) == (11, 9)
        assert gaps_of(after, "card-virgin") == []
        (aside,) = after.set_aside
        assert (aside.account, aside.kind, aside.first_day, aside.last_day) == (
            "card-virgin", MarkKind.NOTHING_TO_FETCH, VIRGIN[0], VIRGIN[1])
        assert aside.origin == "owner"

    def test_Report_WhenTheFeedListsRowsInTheHole_TheContradictedMarkHidesNothing(self, db):
        add_feed_rows_in_virgin_hole(db)
        marked_virgin(db)

        after = report_with(db, marks_read(db))

        assert [g.kind for g in gaps_of(after, "card-virgin")] == [GapKind.HOLE_BETWEEN]
        assert after.set_aside == ()
        assert len(after.marks.contradicted) == 1

    def test_Report_WhenTheMarkIsOnAnotherAccountOrSource_TheGapStays(self, db):
        mark(db, account="card-hole", kind="nothing-to-fetch",
             first_day=D(2026, 3, 11), last_day=D(2026, 4, 10))
        mark(db, account="card-virgin", source="starling-csv", kind="known-gap",
             first_day=VIRGIN[0], last_day=VIRGIN[1])

        after = report_with(db, marks_read(db))

        assert [g.kind for g in gaps_of(after, "card-virgin")] == [GapKind.HOLE_BETWEEN]

    def test_Report_WhenTheMarkIsUndone_TheGapReturnsExactlyAsItWas(self, db):
        before = report_with(db, None)
        made = marked_virgin(db)
        with Store(db) as store:
            remove_mark(store, made.id, "2026-10-06T00:00:00+00:00")

        assert report_with(db, marks_read(db)).gaps == before.gaps


class TestAGapPartlyInsideAMarkIsSplit:
    def test_Report_WhenAMarkCoversTheFirstPartOfAnInferredHole_TheRestStaysWithDatesThatSaySo(
        self, db
    ):
        mark(db, account="card-hole", kind="nothing-to-fetch",
             first_day=D(2026, 3, 11), last_day=D(2026, 3, 31))

        (rest,) = gaps_of(report_with(db, marks_read(db)), "card-hole")

        assert (rest.first_day, rest.last_day) == (D(2026, 4, 1), D(2026, 4, 10))
        assert rest.split_from == (D(2026, 3, 11), D(2026, 4, 10))
        assert rest.closings == (D(2026, 4, 10),) and rest.probably == 1
        assert rest.last_day_inferred, "the end is still the missing statement's expected close"

    def test_Report_WhenTheMarkCutsTheInferredEnd_TheNewEndIsADayHeNamedNotAnInference(self, db):
        mark(db, account="card-hole", kind="known-gap",
             first_day=D(2026, 4, 1), last_day=D(2026, 4, 10))

        (rest,) = gaps_of(report_with(db, marks_read(db)), "card-hole")

        assert (rest.first_day, rest.last_day) == (D(2026, 3, 11), D(2026, 3, 31))
        assert not rest.last_day_inferred and rest.closings == ()

    def test_Report_WhenAMarkCoversTheMiddleOfAGap_TwoPartsRemain(self, db):
        mark(db, account="card-hole", kind="known-gap",
             first_day=D(2026, 3, 20), last_day=D(2026, 3, 25))

        parts = gaps_of(report_with(db, marks_read(db)), "card-hole")

        assert [(g.first_day, g.last_day) for g in parts] == [
            (D(2026, 3, 11), D(2026, 3, 19)), (D(2026, 3, 26), D(2026, 4, 10))]


class TestKnownGapIsAnAcknowledgement:
    def test_Report_WhenAKnownGapIsAcknowledged_TheGapLeavesWithoutAnyReasonOrEvidence(self, db):
        made = mark(db, account="card-virgin", kind="known-gap",
                    first_day=VIRGIN[0], last_day=VIRGIN[1])

        after = report_with(db, marks_read(db))

        assert gaps_of(after, "card-virgin") == []
        assert made.note == ""
        assert after.set_aside[0].kind is MarkKind.KNOWN_GAP

    def test_Report_WhenTheLookAgainDayArrives_TheGapReturnsAndSaysWhy(self, db):
        mark(db, account="card-virgin", kind="known-gap", first_day=VIRGIN[0],
             last_day=VIRGIN[1], review_on=D(2026, 11, 1))

        before_day = report_with(db, read_at(db, D(2026, 10, 31)), D(2026, 10, 31))
        on_day = report_with(db, read_at(db, D(2026, 11, 1)), D(2026, 11, 1))

        assert gaps_of(before_day, "card-virgin") == []
        (back,) = gaps_of(on_day, "card-virgin")
        assert "look again on 2026-11-01" in back.reminder
        assert (back.first_day, back.last_day) == VIRGIN

    def test_Report_WhenAGapIsFlaggedWhereTheStoreSawNone_ItIsKeptAndSetsNothingAside(self, db):
        made = mark(db, account="card-quiet", kind="known-gap",
                    first_day=D(2026, 2, 1), last_day=D(2026, 2, 28), note="a statement is missing")

        after = report_with(db, marks_read(db))

        assert made.id in {r.mark.id for r in after.marks.readings}
        assert after.set_aside == ()


class TestTodayAndThePageReadOneAnswer:
    def items(self, db, marks):
        from obdi.read.fetch_gaps import STATEMENT_SOURCES
        from obdi.read.fetch_marks import period_is_set_aside

        return standing_items_from(
            standings_of(db), lambda ref: ref, lambda ref: False, TODAY,
            lambda ref, first, last: period_is_set_aside(
                marks, ref, first, last, TODAY, statement_sources=STATEMENT_SOURCES),
        )

    def named(self, items):
        return {ref for item in items if item.kind == "statement-due" for ref in item.accounts}

    def test_Today_WhenTheAwaitedPeriodIsSetAside_NoLongerNamesTheAccountAndNeitherDoesThePage(
        self, db
    ):
        mark(db, account="card-late-one", kind="known-gap",
             first_day=D(2026, 6, 11), last_day=D(2026, 8, 1))
        marks = marks_read(db)

        report = report_with(db, marks)

        assert self.named(self.items(db, marks)) == {"card-behind"}
        assert not any(g.kind is GapKind.NEWER_STATEMENT for g in gaps_of(report, "card-late-one"))

    def test_Today_WhenOnlyPartOfTheAwaitedPeriodIsSetAside_StillNamesTheAccountAndSoDoesThePage(
        self, db
    ):
        mark(db, account="card-late-one", kind="known-gap",
             first_day=D(2026, 6, 11), last_day=D(2026, 7, 31))
        marks = marks_read(db)

        report = report_with(db, marks)

        assert self.named(self.items(db, marks)) == {"card-behind", "card-late-one"}
        (rest,) = [g for g in gaps_of(report, "card-late-one") if g.kind is GapKind.NEWER_STATEMENT]
        assert (rest.first_day, rest.last_day) == (D(2026, 8, 1), D(2026, 8, 1))

    def test_Today_WhenAContradictedMarkCoversTheAwaitedPeriod_StillNamesTheAccount(self, db):
        mark(db, account="card-late-one", kind="nothing-to-fetch",
             first_day=D(2026, 6, 11), last_day=D(2026, 8, 1))
        marks = marks_read(db)

        assert "card-late-one" in self.named(self.items(db, marks))


class TestVerificationIsUnchangedByAnyDecision:
    def test_Standings_AfterEveryKindOfDecision_AreEqualToBefore(self, db):
        before = standings_of(db)

        marked = [
            mark(db, account="card-virgin", kind="nothing-to-fetch",
                 first_day=VIRGIN[0], last_day=VIRGIN[1]),
            mark(db, account="card-late-one", kind="known-gap",
                 first_day=D(2026, 6, 11), last_day=D(2026, 8, 1)),
            mark(db, account="main", kind="other", note="the bank lost it",
                 first_day=D(2026, 3, 1), last_day=D(2026, 3, 31)),
        ]
        scope(db, months=6)

        assert marked
        assert standings_of(db) == before

    def test_Today_AfterTheDecisions_DiffersOnlyInTheItemThatAsksForTheNextStatement(self, db):
        from obdi.read.fetch_gaps import STATEMENT_SOURCES
        from obdi.read.fetch_marks import period_is_set_aside

        before = standing_items_from(
            standings_of(db), lambda ref: ref, lambda ref: False, TODAY)
        mark(db, account="card-late-one", kind="known-gap",
             first_day=D(2026, 6, 11), last_day=D(2026, 8, 1))
        marks = marks_read(db)
        after = standing_items_from(
            standings_of(db), lambda ref: ref, lambda ref: False, TODAY,
            lambda ref, first, last: period_is_set_aside(
                marks, ref, first, last, TODAY, statement_sources=STATEMENT_SOURCES))

        others = [i for i in before if i.kind != "statement-due"]
        assert others == [i for i in after if i.kind != "statement-due"]
        assert [i.kind for i in before].count("statement-due") == 1
        assert [i.accounts for i in after if i.kind == "statement-due"] == [("card-behind",)]


class TestAScopeTrimsWhatIsLookedFor:
    def test_Report_WhenAFixedScopeBeginsAfterAGap_TheGapIsOutOfScopeAndCounted(self, db):
        scope(db, first_day=D(2026, 4, 1))

        after = report_with(db, marks_read(db))

        months = [g for g in gaps_of(after, "main") if g.kind is GapKind.EXPORT_MONTHS]
        assert months == []
        (outside,) = [o for o in after.out_of_scope if o.gap_kind == "export-months"]
        assert (outside.first_day, outside.last_day, outside.left_on) == (
            D(2026, 3, 1), D(2026, 3, 31), None)

    def test_Report_WhenAFixedScopeBeginsInsideAGap_OnlyTheDaysFromItRemain(self, db):
        scope(db, first_day=D(2026, 3, 15))

        (rest,) = [g for g in gaps_of(report_with(db, marks_read(db)), "main")
                   if g.kind is GapKind.EXPORT_MONTHS]

        assert (rest.first_day, rest.last_day) == (D(2026, 3, 15), D(2026, 3, 31))

    def test_Report_WhenARollingScopeMovesOnPastAGap_SaysTheDayItLeftUnfilled(self, db):
        scope(db, months=7)

        early = report_with(db, read_at(db, TODAY), TODAY)
        late = report_with(db, read_at(db, D(2026, 11, 2)), D(2026, 11, 2))

        (rest,) = [g for g in gaps_of(early, "main") if g.kind is GapKind.EXPORT_MONTHS]
        assert (rest.first_day, rest.last_day) == (D(2026, 3, 5), D(2026, 3, 31))
        assert not [g for g in gaps_of(late, "main") if g.kind is GapKind.EXPORT_MONTHS]
        (left,) = [o for o in late.out_of_scope if o.gap_kind == "export-months"]
        assert left.left_on == D(2026, 11, 1)

    def test_Report_WhenTheHouseholdDefaultIsSetAndAnAccountHasItsOwn_TheAccountsOwnWins(
        self, db
    ):
        scope(db, first_day=D(2026, 4, 1), account="")
        scope(db, first_day=D(2025, 1, 1), account="main")

        after = report_with(db, marks_read(db))

        assert [g for g in gaps_of(after, "main") if g.kind is GapKind.EXPORT_MONTHS]
        assert [g.first_day for g in gaps_of(after, "card-hole")] == [D(2026, 4, 1)]

    def test_Report_WhenTheScopeIsWiderThanEveryGap_NothingIsHidden(self, db):
        before = report_with(db, None)
        scope(db, first_day=D(2020, 1, 1))

        assert report_with(db, marks_read(db)).gaps == before.gaps

    def test_Report_WhenTheFirstKnownBalanceIsBeforeTheScope_StandingIsUnchangedAndReportCarriesIt(
        self, db
    ):
        before = standings_of(db)
        scope(db, first_day=D(2026, 6, 1))

        after = report_with(db, marks_read(db))

        assert after.first_known_balance["main"] == D(2026, 1, 11) < D(2026, 6, 1)
        assert standings_of(db) == before


class TestWideningAndNarrowingScopeRoundTrips:
    """G1 is main's missing March export (ends 03-31); G2 is card-virgin's hole (06-05..07-04).

    Scope 6 months is 2026-04-05: G1 is out of scope and G2 flagged. Acknowledging G2 and then
    widening to 12 months brings G1 back and leaves G2 acknowledged; narrowing hides G1 again;
    undoing the acknowledgement flags G2 once more. Nothing is deleted at any step.
    """

    def flagged(self, db):
        report = report_with(db, marks_read(db))
        return (
            any(g.kind is GapKind.EXPORT_MONTHS for g in gaps_of(report, "main")),
            any(g.kind is GapKind.HOLE_BETWEEN for g in gaps_of(report, "card-virgin")),
            [s.kind for s in report.set_aside if s.account == "card-virgin"],
        )

    def test_Scope_WidenedAndNarrowedAndTheAcknowledgementUndone_RestoresEachGapExactly(self, db):
        scope(db, months=6, account="")
        assert self.flagged(db) == (False, True, [])

        made = mark(db, account="card-virgin", kind="known-gap",
                    first_day=VIRGIN[0], last_day=VIRGIN[1])
        assert self.flagged(db) == (False, False, [MarkKind.KNOWN_GAP])

        scope(db, months=12, account="")
        assert self.flagged(db) == (True, False, [MarkKind.KNOWN_GAP])

        scope(db, months=6, account="")
        assert self.flagged(db) == (False, False, [MarkKind.KNOWN_GAP])

        with Store(db) as store:
            remove_mark(store, made.id, "2026-10-06T00:00:00+00:00")
        assert self.flagged(db) == (False, True, [])

    def test_Scope_WhenAMarkLiesWhollyOutsideIt_TheMarkIsKeptAndStillRead(self, db):
        scope(db, months=6, account="")
        made = mark(db, account="main", source="starling-csv", kind="known-gap",
                    first_day=D(2026, 3, 1), last_day=D(2026, 3, 31))

        found = marks_read(db)
        report = report_with(db, found)

        assert made.id in {r.mark.id for r in found.readings}
        assert report.set_aside == ()
        assert [o.gap_kind for o in report.out_of_scope if o.account == "main"]
