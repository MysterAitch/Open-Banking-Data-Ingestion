"""The time the bank's feed states for each item, on the ledger and in the explanations.

Every scenario varies `feed_morning_corpus` (see its docstring for the answers decided
first). The feed states UTC and the bank's app shows London time, so a page shows
London time and says so once; a row's date and the day it counts toward never move.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime

import pytest

from feed_morning_corpus import (
    FIGURES,
    LONDON,
    TOP_UP_LISTED,
    WORDS,
    cafe_payment,
    declined_attempt,
    morning,
    top_up,
)
from obdi.ingest.family_anchors import families_of
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ledger import build_ledger
from obdi.verify.balance_anchors import effective_opening
from obdi.web_ledger import render_ledger
from round_up_corpus import card_payment, household_store, main_feed, space_feed
from test_export_cuts import Row
from test_space_attribution import MAIN, MAP

CLOCK_NOTE = "Times are London time, as the bank's app shows them."


@pytest.fixture
def make(tmp_path):
    opened = []

    def build(main=(), space=(), **more):
        # A directory per store, so two stores built in one test never share a file.
        directory = tmp_path / f"store-{len(opened)}"
        directory.mkdir()
        store = household_store(
            directory, [*main_feed(), *main], [*space_feed(), *space], **more
        )
        opened.append(store)
        assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def ledger_of(store, month="2026-09"):
    return build_ledger(store, MAIN, month, bound=False, families=families_of(store, MAP))


def render(store, month="2026-09") -> str:
    return render_ledger(ledger_of(store, month), unmasked=False).decode("utf-8")


def plain(page: str) -> str:
    """The page's words as a reader gets them: tags gone, entities read, spacing collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", page)))


def listed_morning(make, **more):
    return make(morning(), export_extra=(TOP_UP_LISTED,), **more)


class TestAMorningOfThreeItems:
    def test_Differences_WhenTheExportOmitsThePayment_TheExportIsAboveByItsSize(self, make):
        store = listed_morning(make)
        opening = effective_opening(store, MAIN, families=families_of(store, MAP))

        found = {r.day: r.difference_minor for r in opening.family.readings if r.difference_minor}

        assert found == {date(2026, 9, 9): 3699, date(2026, 9, 10): 3699, date(2026, 9, 12): 3699}

    def test_Ledger_WhenTheFeedStatesTimes_ADaysRowsAreOrderedByThemNewestFirst(self, make):
        ledger = ledger_of(listed_morning(make))

        today = [row for row in ledger.rows if row.dated == date(2026, 9, 9)]

        assert [row.feed_at for row in today] == [
            datetime(2026, 9, 9, 5, 11, tzinfo=UTC),
            datetime(2026, 9, 9, 4, 58, tzinfo=UTC),
        ]
        assert [row.direction for row in today] == ["out", "in"]

    def test_Ledger_WhenManyRowsShareADay_TheyAreInTimeOrderWhateverTheirIdsAre(self, make):
        """Eight rows, so an id order that matched the time order would be a 1 in 40320 chance."""
        rush = [
            card_payment(
                f"f-rush-{n}",
                f"Rush{n}",
                100 + n,
                9,
                transactionTime=f"2026-09-09T0{n}:{10 + 7 * n}:00.000Z",
            )
            for n in range(8)
        ]
        ledger = ledger_of(make(rush))

        today = [row.feed_at for row in ledger.rows if row.dated == date(2026, 9, 9)]

        assert len(today) == 8
        assert today == sorted(today, reverse=True)

    def test_Ledger_WhenTheFeedReportsInTheOppositeOrder_TheTimesStillDecideTheOrder(self, make):
        reversed_feed = [declined_attempt(), top_up(), cafe_payment()]
        ledger = ledger_of(make(reversed_feed, export_extra=(TOP_UP_LISTED,)))

        today = [row for row in ledger.rows if row.dated == date(2026, 9, 9)]

        assert [row.direction for row in today] == ["out", "in"]

    def test_Ledger_WhenARowHasNoFeedTime_ItKeepsItsPlaceAfterTheDaysTimedRows(self, make):
        untimed = Row("Cheque", -1111, 9, 9)
        ledger = ledger_of(make(morning(), export_extra=(TOP_UP_LISTED, untimed)))

        today = [row for row in ledger.rows if row.dated == date(2026, 9, 9)]

        assert [row.feed_at is None for row in today] == [False, False, True]
        assert [row.sources for row in today][-1] == ("starling-csv",)

    def test_Ledger_WhenTheExportArrivesLast_TheTimesAreTheSameAsWhenItArrivesFirst(self, make):
        first = ledger_of(listed_morning(make))
        last = ledger_of(listed_morning(make, export_last=True))

        assert [(r.dated, r.feed_at) for r in first.rows] == [
            (r.dated, r.feed_at) for r in last.rows
        ]


class TestThePageShowsTheFeedsTime:
    def test_Ledger_WhenARowHasAFeedTime_ListsItBesideTheDateInLondonTime(self, make):
        page = plain(render(listed_morning(make)))

        for shown in LONDON[1:]:
            assert f"2026-09-09 {shown}" in page
        assert "2026-09-09 05:57" not in page, "the DECLINED item makes no row, so no row shows it"

    def test_Ledger_WhenARowHasNoFeedItem_ShowsOnlyItsDate(self, make):
        untimed = Row("Cheque", -1111, 9, 9)
        store = make(morning(), export_extra=(TOP_UP_LISTED, untimed))
        page = render(store)

        # The time a feed stated is said when a transaction is opened, and only for those that
        # have one: a transaction no feed item reports has the date alone.
        timed = [row for row in ledger_of(store).rows if row.feed_at is not None]
        assert len(timed) < len(ledger_of(store).rows), "the cheque has no feed item"
        assert page.count('class="muted t-time"') == len(timed), "a row with no time shows none"

    def test_Explanation_WhenARowIsNamed_SaysTheFeedTimeBesideTheDayTheFeedGave(self, make):
        page = plain(render(listed_morning(make)))

        assert (
            "out row dated starling 2026-09-09 06:11, ledger 2026-09-09; seen by starling"
        ) in page

    def test_Page_WhenFeedTimesAreShown_SaysWhichClockOnceAndWhatItDoesToADate(self, make):
        page = plain(render(listed_morning(make)))

        assert page.count(CLOCK_NOTE) == 1
        assert "The bank's feed states UTC" in page
        assert "the transaction keeps the feed's own (UTC) date and counts toward that day" in page

    def test_Page_WhenNoRowIsFedByTheBank_SaysNothingOfTheClockAndShowsNoTime(self, tmp_path):
        from obdi.ingest.store import Store
        from test_ledger import CURRENT, land, txn

        with Store(tmp_path / "elsewhere.sqlite3") as store:
            land(store, "d", txn(CURRENT, "src-a", "x", date(2026, 3, 2), -1250, "ONE"))
            ledger = build_ledger(store, CURRENT, "2026-03", bound=False)

        page = plain(render_ledger(ledger, unmasked=False).decode("utf-8"))

        assert CLOCK_NOTE not in page
        assert all(row.feed_at is None for row in ledger.rows)

    def test_MaskedPage_WhenTimesAreShown_StillShowsNoFigureDescriptionOrPayee(self, make):
        page = render(listed_morning(make))

        for figure in FIGURES:
            assert figure not in page, figure
        for word in WORDS:
            assert word not in page, word


class TestARowEitherSideOfAClockChangeOrMidnight:
    def stored(self, make, *times: str):
        return make(
            [
                card_payment(f"f-t{n}", f"Item{n}", 111 + n, 1, transactionTime=stamp)
                for n, stamp in enumerate(times)
            ]
        )

    def test_Summer_ARowJustBeforeMidnightUtc_ShowsItsLondonDateAndKeepsTheFeedsDay(self, make):
        store = self.stored(make, "2026-09-30T23:30:00.000Z")

        ledger = ledger_of(store, "2026-09")
        page = plain(render(store, "2026-09"))

        late = next(row for row in ledger.rows if row.dated == date(2026, 9, 30))
        assert late.feed_at == datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
        assert "2026-09-30 00:30 on 2026-10-01" in page
        assert not [r for r in ledger_of(store, "2026-10").rows if r.dated == date(2026, 9, 30)]

    def test_Summer_ARowJustAfterMidnightUtc_ShowsTheSameDateAnHourLater(self, make):
        store = self.stored(make, "2026-09-30T00:10:00.000Z")

        page = plain(render(store, "2026-09"))

        assert "2026-09-30 01:10booked" in page

    def test_Winter_ARowJustBeforeMidnightUtc_ShowsTheSameClockAndNoSecondDate(self, make):
        store = self.stored(make, "2026-12-15T23:30:00.000Z")

        page = plain(render(store, "2026-12"))

        assert "2026-12-15 23:30booked" in page
        assert " on 2026-12-16" not in page

    def test_ClocksGoingBack_TwoRowsAMinuteApart_AreOrderedByInstantNotByTheClockShown(self, make):
        store = self.stored(make, "2026-10-25T00:59:00.000Z", "2026-10-25T01:00:00.000Z")

        page = plain(render(store, "2026-10"))

        assert page.index("2026-10-25 01:00") < page.index("2026-10-25 01:59")
