"""A decision about a period, weighed against what the store holds, over `fetch_marks_world`."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from fetch_gaps_world import TODAY, D, Household
from fetch_marks_world import (
    add_feed_rows_in_virgin_hole,
    april_statement,
    household,
    mark,
    marks_read,
    report_with,
)
from obdi.fetch_gaps import GapKind
from obdi.fetch_marks import (
    KINDS,
    MarkKind,
    MarkRefused,
    Scope,
    Standing,
    add_months,
    marks_for_account,
    remove_mark,
    scope_exit_day,
)
from obdi.rebuild import rebuild_from_raw
from obdi.store import SCHEMA_VERSION, Store

VIRGIN_HOLE = (D(2026, 6, 5), D(2026, 7, 4))
HOLE_NOTHING = (D(2026, 3, 11), D(2026, 4, 10))


@pytest.fixture
def db(tmp_path):
    return household(tmp_path)


def reading_of(db, mark_id):
    return next(r for r in marks_read(db).readings if r.mark.id == mark_id)


class TestNothingToFetchIsWeighedAgainstEveryRow:
    def test_Mark_WhenNoSourceListsARowInTheHole_IsSupportedAndTheChainJoins(self, db):
        made = mark(db, account="card-virgin", kind="nothing-to-fetch",
                    first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])

        reading = reading_of(db, made.id)

        assert reading.standing is Standing.SUPPORTED
        assert reading.evidence.rows == 0
        assert reading.evidence.chain is True

    def test_Mark_WhenTheStatementsEitherSideDoNotJoin_IsStillSupportedButSaysSo(self):
        from obdi.fetch_gaps import STATEMENT_SOURCES
        from obdi.fetch_marks import MarkWorld, StatementFact, gather_evidence, judge

        world = MarkWorld(statements={"a": (
            StatementFact(D(2026, 1, 31), D(2026, 1, 1), 100, 400, "p"),
            StatementFact(D(2026, 4, 30), D(2026, 4, 1), 500, 900, "p"),
        )})
        found = gather_evidence(world, "a", "", D(2026, 2, 1), D(2026, 3, 31),
                                statement_sources=STATEMENT_SOURCES)

        assert found.chain is False
        assert judge(MarkKind.NOTHING_TO_FETCH, "", D(2026, 2, 1), D(2026, 3, 31), found,
                     statement_sources=STATEMENT_SOURCES)[0] is Standing.SUPPORTED

    def test_Mark_WhenAnApril_StatementMissingFromAnInferredHoleIsMarked_TheChainJoins(self, db):
        made = mark(db, account="card-hole", kind="nothing-to-fetch",
                    first_day=HOLE_NOTHING[0], last_day=HOLE_NOTHING[1])

        reading = reading_of(db, made.id)

        assert reading.standing is Standing.SUPPORTED
        assert reading.evidence.chain is True

    def test_Mark_WhenTheFeedListsTwoRowsInTheHole_IsContradictedWithTheirCountAndDates(self, db):
        add_feed_rows_in_virgin_hole(db)
        made = mark(db, account="card-virgin", kind="nothing-to-fetch",
                    first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])

        reading = reading_of(db, made.id)

        assert reading.standing is Standing.CONTRADICTED
        assert reading.evidence.rows == 2
        assert (reading.evidence.first_row, reading.evidence.last_row) == (
            D(2026, 6, 20), D(2026, 7, 1))
        assert dict(reading.evidence.by_source) == {"truelayer-booked": 2}

    def test_Mark_WhenARowComesLater_IsReadAsChangedSinceItWasMade(self, db):
        made = mark(db, account="card-virgin", kind="nothing-to-fetch",
                    first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])
        assert reading_of(db, made.id).changed is False

        add_feed_rows_in_virgin_hole(db)

        assert reading_of(db, made.id).changed is True

    def test_Mark_WhenTheMissingStatementIsImportedAfterAll_IsSatisfiedNotContradicted(
        self, db, tmp_path
    ):
        made = mark(db, account="card-hole", kind="nothing-to-fetch",
                    first_day=HOLE_NOTHING[0], last_day=HOLE_NOTHING[1])

        april_statement(db, tmp_path, Household())

        reading = reading_of(db, made.id)
        assert reading.standing is Standing.SATISFIED
        assert not reading.hides


class TestBeforeTheSourcesHistory:
    def test_Offer_WhenAnAskReachingBackCameBackEmpty_IsReadyMadeUpToTheDayBeforeTheRows(self, db):
        offers = marks_read(db).offers

        assert [(o.account, o.source, o.last_day) for o in offers] == [
            ("main", "truelayer", D(2026, 3, 14))]

    def test_Offer_WhenNothingWasEverAskedFurtherBack_IsNotMadeAndSaysNotAsked(self, db):
        found = marks_read(db)

        assert "card-behind" not in {o.account for o in found.offers}
        assert ("card-behind", D(2026, 8, 2)) in {(n.account, n.first_row) for n in found.not_asked}

    def test_Mark_WhenAcceptedFromTheOffer_IsSupportedByTheEmptyAsk(self, db):
        made = mark(db, account="main", source="truelayer", kind="before-history",
                    last_day=D(2026, 3, 14), origin="source")

        reading = reading_of(db, made.id)

        assert reading.standing is Standing.SUPPORTED
        assert reading.reason == "asked-empty"
        assert reading.mark.origin == "source"
        assert marks_read(db).offers == (), "an offer already taken is not made again"

    def test_Mark_WhenTheAggregatorWasNeverAskedThatFarBack_IsUntestedNotSupported(self, db):
        made = mark(db, account="card-behind", source="truelayer", kind="before-history",
                    last_day=D(2026, 8, 1))

        reading = reading_of(db, made.id)

        assert reading.standing is Standing.UNTESTED
        assert reading.reason == "not-asked"

    def test_Mark_WhenALaterResponseBringsAnEarlierRow_IsContradicted(self, db):
        from fetch_gaps_world import feed

        made = mark(db, account="main", source="truelayer", kind="before-history",
                    last_day=D(2026, 3, 14), origin="source")
        with Store(db) as store:
            feed(store, "main", [(D(2026, 2, 20), -777, "Earlier Zeppelin")], digest="main-early")

        reading = reading_of(db, made.id)
        assert reading.standing is Standing.CONTRADICTED
        assert reading.evidence.own_rows == 1


class TestNotOpen:
    def test_Mark_WhenAfterTheDeclaredClosingDay_IsSupportedByTheDeclaredDates(self, db):
        with Store(db) as store:
            from obdi.accounts import AccountRecord, AccountRef

            store.declare_account(AccountRecord(
                ref=AccountRef("card-quiet"), label="Quiet card", closed=D(2026, 3, 1)))
        made = mark(db, account="card-quiet", kind="not-open",
                    first_day=D(2026, 3, 2), last_day=D(2026, 3, 20))

        assert reading_of(db, made.id).standing is Standing.SUPPORTED

    def test_Mark_WhenTheAccountHoldsRowsInThePeriod_IsContradicted(self, db):
        made = mark(db, account="main", kind="not-open",
                    first_day=D(2026, 1, 1), last_day=D(2026, 1, 31))

        assert reading_of(db, made.id).standing is Standing.CONTRADICTED

    def test_Mark_WhenNoDatesAreDeclaredAndRowsLieOnBothSides_IsUntested(self, db):
        made = mark(db, account="main", kind="not-open",
                    first_day=D(2026, 3, 1), last_day=D(2026, 3, 10))

        assert reading_of(db, made.id).standing is Standing.UNTESTED


class TestKnownGapAssertsNothing:
    def test_Mark_OverAPeriodTheStoreHoldsRowsFor_IsNeverContradicted(self, db):
        made = mark(db, account="main", kind="known-gap",
                    first_day=D(2026, 1, 1), last_day=D(2026, 1, 31))

        assert reading_of(db, made.id).standing is Standing.UNTESTED

    def test_Kinds_EveryKindSaysWhatItAssertsAndOnlyClaimsAreWeighed(self):
        assert set(KINDS) == set(MarkKind)
        for meaning in KINDS.values():
            assert len(meaning.asserts.split()) >= 6
        assert KINDS[MarkKind.KNOWN_GAP].claims is False
        assert KINDS[MarkKind.OTHER].claims is False
        assert KINDS[MarkKind.NOTHING_TO_FETCH].data_missing is False
        assert KINDS[MarkKind.NO_LONGER_PROVIDED].data_missing is True


class TestWhatMakingAMarkRefuses:
    def refused(self, db, **fields) -> str:
        with pytest.raises(MarkRefused) as raised:
            mark(db, **fields)
        return str(raised.value)

    def test_Mark_WhenDatesAreOutOfOrder_IsRefused(self, db):
        said = self.refused(db, account="main", kind="known-gap",
                            first_day=D(2026, 3, 5), last_day=D(2026, 3, 1))
        assert "first day is after the last day" in said

    def test_Mark_WhenItEndsAfterToday_IsRefusedBecauseAnOpenFutureSilencesForEver(self, db):
        said = self.refused(db, account="main", kind="known-gap", last_day=D(2026, 10, 6))
        assert "has not ended" in said

    def test_Mark_WhenTheKindIsNotOnTheList_IsRefused(self, db):
        assert "kinds of mark" in self.refused(
            db, account="main", kind="whatever", last_day=D(2026, 3, 1))

    def test_Mark_WhenTheKindIsOtherWithoutANote_IsRefusedAndWithOneIsTaken(self, db):
        assert "needs a note" in self.refused(db, account="main", kind="other",
                                              last_day=D(2026, 3, 1))
        assert mark(db, account="main", kind="other", last_day=D(2026, 3, 1),
                    note="the bank closed its archive").note

    def test_Mark_WhenTheAccountIsUnknown_IsRefused(self, db):
        assert "not an account" in self.refused(db, account="nobody", kind="known-gap",
                                                last_day=D(2026, 3, 1))

    def test_Mark_WhenTheSourceDoesNotFeedTheAccount_IsRefused(self, db):
        assert "not a source" in self.refused(db, account="main", source="no-such-source",
                                              kind="known-gap", last_day=D(2026, 3, 1))

    def test_Mark_WhenTheNoteIsTooLong_IsRefused(self, db):
        assert "longer than" in self.refused(db, account="main", kind="known-gap",
                                             last_day=D(2026, 3, 1), note="x" * 400)

    def test_Mark_WhenALookAgainDayIsGivenForAnotherKindOrIsPast_IsRefused(self, db):
        assert "known gap" in self.refused(db, account="main", kind="other", note="n",
                                           last_day=D(2026, 3, 1), review_on=D(2027, 1, 1))
        assert "after today" in self.refused(db, account="main", kind="known-gap",
                                             last_day=D(2026, 3, 1), review_on=TODAY)

    def test_Mark_WhenItOverlapsAnotherOfTheSameAccountAndSource_IsRefusedAndTheFirstKept(self, db):
        first = mark(db, account="main", kind="known-gap",
                     first_day=D(2026, 3, 1), last_day=D(2026, 3, 31))

        said = self.refused(db, account="main", kind="other", note="n",
                            first_day=D(2026, 3, 31), last_day=D(2026, 4, 5))

        assert "Remove that one first" in said
        assert [r.mark.id for r in marks_read(db).readings] == [first.id]

    def test_Mark_WhenItOnlyTouchesAnotherOrNamesAnotherSource_IsTaken(self, db):
        mark(db, account="main", kind="known-gap", first_day=D(2026, 3, 1), last_day=D(2026, 3, 31))
        mark(db, account="main", kind="known-gap", first_day=D(2026, 4, 1), last_day=D(2026, 4, 5))
        mark(db, account="main", source="starling-csv", kind="known-gap",
             first_day=D(2026, 3, 1), last_day=D(2026, 3, 31))

        assert len(marks_read(db).readings) == 3


class TestASpaceHasNothingToSetAside:
    def test_Mark_WhenTheAccountIsASpaceOfAParent_IsRefusedAndTheParentIsNot(self, tmp_path):
        from obdi.fetch_gaps import STATEMENT_SOURCES
        from obdi.fetch_marks import MarkWorld, make_mark

        world = MarkWorld(
            declared={"space": (None, None), "parent": (None, None)},
            spaces=frozenset({"space"}),
        )
        with Store(tmp_path / "spaces.sqlite3") as store:
            def attempt(account):
                return make_mark(
                    store, world, account=account, source="", kind="known-gap", first_day=None,
                    last_day=D(2026, 3, 1), note="", review_on=None, origin="owner",
                    now="2026-10-05T00:00:00+00:00", today=TODAY,
                    statement_sources=STATEMENT_SOURCES,
                )

            with pytest.raises(MarkRefused, match="A Space has no statement"):
                attempt("space")
            assert attempt("parent").account == "parent"


class TestMarksAreKeptAndUndone:
    def test_Remove_WhenAMarkIsUndone_TheGapReturnsAndTheHistoryKeepsBoth(self, db):
        made = mark(db, account="card-virgin", kind="nothing-to-fetch",
                    first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])
        assert not _virgin_gaps(report_with(db, marks_read(db)))

        with Store(db) as store:
            remove_mark(store, made.id, "2026-10-06T09:00:00+00:00")

        assert [g.kind for g in _virgin_gaps(report_with(db, marks_read(db)))] == [
            GapKind.HOLE_BETWEEN]
        with Store(db) as store:
            (row,) = store.fetch_mark_rows(including_removed=True)
            assert (row["made_at"], row["removed_at"]) == (
                "2026-10-05T09:00:00+00:00", "2026-10-06T09:00:00+00:00")

    def test_Remove_WhenAlreadyRemoved_IsRefused(self, db):
        made = mark(db, account="main", kind="known-gap", last_day=D(2026, 3, 1))
        with Store(db) as store:
            remove_mark(store, made.id, NOW_LATER)
            with pytest.raises(MarkRefused):
                remove_mark(store, made.id, NOW_LATER)

    def test_Rebuild_WhenTheDerivedLayerIsReplayed_EveryMarkSurvivesAndEvidenceIsRecomputed(
        self, db
    ):
        add_feed_rows_in_virgin_hole(db)
        contradicted = mark(db, account="card-virgin", kind="nothing-to-fetch",
                            first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])
        acknowledged = mark(db, account="main", kind="known-gap", last_day=D(2026, 1, 1),
                            note="kept")
        with Store(db) as store:
            before = marks_read(db)
            report = rebuild_from_raw(store)
            assert report.problems == []

        after = marks_read(db)
        assert [r.mark for r in after.readings] == [r.mark for r in before.readings]
        assert {r.mark.id for r in after.readings} == {contradicted.id, acknowledged.id}
        # The feed rows were handed over without a stored original, so the replay does not hold
        # them and the same mark now reads as it would over what is held.
        assert reading_after(after, contradicted.id).evidence.rows == 0


class TestAMarkOnAnArchivedAccount:
    def test_Mark_WhenTheAccountIsArchived_IsKeptAndReadWithoutAGapToHide(self, db):
        made = mark(db, account="card-old", kind="known-gap", last_day=D(2026, 1, 1))

        assert reading_of(db, made.id).standing is Standing.UNTESTED
        assert report_with(db, marks_read(db)).set_aside == ()


class TestWhatTheTimelineReads:
    def test_MarksForAccount_ReturnsKindSourceDatesStandingAndTexture(self, db):
        mark(db, account="card-virgin", kind="nothing-to-fetch",
             first_day=VIRGIN_HOLE[0], last_day=VIRGIN_HOLE[1])
        mark(db, account="main", kind="known-gap", last_day=D(2026, 1, 1))

        with Store(db) as store:
            found = marks_for_account(store, "card-virgin", TODAY)

        assert [(m.kind, m.first_day, m.last_day, m.standing) for m in found] == [
            (MarkKind.NOTHING_TO_FETCH, VIRGIN_HOLE[0], VIRGIN_HOLE[1], Standing.SUPPORTED)]
        assert found[0].texture == KINDS[MarkKind.NOTHING_TO_FETCH].texture


class TestAStoreAtTheVersionBefore:
    def test_Store_OpenedAtSchemaEighteen_GainsTheMarkTablesWithoutLosingAnything(self, tmp_path):
        path = tmp_path / "eighteen.sqlite3"
        with Store(path):
            pass
        connection = sqlite3.connect(path)
        connection.execute("DROP TABLE fetch_marks")
        connection.execute("DROP TABLE record_scopes")
        connection.execute("UPDATE obdi_meta SET value = '18' WHERE key = 'schema_version'")
        connection.commit()
        connection.close()

        with Store(path) as reopened:
            assert SCHEMA_VERSION == 19
            before = reopened.standing_epoch()
            reopened.set_record_scope("x", first_day="2024-01-01", months=None, at="now")
            assert reopened.standing_epoch() > before, "a scope moves what pages say"
            assert reopened.fetch_mark_rows() == []


class TestScope:
    def test_Scope_WhenRolling_StartsMonthsBackFromTodayAndMovesWithIt(self):
        scope = Scope("main", None, 24, "t")

        assert scope.starts(D(2026, 10, 5)) == D(2024, 10, 5)
        assert scope.starts(D(2026, 11, 1)) == D(2024, 11, 1)
        assert scope.describe(TODAY) == "from 2024-10-05 (the last 24 months)"

    def test_Scope_WhenFixed_DoesNotMove(self):
        scope = Scope("main", D(2025, 1, 1), None, "t")

        assert scope.starts(D(2026, 10, 5)) == scope.starts(D(2030, 1, 1)) == D(2025, 1, 1)

    def test_ScopeExit_IsTheFirstDayTheRollingScopeNoLongerReachesTheGap(self):
        exit_day = scope_exit_day(D(2024, 10, 31), 24)

        assert exit_day == D(2026, 11, 1)
        assert add_months(exit_day, -24) > D(2024, 10, 31) >= add_months(
            exit_day - (date(2026, 11, 2) - date(2026, 11, 1)), -24)


NOW_LATER = "2026-10-06T09:00:00+00:00"


def reading_after(found, mark_id):
    return next(r for r in found.readings if r.mark.id == mark_id)


def _virgin_gaps(report):
    return [g for g in report.gaps if g.account == "card-virgin"]
