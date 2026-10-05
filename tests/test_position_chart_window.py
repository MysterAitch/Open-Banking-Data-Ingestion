"""The position chart over a window of days: its points, axis, and words.

The household, and every figure it gives on each day, week, and month-end, are worked
out by hand in `position_window_household`. What the chart must draw, worked out
BEFORE the first run (today 2026-10-04; the chart is 400 wide with 12 either side, so
points sit between x 12 and x 388, and y runs 30 (the highest figure drawn) to 198 (the
lowest), the provisional line sharing the scale):

    last 90 days, 2026-07-07 to 2026-10-04, daily
        points      90, 4.2247 apart (376 / 89), the last at x 388
        partial     the first 70 (07-07 to 09-14, when the saver opens), then solid
        ticks       12 Mondays, 3 month-firsts (Aug, Sep, Oct), 75 other days
        labels      months Aug Sep Oct; Mondays 13, 10, 24, 14 (a Monday label that
                    would sit within 30 of one placed is dropped: 20 Jul is 29.6 from
                    13 Jul, 27 Jul is 21 from Aug, and so on)
        span        "90 days"
        scale       highest 715,000 (provisional, 09-27), lowest -250,000 (09-12)
        below nil   from 09-12 to 09-25: the wash and its key entry are drawn
    last 7 days, 2026-09-28 to 2026-10-04, daily
        7 points, all solid (every item has a figure), none below nil, span "7 days"
    last 12 months, 2025-10-05 to 2026-10-04, weekly (365 days; the bond is held from 2024)
        53 points, the first and last Sundays; 51 partial (index 0 to 50, the last being
        09-20, the first Sunday with all five items) and 3 solid (09-20, 09-27, 10-04)
        ticks       month-firsts only, and the year at 2026-01-01; a month label that
                    would sit within 30 of one placed is dropped: Mar is 28.9 from Feb
                    (28 days at 1.033 a day), so Nov Dec Feb Apr May Jun Jul Aug Sep Oct
        span        "52 weeks 1 day" (365 days)
        y of each   30 + (715,000 - figure) / 965,000 * 168, from the weekly figures
    last 36 months, monthly: 32 month-ends 2024-03 to 2026-10, as the history
    from 2026-09-01 to 2026-09-27: daily, last point read at 09-27, not today
    from 2024-01-01 to 2024-03-01: before anything is held (2024-03-15), no chart
    the card (the liability) left out of a 90-day window: never below nil
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from itertools import pairwise

import pytest

from obdi.date_window import (
    DAILY_UP_TO_DAYS,
    WEEKLY_UP_TO_DAYS,
    Anchor,
    Period,
    Unit,
    between,
    everything,
    length,
    named,
)
from obdi.position import Position, read_position
from obdi.store import Store
from obdi.web_position import render_position
from position_window_household import CARD, TODAY, window_household
from test_position_chart_axis import eight_years, tick_labels, tick_xs

D = date
CHART = re.compile(r'<svg role="img" aria-labelledby="chart-title chart-desc".*?</svg>', re.S)
TOP, PLOT = 30, 168


@pytest.fixture
def held(tmp_path) -> Position:
    with Store(tmp_path / "w.sqlite3") as store:
        window_household(store)
        return read_position(store, today=TODAY)


def page(position: Position, window, *, chart_in=None, unmasked=True) -> str:
    return render_position(
        position, unmasked=unmasked, window=window, chart_in=chart_in
    ).decode("utf-8")


def chart_in_page(html: str) -> str:
    found = CHART.search(html)
    assert found, "the page draws no chart"
    return found.group(0)


def chart(position: Position, window, **kwargs) -> str:
    return chart_in_page(page(position, window, **kwargs))


def polylines(svg: str, series: str) -> list[list[tuple[float, float]]]:
    out = []
    for points in re.findall(rf'<polyline points="([^"]*)" data-series="{series}"', svg):
        out.append([tuple(float(v) for v in pair.split(",")) for pair in points.split()])
    return out


def xs_of(svg: str, series: str) -> list[float]:
    seen: list[float] = []
    for line in polylines(svg, series):
        for x, _ in line:
            if x not in seen:
                seen.append(x)
    return sorted(seen)


def span(svg: str) -> str:
    found = re.search(r"<text[^>]*data-span[^>]*>([^<]*)</text>", svg)
    return found.group(1) if found else ""


def texts(svg: str) -> list[str]:
    return re.findall(r"<text[^>]*>([^<]*)</text>", svg)


def last_90(position: Position) -> str:
    return chart(position, length(90, Unit.DAYS))


class TestADailyChart:
    def test_Last90Days_DrawsAPointPerDayEvenlySpacedToTheRightEdge(self, held):
        svg = last_90(held)

        xs = xs_of(svg, "known")
        assert len(xs) == 90
        assert xs[0] == pytest.approx(12.0, abs=0.06)
        assert xs[-1] == pytest.approx(388.0, abs=0.06)
        steps = {round(b - a, 1) for a, b in pairwise(xs)}
        assert steps <= {4.2, 4.3}

    def test_Last90Days_DrawsTheDaysBeforeTheSaverOpensDashedAndTheRestSolid(self, held):
        svg = last_90(held)

        lines = polylines(svg, "known")
        partial = next(line for line in lines if len(line) == 70)
        solid = next(line for line in lines if len(line) == 21)
        assert partial[-1][0] == pytest.approx(solid[0][0], abs=0.06)
        assert 'stroke-dasharray="5 5"' in svg

    def test_Last90Days_TicksEveryDayAndMarksEachMondayLonger(self, held):
        svg = last_90(held)

        assert len(tick_xs(svg, "day")) == 75
        assert len(tick_xs(svg, "week")) == 12
        assert len(tick_xs(svg, "month")) == 3

    def test_Last90Days_NamesTheMonthsAndTheMondaysThatFit(self, held):
        svg = last_90(held)

        assert tick_labels(svg, "month") == ["Aug", "Sep", "Oct"]
        assert tick_labels(svg, "week") == ["13", "10", "24", "14"]

    def test_Last90Days_NeverPlacesTwoLabelsWithinThirtyUnitsOfEachOther(self, held):
        svg = last_90(held)
        placed = [
            float(x)
            for x in re.findall(r'<text[^>]*data-tick-label="[a-z]+"[^>]*\bx="([0-9.]+)"', svg)
        ]

        placed.sort()
        assert all(b - a >= 30 for a, b in pairwise(placed))

    def test_Last90Days_SaysItCoversNinetyDaysAndNamesBothEndDaysInFull(self, held):
        svg = last_90(held)

        assert span(svg) == "90 days"
        assert "2026-07-07" in texts(svg) and "2026-10-04" in texts(svg)

    def test_Last90Days_LabelsItsLowestHighestAndLatestAsTheWindows(self, held):
        svg = last_90(held)

        assert "window highest £7,150.00" in texts(svg)
        assert "window lowest -£2,500.00" in texts(svg)
        assert "window latest £7,050.00" in texts(svg)
        assert "provisional latest £7,100.00" in texts(svg)

    def test_Last90Days_SaysOneFigurePerDayInItsTitleDescriptionKeyAndSentence(self, held):
        html = page(held, length(90, Unit.DAYS))
        svg = chart_in_page(html)

        assert "on each day</title>" in svg
        assert "on each day." in svg
        assert '<li data-key="resolution">One figure per day.</li>' in html
        assert "One figure per day. " in html
        assert "at each month-end" not in svg and "One figure per month-end" not in html

    def test_Last90Days_ShowsTheDipBelowNilAndSaysWhatTheWashMeans(self, held):
        html = page(held, length(90, Unit.DAYS))

        assert "data-below-nil" in html
        assert '<li data-key="below-nil">' in html

    def test_Last7Days_ExcludesTheDipSoNothingIsBelowNil(self, held):
        html = page(held, length(7, Unit.DAYS))
        svg = chart_in_page(html)

        assert "data-below-nil" not in html
        assert len(xs_of(svg, "known")) == 7
        assert len(polylines(svg, "known")) == 1
        assert span(svg) == "7 days"

    def test_CardLeftOutOfA90DayWindow_NeverGoesBelowNilAndSaysTheChartLeavesItOut(self, held):
        rest = {i.key for i in held.chart_items} - {CARD}

        html = page(held, length(90, Unit.DAYS), chart_in=rest)

        assert "data-below-nil" not in html
        assert "The chart leaves out: <code>card</code>" in html
        assert "chosen accounts only, not the net worth" in html
        assert "window highest" in html

    def test_WindowEndingInThePast_ReadsItsLastPointAtThatDayAndSaysSo(self, held):
        html = page(held, between(D(2026, 9, 1), D(2026, 9, 27)))
        svg = chart_in_page(html)

        assert "The last point is drawn at the end of 2026-09-27." in html
        assert "window latest £7,100.00" in texts(svg)
        assert "everything held now" not in html

    def test_WindowEndingToday_ReadsItsLastPointAtEverythingHeldNow(self, held):
        html = page(held, length(90, Unit.DAYS))

        assert "The last point is drawn at everything held now." in html


class TestAWeeklyChart:
    WEEKLY = [280000] * 43 + [
        480000, 490000, 520000, 520000, 520000, 560000, -250000, -90000, 710000, 705000,
    ]
    PROVISIONAL = [280000] * 43 + [
        480000, 490000, 520000, 527000, 527000, 567000, -243000, -85000, 715000, 710000,
    ]

    def test_Last12Months_DrawsAPointOnEachSundayFromTheFirstDayToTheLast(self, held):
        svg = chart(held, length(12, Unit.MONTHS))

        xs = xs_of(svg, "known")
        assert len(xs) == 53
        assert xs[0] == pytest.approx(12.0, abs=0.06)
        assert xs[-1] == pytest.approx(388.0, abs=0.06)
        assert [b - a for a, b in pairwise(xs)] == pytest.approx(
            [7 * 376 / 364] * 52, abs=0.11
        )

    def test_Last12Months_DrawsEachSundayAtItsHandWorkedHeight(self, held):
        svg = chart(held, length(12, Unit.MONTHS))
        line = {}
        for points in polylines(svg, "known"):
            for x, y in points:
                line[x] = y

        drawn = [y for _, y in sorted(line.items())]
        expected = [TOP + (715000 - v) / 965000 * PLOT for v in self.WEEKLY]
        assert drawn == pytest.approx(expected, abs=0.06)

    def test_Last12Months_DrawsTheProvisionalLineAtItsHandWorkedHeights(self, held):
        svg = chart(held, length(12, Unit.MONTHS))

        (dotted,) = polylines(svg, "provisional")

        expected = [TOP + (715000 - v) / 965000 * PLOT for v in self.PROVISIONAL]
        assert [y for _, y in dotted] == pytest.approx(expected, abs=0.06)

    def test_Last12Months_IsPartialUntilTheFirstSundayWithAllFiveItems(self, held):
        svg = chart(held, length(12, Unit.MONTHS))

        lines = polylines(svg, "known")
        assert sorted(len(line) for line in lines) == [3, 51]

    def test_Last12Months_NamesTheYearAndTheMonthsThatFitAndTicksNothingFinerThanAMonth(
        self, held
    ):
        svg = chart(held, length(12, Unit.MONTHS))

        assert tick_labels(svg, "year") == ["2026"]
        assert tick_labels(svg, "month") == [
            "Nov", "Dec", "Feb", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
        ]
        assert len(tick_xs(svg, "month")) == 11
        assert tick_xs(svg, "day") == [] and tick_xs(svg, "week") == []

    def test_Last12Months_SaysItsSpanInWeeksAndDaysAndNamesItsEndDays(self, held):
        svg = chart(held, length(12, Unit.MONTHS))

        assert span(svg) == "52 weeks 1 day"
        assert "2025-10-05" in texts(svg) and "2026-10-04" in texts(svg)

    def test_Last12Months_SaysOneFigurePerWeekAndWhichDaysAreRead(self, held):
        html = page(held, length(12, Unit.MONTHS))
        svg = chart_in_page(html)

        assert "on the first day, each Sunday, and the last day</title>" in svg
        assert '<li data-key="resolution">One figure per week.</li>' in html
        assert "One figure per week, read on each Sunday and on the window's first and last" in html


class TestAMonthlyChartOfAWindow:
    def test_Last36Months_DrawsTheSameFiveMonthEndsAsTheHistory(self, held):
        html = page(held, length(36, Unit.MONTHS))
        svg = chart_in_page(html)
        default = chart_in_page(page(held, None))

        assert xs_of(svg, "known") == xs_of(default, "known")
        assert len(xs_of(svg, "known")) == 32
        assert polylines(svg, "known") == polylines(default, "known")
        assert "at each month-end</title>" in svg
        assert '<li data-key="resolution">One figure per month-end.</li>' in html

    def test_Everything_IsTheDefaultChartUnchangedAndNotAWindow(self, held):
        plain = page(held, None)

        assert page(held, everything()) == plain
        assert "data-window-words" not in plain
        assert "window highest" not in plain


class TestTheWordsAboveTheChart:
    def test_Last12Months_SaysWhatWasChosenTheDaysAndTheResolution(self, held):
        html = page(held, length(12, Unit.MONTHS))

        assert (
            "12 months ending 2026-10-04: 2025-10-05 to 2026-10-04, one figure per week."
            in html
        )
        assert "Nothing is held before" not in html

    def test_AWindowStartingBeforeAnythingIsHeld_SaysWhereItStartsInstead(self, held):
        html = page(held, length(10, Unit.YEARS))

        assert (
            "10 years ending 2026-10-04: 2024-03-15 to 2026-10-04, one figure per month-end."
            in html
        )
        assert "Nothing is held before 2024-03-15, so the window starts there." in html

    def test_ANarrowerWindow_SaysItsLowestHighestAndLatestAreItsOwnAndTheTableIsWhole(self, held):
        html = unescape(page(held, length(90, Unit.DAYS)))

        assert "This window is narrower than everything held" in html
        assert "the chart's lowest, highest, and latest are of the window" in html
        assert "The month table below stays monthly and whole" in html

    def test_AWindowCoveringEverythingHeld_DoesNotSayItIsNarrower(self, held):
        html = page(held, between(D(2024, 3, 15), D(2026, 10, 4)))

        assert "This window is narrower" not in html
        assert "data-window-words" in html

    def test_ThisTaxYear_SaysItRunsToAFutureDayAndIsShownToToday(self, held):
        html = page(held, named(Period.THIS_TAX_YEAR))

        assert "This tax year: 2026-04-06 to 2026-10-04, one figure per week." in html
        assert "The period runs to 2027-04-05, which has not come, so it is shown to today." in html

    def test_TaxYearToDate_DoesNotSayItRunsIntoTheFuture(self, held):
        html = page(held, named(Period.TAX_YEAR_TO_DATE))

        assert "which has not come" not in html

    def test_AWindowBeforeAnythingIsHeld_DrawsNoChartAndSaysWhy(self, held):
        html = page(held, between(D(2024, 1, 1), D(2024, 3, 1)))

        assert '<svg role="img"' not in html
        assert "The window ends on 2024-03-01, before anything is held" in html
        assert "No chart is drawn." in html

    def test_AWindowStartingAfterToday_DrawsNoChartAndSaysWhy(self, held):
        html = page(held, between(D(2027, 1, 1), D(2027, 3, 31)))

        assert '<svg role="img"' not in html
        assert "which is after today (2026-10-04)" in html

    def test_AWindowInWhichOnlyAnUnchosenAccountHasAFigure_SaysNothingTickedHasOne(self, held):
        only_pension = {"account:pension"}

        html = page(held, between(D(2026, 7, 1), D(2026, 7, 31)), chart_in=only_pension)

        assert "Nothing that is ticked has a figure in this window" in html
        assert '<svg role="img"' not in html

    def test_TheMaskedPage_DrawsNoChartWhateverWindowIsGiven(self, held):
        html = page(held, length(90, Unit.DAYS), unmasked=False)

        assert '<svg role="img"' not in html
        assert "data-window-words" not in html
        assert "window highest" not in html


class TestTheThresholdsOnARealChart:
    @pytest.fixture
    def long_history(self, tmp_path) -> Position:
        with Store(tmp_path / "e.sqlite3") as store:
            eight_years(store)
            return read_position(store, today=D(2026, 10, 2))

    def window_chart(self, position, first: date, last: date):
        html = render_position(position, unmasked=True, window=between(first, last)).decode()
        return html, chart_in_page(html)

    def test_AWindowOfTheLastDailyLength_IsDailyAndNoMarkIsNearerThanThreeUnits(
        self, long_history
    ):
        last = D.fromordinal(D(2025, 1, 1).toordinal() + DAILY_UP_TO_DAYS - 1)

        html, svg = self.window_chart(long_history, D(2025, 1, 1), last)

        xs = xs_of(svg, "known")
        assert len(xs) == DAILY_UP_TO_DAYS
        assert min(b - a for a, b in pairwise(xs)) >= 3
        assert "One figure per day." in html

    def test_AWindowOneDayLongerThanDaily_IsWeekly(self, long_history):
        last = D.fromordinal(D(2025, 1, 1).toordinal() + DAILY_UP_TO_DAYS)

        html, _ = self.window_chart(long_history, D(2025, 1, 1), last)

        assert "One figure per week." in html and "One figure per day." not in html

    def test_AWindowOfTheLastWeeklyLength_IsWeeklyAndNoMarkIsNearerThanThreeUnits(
        self, long_history
    ):
        last = D.fromordinal(D(2023, 1, 1).toordinal() + WEEKLY_UP_TO_DAYS - 1)

        html, svg = self.window_chart(long_history, D(2023, 1, 1), last)

        xs = xs_of(svg, "known")
        assert min(b - a for a, b in pairwise(xs)) >= 3 - 0.06
        assert "One figure per week." in html

    def test_AWindowOneDayLongerThanWeekly_IsMonthly(self, long_history):
        last = D.fromordinal(D(2023, 1, 1).toordinal() + WEEKLY_UP_TO_DAYS)

        html, svg = self.window_chart(long_history, D(2023, 1, 1), last)

        assert "One figure per month-end." in html
        assert "at each month-end</title>" in svg

    def test_AWindowOfOverAYear_NamesItsYearAndItsMonthsThatFit(self, long_history):
        _, svg = self.window_chart(long_history, D(2024, 3, 10), D(2025, 4, 20))

        assert tick_labels(svg, "year") == ["2025"]
        assert "Apr" in tick_labels(svg, "month")

    def test_ADailyWindowAcrossANewYear_NamesTheYearAndTheMonthBefore(self, long_history):
        _, svg = self.window_chart(long_history, D(2025, 12, 1), D(2026, 1, 31))

        assert tick_labels(svg, "year") == ["2026"]
        assert tick_labels(svg, "month") == ["Dec"]

    def test_ALengthAnchoredToADay_DrawsFromThatDay(self, long_history):
        spec = length(1, Unit.MONTHS, Anchor.STARTING_ON, D(2026, 1, 31))

        html = render_position(long_history, unmasked=True, window=spec).decode()

        assert "1 month starting 2026-01-31: 2026-01-31 to 2026-02-27, one figure per day." in html
