"""Which statements and exports are still to fetch, as data.

Every answer is decided in `fetch_gaps_world`'s docstring, in dates, before the first run; this
file holds the household to them, with the opposite of each scenario beside it (the same account
with the file present), and holds the rules that make them (a cadence, a hole, what a statement
states of its own period) with answers worked out on paper.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from fetch_gaps_world import TODAY, build_household
from obdi.fetch_gaps import (
    Basis,
    GapKind,
    add_months,
    cadence_of,
    closings_after,
    fetch_report,
    gaps_for_account,
    gather_evidence,
)
from obdi.overview import standing_items_from, statement_awaited
from obdi.standing_data import standings_for
from obdi.statement_terms import keep_statement_readings, statement_periods
from obdi.store import Store

D = date


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return _load(tmp_path_factory.mktemp("gaps-original"), repaired=False)


@pytest.fixture(scope="module")
def repaired(tmp_path_factory):
    return _load(tmp_path_factory.mktemp("gaps-repaired"), repaired=True)


class Loaded:
    def __init__(self, db, house, standings, evidence) -> None:
        self.db, self.house, self.standings, self.evidence = db, house, standings, evidence
        self.report = fetch_report(evidence, standings, TODAY)

    def gaps(self, ref):
        return [g for o in self.report.accounts if o.account == ref for g in o.gaps]

    def outlook(self, ref):
        return next((o for o in self.report.accounts if o.account == ref), None)


def _load(root, *, repaired):
    db, house = build_household(root, repaired=repaired)
    with Store(db) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        ]
        standings = standings_for(store, refs, families=None, movement=None)
        return Loaded(db, house, standings, gather_evidence(store))


def shape(gap):
    return (gap.kind, gap.first_day, gap.last_day, gap.basis, gap.source)


class TestEveryKindOfGap:
    def test_Card_WhenNewestTwoStatementsAreMissing_NamesTheDaysAfterTheLastKnownBalance(
        self, world
    ):
        """Statements to 07-10, rows to 10-05: fetch from 07-11; two closings have passed
        (08-10, 09-10) and 10-10 has not."""
        (gap,) = world.gaps("card-behind")
        assert shape(gap) == (
            GapKind.NEWER_STATEMENT, D(2026, 7, 11), D(2026, 10, 5), Basis.STATED,
            "santander-cc-pdf",
        )
        assert gap.rows_to == D(2026, 10, 5)
        assert gap.probably == 2
        assert gap.closings == (D(2026, 8, 10), D(2026, 9, 10))

    def test_Card_WhenOnlyOneStatementIsHeld_InfersNoCadenceAndSaysHowManyAreWaitingNowhere(
        self, world
    ):
        (gap,) = world.gaps("card-late-one")
        assert shape(gap)[:4] == (
            GapKind.NEWER_STATEMENT, D(2026, 6, 11), D(2026, 8, 1), Basis.STATED
        )
        assert gap.probably is None and gap.closings == ()

    def test_Card_WhenAMonthlyStatementIsMissingInTheMiddle_InfersTheHoleFromTheCadence(
        self, world
    ):
        """03-10 to 05-10 is 61 days against a usual 31, so one is missing, closing 04-10."""
        (gap,) = world.gaps("card-hole")
        assert shape(gap) == (
            GapKind.HOLE_BETWEEN, D(2026, 3, 11), D(2026, 5, 9), Basis.INFERRED,
            "santander-cc-pdf",
        )
        assert gap.probably == 1 and gap.closings == (D(2026, 4, 10),)

    def test_Card_WhenAStatementStatesItsPeriodOpensLate_TheHoleIsStatedNotInferred(
        self, world
    ):
        (gap,) = world.gaps("card-virgin")
        assert shape(gap) == (
            GapKind.HOLE_BETWEEN, D(2026, 6, 5), D(2026, 7, 4), Basis.STATED,
            "virgin-money-cc-pdf",
        )
        assert gap.probably is None

    def test_Card_WhenRowsPrecedeItsFirstStatement_NamesTheDaysNothingTests(self, world):
        """Feed rows from 06-01; the first statement lists a row on 08-05, so the days before
        08-05 have no known balance before them."""
        (gap,) = world.gaps("card-early")
        assert shape(gap) == (
            GapKind.NOTHING_BEFORE, D(2026, 6, 1), D(2026, 8, 4), Basis.STATED,
            "santander-cc-pdf",
        )

    def test_Card_WhenOnlyOneStatementIsHeld_SaysOnlyOneKnownBalanceIsHeld(self, world):
        (gap,) = world.gaps("card-single")
        assert shape(gap)[:2] == (GapKind.ONE_BALANCE, D(2026, 9, 5))
        assert gap.last_day == D(2026, 9, 10)

    def test_Account_WhenNoFileAndNoBalanceIsHeld_SaysOnlyTheAutomaticSourcesFeedIt(self, world):
        (gap,) = world.gaps("card-feed-only")
        assert shape(gap) == (
            GapKind.AUTOMATIC_ONLY, D(2026, 8, 2), D(2026, 9, 15), Basis.STATED, ""
        )

    def test_Account_WhenAnExportIsHeldWithNoBalance_SaysNoKnownBalanceAndNamesTheExport(
        self, world
    ):
        (gap,) = world.gaps("card-qif")
        assert shape(gap) == (
            GapKind.NO_BALANCE, D(2026, 8, 3), D(2026, 8, 20), Basis.STATED, "qif"
        )

    def test_MainAccount_WhenItsExportStopsAndLacksAMonth_NamesBothAsRangesOfDays(self, world):
        stops, months = world.gaps("main")
        assert shape(stops) == (
            GapKind.EXPORT_STOPS, D(2026, 8, 5), D(2026, 10, 5), Basis.STATED, "starling-csv"
        )
        assert stops.rows_to == D(2026, 10, 1)
        assert shape(months) == (
            GapKind.EXPORT_MONTHS, D(2026, 3, 1), D(2026, 3, 31), Basis.STATED, "starling-csv"
        )

    def test_Account_WhenAReviewFlagLacksAKnownBalance_TheGapCarriesTheFlagsOwnWords(
        self, world
    ):
        kinds = [(g.kind, g.first_day, g.last_day) for g in world.gaps("opens-on-day")]
        assert kinds == [
            (GapKind.NOTHING_BEFORE, D(2026, 9, 3), D(2026, 9, 14)),
            (GapKind.FLAG_SETTLE, D(2026, 9, 14), D(2026, 9, 14)),
        ]
        flag = world.gaps("opens-on-day")[1]
        assert flag.flag == "before"
        assert "No known balance before 2026-09-14" in flag.why

    def test_Account_WhenItsFlagIsSettledByTheBalances_HasNoFlagGap(self, world):
        assert GapKind.FLAG_SETTLE not in {g.kind for g in world.gaps("between")}


class TestWhatNeedsNothing:
    def test_Card_WhenTheNewestStatementIsWithinAPeriod_IsQuietAndSaysWhenTheNextIsExpected(
        self, world
    ):
        quiet = world.outlook("card-quiet")
        assert quiet.gaps == ()
        assert quiet.next_expected == D(2026, 10, 10)

    def test_BalanceOnlyAccount_HasNoFileToFetch(self, world):
        hand = world.outlook("savings-hand")
        assert hand.balance_only and hand.gaps == ()

    def test_ArchivedAccount_IsNotListedAtAll(self, world):
        assert world.outlook("card-old") is None

    def test_Report_CountsEveryGapAndEveryAccountThatNeedsSomething(self, world):
        assert len(world.report.gaps) == 13
        assert len(world.report.needing) == 11
        assert [o.account for o in world.report.accounts if not o.gaps] == [
            "card-quiet", "savings-hand",
        ]

    def test_Report_ListsTheMostUrgentAccountsFirst(self, world):
        order = [o.account for o in world.report.needing]
        assert order[:2] == ["card-late-one", "card-behind"]
        assert order[2:4] == ["card-hole", "card-virgin"]
        assert order[4] == "main"

    def test_Report_WhenEveryGapIsFilled_NamesTheNextExpectedStatementInstead(self, repaired):
        for ref in ("card-behind", "card-hole", "card-virgin", "card-early", "main"):
            assert repaired.gaps(ref) == [], ref
        assert repaired.outlook("card-behind").next_expected == D(2026, 10, 10)
        assert repaired.outlook("card-hole").next_expected == D(2026, 10, 10)
        assert repaired.outlook("card-early").next_expected == D(2026, 10, 10)
        assert repaired.outlook("card-virgin").next_expected == D(2026, 11, 4)

    def test_Report_WhenOnlySomeAccountsAreRepaired_NextExpectedIsOnlyAmongThoseNeedingNothing(
        self, world, repaired
    ):
        assert world.report.next_expected == D(2026, 10, 10)
        assert repaired.report.next_expected is None or repaired.report.next_expected >= TODAY


class TestOneRuleWithToday:
    def test_Accounts_WhenAStatementIsOverdue_TodayAndThePageNameThem(self, world):
        """Today's item and this page's newer-statement gap read `statement_awaited`."""
        items = standing_items_from(world.standings, lambda ref: ref, lambda ref: False, TODAY)
        named = {ref for item in items if item.kind == "statement-due" for ref in item.accounts}
        on_page = {
            o.account
            for o in world.report.accounts
            if any(g.kind is GapKind.NEWER_STATEMENT for g in o.gaps)
        }
        assert named == on_page == {"card-behind", "card-late-one"}

    def test_Accounts_WhenAnyIsOverdue_TheSharedRuleAgreesForEveryAccountOverTheHousehold(
        self, world, repaired
    ):
        for loaded in (world, repaired):
            for ref, standing in loaded.standings.items():
                awaited = statement_awaited(standing, TODAY) is not None
                listed = any(g.kind is GapKind.NEWER_STATEMENT for g in loaded.gaps(ref))
                assert awaited == listed, ref


class TestRules:
    def test_Cadence_WhenFewerThanThreeStatements_IsNotInferred(self):
        assert cadence_of([D(2026, 1, 10), D(2026, 2, 10)]) is None

    def test_Cadence_WhenMonthly_IsTheLowerMedianInterval(self):
        """Intervals 31 and 28: the lower median is 28. (First written as 31, which was the
        longer interval, not the lower median the rule takes.)"""
        assert cadence_of([D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10)]) == 28
        assert cadence_of([D(2026, 3, 10), D(2026, 4, 10), D(2026, 5, 10)]) == 30

    def test_Cadence_WhenOneStatementIsMissing_IsStillAMonth(self):
        """Intervals 31, 61: the lower median is 31, so the hole does not lift the cadence."""
        assert cadence_of([D(2026, 1, 10), D(2026, 2, 10), D(2026, 4, 10)]) == 31

    def test_Cadence_WhenQuarterly_IsNotInferred(self):
        assert cadence_of([D(2026, 1, 10), D(2026, 4, 10), D(2026, 7, 10)]) is None

    def test_AddMonths_WhenTheDayDoesNotExist_KeepsToTheMonthsEnd(self):
        assert add_months(D(2026, 1, 31), 1) == D(2026, 2, 28)
        assert add_months(D(2026, 11, 30), 3) == D(2027, 2, 28)

    def test_ClosingsAfter_ListsOnlyThoseAlreadyPassed(self):
        assert closings_after(D(2026, 7, 10), D(2026, 10, 5)) == [D(2026, 8, 10), D(2026, 9, 10)]
        assert closings_after(D(2026, 9, 10), D(2026, 10, 5)) == []
        assert closings_after(D(2026, 9, 10), D(2026, 10, 10)) == [D(2026, 10, 10)]


class TestWhatAStatementStatesOfItsOwnPeriod:
    def test_Statements_WhichStateAPeriod_CarryItsFirstDay(self, world):
        with Store(world.db) as store:
            periods = {(p.account_ref, p.closing): p for p in statement_periods(store)}
        assert periods[("card-virgin", D(2026, 8, 4))].opens == D(2026, 7, 5)
        assert periods[("card-virgin", D(2026, 8, 4))].source == "virgin-money-cc-pdf"

    def test_Statements_WhichStateOnlyAClosingDay_CarryNoStart(self, world):
        with Store(world.db) as store:
            periods = {(p.account_ref, p.closing): p for p in statement_periods(store)}
        santander = periods[("card-hole", D(2026, 3, 10))]
        assert santander.opens is None
        assert santander.first_row == D(2026, 3, 5)
        assert santander.covers_from == D(2026, 3, 5)

    def test_KeptReading_WhenWrittenBeforePeriodsWereKept_IsReadAgainFromTheDocument(self, world):
        with Store(world.db) as store:
            digest, source, text = _virgin_reading(store)
            older = json.loads(text)
            del older["period_start"]
            store.keep_statement_reading(digest, source, json.dumps(older))
            store.connection.commit()

            read = keep_statement_readings(store)

            assert read >= 1
            assert '"period_start": "2026-' in store.stored_statement_reading(digest)[1]

    def test_KeptReading_WhenAlreadyHoldingAPeriod_IsNotReadAgain(self, world):
        with Store(world.db) as store:
            assert keep_statement_readings(store) == 0


def _virgin_reading(store):
    for period in statement_periods(store):
        if period.opens is not None:
            row = store.connection.execute(
                "SELECT digest FROM raw_artefacts WHERE account_ref = ? AND media_type = "
                "'application/pdf' LIMIT 1",
                (period.account_ref,),
            ).fetchone()
            stored = store.stored_statement_reading(row["digest"])
            return row["digest"], stored[0], stored[1]
    raise AssertionError("no statement states a period")


class TestOneAccountForAnotherPage:
    def test_GapsForAccount_ReturnsTheSameListAsTheReport(self, world):
        with Store(world.db) as store:
            found = gaps_for_account(
                store, "card-hole", TODAY, standings=world.standings, evidence=world.evidence
            )
        assert found == world.gaps("card-hole")

    def test_GapsForAccount_WhenTheAccountNeedsNothingOrIsUnknown_IsEmpty(self, world):
        with Store(world.db) as store:
            assert gaps_for_account(store, "card-quiet", TODAY) == []
            assert gaps_for_account(store, "no-such-account", TODAY) == []
