"""Which days a statement covers, and how each end of that is known.

THE HAND-WORKED ACCOUNT "five-and-a-half" (Santander, a layout that here prints no start day;
every statement received the day after it closed, today 2026-06-20). Amounts are owed, in
pounds; each statement lists one spend.

  S1  closes 01-10  opens 100  spend 20 on 01-04  closes 120   no predecessor
  S2  closes 02-10  opens 120  spend 30 on 02-03  closes 150   opens on S1's closing
  S3  closes 03-10  (NEVER LANDED)  opens 150, closes 160
  S4  closes 04-10  opens 160  spend 40 on 04-02  closes 200   opens on S3's closing, not S2's
  S5  closes 05-10  opens 200  spend 25 on 05-06  closes 225   opens on S4's closing
  S6  closes 06-10  opens 225  spend 5 on 05-28   closes 230   received 06-02, mid-period

Closing days 01-10, 02-10, 04-10, 05-10, 06-10 are 31, 59, 30, 31 days apart; sorted
30, 31, 31, 59, the lower median is the second, 31, so the cadence is 31 and a hole is an
interval over 31 x 1.5 = 46.5 days.

  S1  first 01-04 OBSERVED (its own first row)   last 01-10 STATED   whole
  S2  opening equals S1's closing, 31 days on, so it begins 01-11, BALANCES_MEET (an inference)
  S4  opening 160 is not S2's closing 150: STATED hole from S2's close + 1 (02-11) to the day
      before S4's first row (04-02, no start being printed): 02-11 to 04-01; S4 first 04-02
      OBSERVED
  S5  opening equals S4's closing, 30 days on: first 04-11 BALANCES_MEET
  S6  opening equals S5's closing, 31 days on: first 05-11 BALANCES_MEET. Received 06-02, before
      its closing 06-10, so PARTIAL: last 06-02, bounded by RECEIVED, INFERRED.

  Next: the newest closing 06-10, so the next closes 07-10; the shortest lag over S1 to S5 is
  1 day (S6 was received before it closed and says nothing), so it is available 07-11, and
  nothing is due on 06-20.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from obdi.statement_span import (
    HOLE_CADENCES,
    Bound,
    Contradiction,
    HoleReason,
    Known,
    OtherSources,
    add_months,
    cadence_of,
    describe_account,
    due_closings,
    months_between,
    statement_spans,
)
from obdi.statement_terms import StatementPeriod, keep_statement_readings
from obdi.store import Store
from statement_span_world import Spend, feed, statement

D = date
TODAY = D(2026, 6, 20)


def _next_day(day: date) -> date:
    return day + timedelta(days=1)


def _spans(root, build):
    with Store(root / "store.sqlite3") as store:
        build(store)
        keep_statement_readings(store)
        store.connection.commit()
        return statement_spans(store, TODAY)


def _five_and_a_half(store, root, ref="five"):
    s1 = statement(
        store,
        root,
        ref,
        D(2026, 1, 10),
        10000,
        [Spend(D(2026, 1, 4), "Aaa Shop", 2000)],
        received=D(2026, 1, 11),
    )
    s2 = statement(
        store,
        root,
        ref,
        D(2026, 2, 10),
        s1,
        [Spend(D(2026, 2, 3), "Bbb Shop", 3000)],
        received=D(2026, 2, 11),
    )
    s3 = s2 + 1000  # the missing statement's movement, never landed
    s4 = statement(
        store,
        root,
        ref,
        D(2026, 4, 10),
        s3,
        [Spend(D(2026, 4, 2), "Ccc Shop", 4000)],
        received=D(2026, 4, 11),
    )
    s5 = statement(
        store,
        root,
        ref,
        D(2026, 5, 10),
        s4,
        [Spend(D(2026, 5, 6), "Ddd Shop", 2500)],
        received=D(2026, 5, 11),
    )
    statement(
        store,
        root,
        ref,
        D(2026, 6, 10),
        s5,
        [Spend(D(2026, 5, 28), "Eee Shop", 500)],
        received=D(2026, 6, 2),
    )


@pytest.fixture(scope="module")
def account(tmp_path_factory):
    root = tmp_path_factory.mktemp("five")
    return _spans(root, lambda store: _five_and_a_half(store, root))["five"]


class TestTheHandWorkedAccount:
    def test_FirstStatement_WithNoPredecessor_BeginsAtItsFirstRow_Observed(self, account):
        first = account.statements[0]
        assert (first.first, first.first_known) == (D(2026, 1, 4), Known.OBSERVED)
        assert (first.last, first.last_known, first.complete) == (
            D(2026, 1, 10),
            Known.STATED,
            True,
        )

    def test_SecondStatement_OpeningEqualToTheFirstsClosing_BeginsTheDayAfter_ByInference(
        self, account
    ):
        second = account.statements[1]
        assert (second.first, second.first_known) == (D(2026, 1, 11), Known.BALANCES_MEET)
        assert second.others is OtherSources.NOT_COVERED

    def test_FourthStatement_OpeningNotTheSecondsClosing_IsAStatedHoleToItsFirstRow(self, account):
        (hole,) = account.holes
        assert (hole.first_day, hole.last_day) == (D(2026, 2, 11), D(2026, 4, 1))
        assert (hole.known, hole.reason) == (Known.STATED, HoleReason.BALANCES_DIFFER)
        fourth = account.statements[2]
        assert (fourth.first, fourth.first_known) == (D(2026, 4, 2), Known.OBSERVED)

    def test_FifthStatement_ChainsToTheFourth(self, account):
        fifth = account.statements[3]
        assert (fifth.first, fifth.first_known) == (D(2026, 4, 11), Known.BALANCES_MEET)

    def test_SixthStatement_ReceivedBeforeItsClosing_IsPartialToTheDayReceived(self, account):
        sixth = account.statements[4]
        assert (sixth.first, sixth.first_known) == (D(2026, 5, 11), Known.BALANCES_MEET)
        assert (sixth.last, sixth.last_known) == (D(2026, 6, 2), Known.INFERRED)
        assert (sixth.complete, sixth.bounded_by) == (False, Bound.RECEIVED)

    def test_EarlierStatements_ReceivedAfterTheyClosed_AreWhole(self, account):
        assert [s.complete for s in account.statements[:4]] == [True] * 4
        assert account.cadence == 31

    def test_NextStatement_IsExpectedAtTheCadence_AvailableAfterTheShortestLag_AndNotDueYet(
        self, account
    ):
        following = account.next
        assert following is not None
        assert following.expected_close == D(2026, 7, 10)
        assert (following.lag_days, following.expected_available) == (1, D(2026, 7, 11))
        assert following.due == ()


class TestWhenTheBalanceReturnsToTheSameFigure:
    """Three held statements and a fourth, where the balance comes back to 100 (a month of
    money in and out). Closings 01-10, 02-10, 03-10, 04-10 each open on the one before; the
    balances at 01-10, 02-10 and 03-10 are all 100.

    With 02-10 held, 03-10 is compared with 02-10 (the nearest earlier held statement), and they
    meet. With 02-10 not held, 03-10 is compared with 01-10, which closed on the same figure
    but two periods before: equality alone must not make them contiguous."""

    def _build(self, store, root, *, hold_february):
        statement(
            store,
            root,
            "ret",
            D(2025, 12, 10),
            9000,
            [Spend(D(2025, 12, 4), "Ret A", 1000)],
            received=D(2025, 12, 11),
        )
        statement(
            store,
            root,
            "ret",
            D(2026, 1, 10),
            10000,
            [Spend(D(2026, 1, 4), "Ret B", 500), Spend(D(2026, 1, 5), "Ret B2", -500)],
            received=D(2026, 1, 11),
        )
        if hold_february:
            statement(
                store,
                root,
                "ret",
                D(2026, 2, 10),
                10000,
                [Spend(D(2026, 2, 4), "Ret C", 700), Spend(D(2026, 2, 6), "Ret C2", -700)],
                received=D(2026, 2, 11),
            )
        statement(
            store,
            root,
            "ret",
            D(2026, 3, 10),
            10000,
            [Spend(D(2026, 3, 4), "Ret D", 100)],
            received=D(2026, 3, 11),
        )

    def test_MarchOpening_EqualToTheNearestHeldClosing_OnePeriodOn_Meets(self, tmp_path):
        spans = _spans(tmp_path, lambda s: self._build(s, tmp_path, hold_february=True))["ret"]

        march = spans.statements[-1]
        assert (march.first, march.first_known) == (D(2026, 2, 11), Known.BALANCES_MEET)
        assert spans.holes == ()

    def test_MarchOpening_EqualToAClosingTwoPeriodsBack_IsAProbableNetNilHoleNotAMeeting(
        self, tmp_path
    ):
        spans = _spans(tmp_path, lambda s: self._build(s, tmp_path, hold_february=False))["ret"]

        (hole,) = spans.holes
        assert (hole.first_day, hole.last_day) == (D(2026, 1, 11), D(2026, 3, 9))
        assert (hole.known, hole.reason) == (Known.INFERRED, HoleReason.BALANCES_MEET_NET_NIL)
        assert (hole.probably, hole.closings) == (1, (D(2026, 2, 10),))
        march = spans.statements[-1]
        assert march.first_known is Known.OBSERVED


class TestAMissingStatementThatNetsToNil:
    """Closings 11-10, 12-10, 01-10 held (cadence 30 or 31: intervals 30 and 31, lower median
    30). The statement that closed 02-10 is missing: money in on 01-13 and out on 01-15, so
    the next held statement opens on the figure 01-10 closed on.

    FAR: the next held closes 03-10, 59 days after 01-10, two periods. Balances meet, so the
    hole is a probable net-nil one (inferred) - until the bank's feed holds the two rows no
    statement lists, when it is stated, with the count.

    NEAR: an interim statement (01-11 to 01-20) is missing and the next held closes 02-10, 31
    days after 01-10. Nothing in the documents tells that from nothing missing: reported as
    meeting, by inference, with no other source covering the days."""

    def _held(self, store, root, ref):
        statement(
            store,
            root,
            ref,
            D(2025, 11, 10),
            6000,
            [Spend(D(2025, 11, 4), f"{ref} A", 1000)],
            received=D(2025, 11, 11),
        )
        statement(
            store,
            root,
            ref,
            D(2025, 12, 10),
            7000,
            [Spend(D(2025, 12, 4), f"{ref} B", 1000)],
            received=D(2025, 12, 11),
        )
        return statement(
            store,
            root,
            ref,
            D(2026, 1, 10),
            8000,
            [Spend(D(2026, 1, 4), f"{ref} C", 2000)],
            received=D(2026, 1, 11),
        )

    def _far(self, store, root, *, with_feed):
        owed = self._held(store, root, "far")
        statement(
            store,
            root,
            "far",
            D(2026, 3, 10),
            owed,
            [Spend(D(2026, 3, 2), "far D", 1000)],
            received=D(2026, 3, 11),
        )
        if with_feed:
            feed(
                store,
                "far",
                [
                    Spend(D(2026, 1, 13), "Money in far", -5000),
                    Spend(D(2026, 1, 15), "Money out far", 5000),
                ],
                digest="far-feed",
            )

    def test_FarApart_WithNothingElseHeld_IsAProbableNilHole_Inferred(self, tmp_path):
        spans = _spans(tmp_path, lambda s: self._far(s, tmp_path, with_feed=False))["far"]

        (hole,) = spans.holes
        assert (hole.known, hole.reason, hole.probably) == (
            Known.INFERRED,
            HoleReason.BALANCES_MEET_NET_NIL,
            1,
        )
        assert (hole.first_day, hole.last_day) == (D(2026, 1, 11), D(2026, 3, 9))
        assert hole.unlisted_rows == 0

    def test_FarApart_WhenTheFeedHoldsTheTwoRowsNoStatementLists_IsAStatedHoleWithTheirCount(
        self, tmp_path
    ):
        spans = _spans(tmp_path, lambda s: self._far(s, tmp_path, with_feed=True))["far"]

        (hole,) = spans.holes
        assert (hole.known, hole.reason, hole.unlisted_rows) == (
            Known.STATED,
            HoleReason.UNLISTED_ROWS,
            2,
        )
        assert hole.probably == 1

    def _near(self, store, root, *, with_feed):
        owed = self._held(store, root, "near")
        statement(
            store,
            root,
            "near",
            D(2026, 2, 10),
            owed,
            [Spend(D(2026, 2, 8), "near D", 1000)],
            received=D(2026, 2, 11),
        )
        if with_feed:
            feed(
                store,
                "near",
                [
                    Spend(D(2026, 1, 13), "Money in near", -5000),
                    Spend(D(2026, 1, 15), "Money out near", 5000),
                ],
                digest="near-feed",
            )

    def test_ShortSpan_LooksOnePeriodApart_AndIsReportedAsMeetingByInference(self, tmp_path):
        spans = _spans(tmp_path, lambda s: self._near(s, tmp_path, with_feed=False))["near"]

        last = spans.statements[-1]
        assert (last.first, last.first_known) == (D(2026, 1, 11), Known.BALANCES_MEET)
        assert last.others is OtherSources.NOT_COVERED
        assert spans.holes == ()

    def test_ShortSpan_WhenTheFeedHoldsRowsNoStatementLists_IsAStatedHole(self, tmp_path):
        spans = _spans(tmp_path, lambda s: self._near(s, tmp_path, with_feed=True))["near"]

        (hole,) = spans.holes
        assert (hole.known, hole.reason, hole.unlisted_rows) == (
            Known.STATED,
            HoleReason.UNLISTED_ROWS,
            2,
        )
        assert (hole.first_day, hole.last_day) == (D(2026, 1, 11), D(2026, 2, 7))

    def test_ShortSpan_WhenTheFeedCoversTheDaysAndListsNothingExtra_SupportsMeetingWithoutProvingIt(
        self, tmp_path
    ):
        def build(store):
            owed = self._held(store, tmp_path, "cov")
            statement(
                store,
                tmp_path,
                "cov",
                D(2026, 2, 10),
                owed,
                [Spend(D(2026, 2, 8), "cov D", 1000)],
                received=D(2026, 2, 11),
            )
            feed(
                store,
                "cov",
                [Spend(D(2026, 1, 4), "cov C", 2000), Spend(D(2026, 2, 8), "cov D", 1000)],
                digest="cov-feed",
            )

        spans = _spans(tmp_path, build)["cov"]

        last = spans.statements[-1]
        assert (last.first, last.first_known) == (D(2026, 1, 11), Known.BALANCES_MEET)
        assert last.others is OtherSources.COVERED_NONE_UNLISTED
        assert spans.holes == ()


class TestAStatementThatStatesItsStart:
    def _period(self, closing, *, opens=None, opening=None, closing_minor=None, rows=()):
        return StatementPeriod(
            "acc",
            closing,
            opens,
            "virgin-money-cc-pdf",
            min(rows, default=None),
            max(rows, default=None),
            opening,
            closing_minor,
        )

    def test_StartAfterThePreviousClose_IsAStatedHoleOfTheDaysBetween(self):
        earlier = self._period(D(2026, 5, 4), opens=D(2026, 4, 5), opening=100, closing_minor=200)
        later = self._period(D(2026, 8, 4), opens=D(2026, 7, 5), opening=200, closing_minor=300)

        spans = describe_account([earlier, later], TODAY)

        (hole,) = spans.holes
        assert (hole.first_day, hole.last_day) == (D(2026, 5, 5), D(2026, 7, 4))
        assert (hole.known, hole.reason) == (Known.STATED, HoleReason.STARTS_AFTER)

    def test_StartTheDayAfterThePreviousClose_LeavesNoHole_AndIsStated(self):
        earlier = self._period(D(2026, 5, 4), opens=D(2026, 4, 5), opening=100, closing_minor=200)
        later = self._period(D(2026, 6, 4), opens=D(2026, 5, 5), opening=200, closing_minor=300)

        spans = describe_account([earlier, later], TODAY)

        assert spans.holes == ()
        assert (spans.statements[1].first, spans.statements[1].first_known) == (
            D(2026, 5, 5),
            Known.STATED,
        )

    def test_StartTheDayAfterThePreviousClose_ButAnOpeningThatIsNotItsClosing_IsABalancesBreak(
        self,
    ):
        earlier = self._period(D(2026, 5, 4), opens=D(2026, 4, 5), opening=100, closing_minor=200)
        later = self._period(D(2026, 6, 4), opens=D(2026, 5, 5), opening=250, closing_minor=300)

        spans = describe_account([earlier, later], TODAY)

        assert spans.holes == ()
        assert spans.statements[1].contradictions == (Contradiction.BALANCES_BREAK,)

    def test_StartBeforeThePreviousClose_IsAnOverlapContradiction(self):
        earlier = self._period(D(2026, 5, 4), opens=D(2026, 4, 5), opening=100, closing_minor=200)
        later = self._period(D(2026, 6, 4), opens=D(2026, 5, 1), opening=200, closing_minor=300)

        spans = describe_account([earlier, later], TODAY)

        assert Contradiction.OVERLAPS_PREVIOUS in spans.statements[1].contradictions

    def test_RowsOutsideTheStatedPeriod_AreReportedAsContradictions(self):
        item = self._period(
            D(2026, 5, 4),
            opens=D(2026, 4, 5),
            opening=100,
            closing_minor=200,
            rows=(D(2026, 4, 1), D(2026, 5, 9)),
        )

        (span,) = describe_account([item], TODAY).statements

        assert span.contradictions == (Contradiction.ROW_BEFORE_START, Contradiction.ROW_AFTER_END)

    def test_RowsInsideTheStatedPeriod_ReportNoContradiction(self):
        item = self._period(
            D(2026, 5, 4),
            opens=D(2026, 4, 5),
            opening=100,
            closing_minor=200,
            rows=(D(2026, 4, 5), D(2026, 5, 4)),
        )

        (span,) = describe_account([item], TODAY).statements

        assert span.contradictions == ()


class TestAStatementReadBeforeItsFormatWasCurrent:
    """A kept reading without the start its layout now prints must give the weaker answer, never
    a wrong one; after the next keep pass, the stronger. The Santander statement here prints
    "Previous balance as at 01-10" and so begins 01-11."""

    def test_Statement_BeforeItsReadingIsKeptAgain_IsOnlyObserved_AfterwardsStated(self, tmp_path):
        import json

        with Store(tmp_path / "s.sqlite3") as store:
            statement(
                store,
                tmp_path,
                "fmt",
                D(2026, 2, 10),
                12000,
                [Spend(D(2026, 2, 3), "Fmt A", 3000)],
                received=D(2026, 2, 11),
                previous_close=D(2026, 1, 10),
            )
            keep_statement_readings(store)
            store.connection.commit()
            digest = store.connection.execute("SELECT digest FROM raw_artefacts").fetchone()[
                "digest"
            ]
            source, text = store.stored_statement_reading(digest)
            old = json.loads(text)
            old["period_start"] = None
            del old["format"], old["produced"]
            store.keep_statement_reading(digest, source, json.dumps(old))
            store.connection.commit()

            before = statement_spans(store, TODAY)["fmt"].statements[0]
            keep_statement_readings(store)
            after = statement_spans(store, TODAY)["fmt"].statements[0]

        assert (before.first, before.first_known) == (D(2026, 2, 3), Known.OBSERVED)
        assert (after.first, after.first_known) == (D(2026, 1, 11), Known.STATED)


def _closings(*days: date) -> list[StatementPeriod]:
    """Statements that state a closing day and no balance, so only spacing speaks."""
    return [StatementPeriod("acc", day, None, "x", None, None) for day in days]


class TestTheCadence:
    @pytest.mark.parametrize("gap", [28, 29, 30, 31])
    def test_ThreeStatementsAGivenDaysApart_ImplyThatCadence(self, gap):
        base = D(2026, 1, 1)
        days = [base + timedelta(days=gap * n) for n in range(3)]

        assert cadence_of(days) == gap

    @pytest.mark.parametrize("gap", [20, 40, 90])
    def test_ThreeStatementsAtANonMonthlyRhythm_ImplyNoCadence(self, gap):
        base = D(2026, 1, 1)

        assert cadence_of([base + timedelta(days=gap * n) for n in range(3)]) is None

    def test_TwoStatements_ImplyNoCadence_SoNoHoleOrDueStatementIsInferred(self):
        spans = describe_account(_closings(D(2026, 1, 10), D(2026, 5, 10)), D(2026, 12, 1))

        assert spans.cadence is None
        assert spans.holes == ()
        assert spans.next is None

    def test_ThreeStatements_ImplyACadence_AndASpacingHoleIsInferred(self):
        spans = describe_account(
            _closings(D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 5, 10)), D(2026, 6, 1)
        )

        (hole,) = spans.holes
        assert (hole.known, hole.reason, hole.probably) == (Known.INFERRED, HoleReason.SPACING, 1)
        assert hole.closings == (D(2026, 4, 10),)

    def test_HoleThreshold_IsOneAndAHalfCadences(self):
        assert HOLE_CADENCES == 1.5


class TestWhenAStatementIsDue:
    """Statements close on calendar-month steps from the newest. Today 2026-10-05, a monthly
    cadence from three held statements; the newest closed AGE days ago:
      20 days: the next closes in 10 days - nothing due
      35 days: the next closed about 5 days ago - one due
      70 days: two have closed - two due
    """

    @pytest.mark.parametrize(("age", "due"), [(0, 0), (20, 0), (27, 0), (35, 1), (60, 1), (70, 2)])
    def test_NewestStatementAGivenAgeOld_HasThatManyDue(self, age, due):
        today = D(2026, 10, 5)
        newest = today - timedelta(days=age)
        held = _closings(add_months(newest, -2), add_months(newest, -1), newest)

        spans = describe_account(held, today)

        assert spans.next is not None
        assert len(spans.next.due) == due

    @pytest.mark.parametrize("day", [10, 11, 12])
    def test_ClosingsOnTheTenthEleventhOrTwelfth_DueDaysAreThatDayOfEachMonth(self, day):
        today = D(2026, 10, 5)
        held = _closings(D(2026, 5, day), D(2026, 6, day), D(2026, 7, day))

        spans = describe_account(held, today)

        assert spans.next is not None
        assert spans.next.due == (D(2026, 8, day), D(2026, 9, day))
        assert spans.next.expected_close == D(2026, 8, day)

    def test_DueClosings_AgreeWithTheClosingsOfAHoleOfTheSameSpan(self):
        # A hole between 03-10 and 06-10 names two statements; the same span after 03-10, once
        # today is 06-10, has the same two due and the third closing.
        held = _closings(D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 6, 10))
        hole = describe_account(held, D(2026, 7, 1)).holes[0]

        assert hole.closings == (D(2026, 4, 10), D(2026, 5, 10))
        assert hole.closings == tuple(due_closings(D(2026, 3, 10), D(2026, 5, 31)))
        assert hole.probably == len(hole.closings)

    @pytest.mark.parametrize("later_day", list(range(5, 28)))
    def test_HoleCount_AndHoleClosings_AlwaysAgree(self, later_day):
        held = _closings(D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10), D(2026, 5, later_day))

        holes = describe_account(held, D(2026, 7, 1)).holes

        for hole in holes:
            assert hole.probably == len(hole.closings)
            assert all(
                hole.earlier_closing < day < hole.later_closing + timedelta(days=16)
                for day in hole.closings
            )

    def test_LagFromKeptTimes_DelaysWhenTheNextIsAvailable_AndWhenItIsDue(self):
        held = [
            StatementPeriod(
                "acc", D(2026, 7, 10), None, "x", None, None, 0, 0, None, D(2026, 7, 14)
            ),
            StatementPeriod(
                "acc", D(2026, 8, 10), None, "x", None, None, 0, 0, None, D(2026, 8, 14)
            ),
            StatementPeriod(
                "acc", D(2026, 9, 10), None, "x", None, None, 0, 0, None, D(2026, 9, 16)
            ),
        ]

        early = describe_account(held, D(2026, 10, 12)).next
        later = describe_account(held, D(2026, 10, 14)).next

        assert early is not None and later is not None
        assert (early.lag_days, early.expected_close, early.expected_available) == (
            4,
            D(2026, 10, 10),
            D(2026, 10, 14),
        )
        assert early.due == ()
        assert later.due == (D(2026, 10, 10),)

    def test_WithNoKeptTimes_TheAvailableDayIsTheClosingDay(self):
        held = _closings(D(2026, 7, 10), D(2026, 8, 10), D(2026, 9, 10))

        following = describe_account(held, D(2026, 10, 1)).next

        assert following is not None
        assert following.lag_days is None
        assert following.expected_available == following.expected_close


class TestMonthsBetween:
    def test_ClosingsADayOrTwoOffTheSameDayOfMonth_AreWholeMonthsApart(self):
        assert months_between(D(2026, 1, 10), D(2026, 3, 12)) == 2
        assert months_between(D(2026, 1, 31), D(2026, 2, 28)) == 1
        assert months_between(D(2026, 1, 10), D(2026, 2, 10)) == 1


class TestAPartialStatement:
    """Partial only where the closing day is later than the day the document was produced
    (printed), else received, else today."""

    def _item(self, *, closing, produced=None, received=None):
        return StatementPeriod(
            "acc", closing, None, "credit-union-pdf", None, None, 0, 0, produced, received
        )

    def test_ProducedBeforeItsClosing_CoversToTheDayProduced_StatedProduced(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 6, 30), produced=D(2026, 4, 2), received=D(2026, 4, 3))],
            TODAY,
        ).statements

        assert (span.last, span.last_known, span.complete, span.bounded_by) == (
            D(2026, 4, 2),
            Known.STATED,
            False,
            Bound.PRODUCED,
        )

    def test_ProducedAfterItsClosing_IsWholeWhateverTheReceiptSays(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 3, 31), produced=D(2026, 4, 2), received=D(2026, 3, 20))],
            TODAY,
        ).statements

        assert (span.last, span.complete, span.bounded_by) == (D(2026, 3, 31), True, None)

    def test_ProducedOnItsClosingDay_IsWhole(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 3, 31), produced=D(2026, 3, 31))], TODAY
        ).statements

        assert span.complete

    def test_NoProductionDate_ReceivedBeforeItsClosing_IsPartialToTheDayReceived_Weaker(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 6, 30), received=D(2026, 4, 3))], TODAY
        ).statements

        assert (span.last, span.last_known, span.complete, span.bounded_by) == (
            D(2026, 4, 3),
            Known.INFERRED,
            False,
            Bound.RECEIVED,
        )

    def test_NoProductionDate_ReceivedAfterItsClosing_IsWhole(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 3, 31), received=D(2026, 4, 3))], TODAY
        ).statements

        assert span.complete

    def test_NeitherProducedNorReceived_AClosingInTheFuture_IsPartialToToday(self):
        (span,) = describe_account([self._item(closing=D(2026, 7, 31))], TODAY).statements

        assert (span.last, span.complete, span.bounded_by) == (TODAY, False, Bound.TODAY)

    def test_ThePrintedProductionDay_WinsOverAnEarlierReceipt(self):
        (span,) = describe_account(
            [self._item(closing=D(2026, 6, 30), produced=D(2026, 6, 29), received=D(2026, 6, 1))],
            TODAY,
        ).statements

        assert (span.last, span.bounded_by) == (D(2026, 6, 29), Bound.PRODUCED)
