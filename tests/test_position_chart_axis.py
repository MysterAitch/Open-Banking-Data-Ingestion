"""The position chart says what its lines are and how long a time it covers.

The chart had a sentence beneath it describing its lines by colour, and an axis
labelled at its first and last month only, so a line could not be named at a
glance and nothing said whether a slope took a quarter or five years.

Each household is invented, and what the axis must say was worked out by hand
BEFORE the first run (today is 2026-10-02 throughout, so every history ends in
2026-10):

    three years    rows from 2023-11, so 36 months, 2023-11 to 2026-10
        Januaries  2024-01 (column 2), 2025-01 (column 14), 2026-01 (column 26)
        span       "3 years"
    eight months   the household of `test_position_chart_choice`, 2026-03 to 2026-10
        Januaries  none, so every month is named: Mar Apr May Jun Jul Aug Sep Oct
        span       "8 months"
    eight years    rows from 2019-01, so 94 months, 2019-01 to 2026-10
        Januaries  2019 to 2026, eight of them
        quarters   Apr, Jul, Oct of 2019 to 2026, twenty-four ticks, none named
        span       "7 years 10 months"
    one month      one row on 2026-10-02 only
        span       "1 month"
"""

from __future__ import annotations

import itertools
import re
from datetime import date

import pytest

from obdi.balance_anchors import record_stated_anchor
from obdi.position import read_position
from obdi.store import Store
from obdi.web_position import render_position
from test_ledger import land, txn
from test_position_chart_choice import household

D = date
TODAY = D(2026, 10, 2)

_LEFT, _RIGHT, _WIDTH = 12, 12, 400
#: The chart itself, as distinct from the swatches of its key.
CHART = re.compile(r'<svg role="img" aria-labelledby="chart-title chart-desc".*?</svg>', re.S)


def _column_x(column: int, months: int) -> float:
    return _LEFT + column * (_WIDTH - _LEFT - _RIGHT) / (months - 1)


def from_month(store: Store, first: date, *, with_uncounted: bool = False) -> None:
    """One account with one row, and a known balance on the day of that row."""
    land(store, "d-everyday", txn("everyday", "s", "e1", first, 20000, "PAY"))
    record_stated_anchor(store, "everyday", first.isoformat(), "450.00")
    if with_uncounted:
        land(store, "d-mystery", txn("mystery", "s", "y1", first, 7000, "IN"))


def chart_of(tmp_path, build, **kwargs) -> str:
    with Store(tmp_path / "p.sqlite3") as store:
        build(store, **kwargs)
        position = read_position(store, labels={}, today=TODAY)
    page = render_position(position, unmasked=True).decode("utf-8")
    found = CHART.search(page)
    assert found, "the page with values shown draws no chart"
    return found.group(0)


def page_of(tmp_path, build, *, unmasked: bool, **kwargs) -> str:
    with Store(tmp_path / "p.sqlite3") as store:
        build(store, **kwargs)
        position = read_position(store, labels={}, today=TODAY)
    return render_position(position, unmasked=unmasked).decode("utf-8")


def tick_labels(svg: str, kind: str) -> list[str]:
    return re.findall(rf'<text[^>]*data-tick-label="{kind}"[^>]*>([^<]*)</text>', svg)


def tick_xs(svg: str, kind: str) -> list[float]:
    return [
        float(x)
        for x in re.findall(rf'<line[^>]*data-tick="{kind}"[^>]*\bx1="([0-9.]+)"', svg)
    ]


def span(svg: str) -> str:
    found = re.search(r"<text[^>]*data-span[^>]*>([^<]*)</text>", svg)
    return found.group(1) if found else ""


def line_points(svg: str, series: str) -> list[float]:
    """The x of every point of every line of `series`, in order, without repeats."""
    xs: list[float] = []
    for points in re.findall(rf'<polyline points="([^"]*)" data-series="{series}"', svg):
        for pair in points.split():
            x = float(pair.split(",")[0])
            if x not in xs:
                xs.append(x)
    return sorted(xs)


def three_years(store: Store, **kwargs) -> None:
    from_month(store, D(2023, 11, 5), **kwargs)


def eight_years(store: Store, **kwargs) -> None:
    from_month(store, D(2019, 1, 5), **kwargs)


def one_month(store: Store, **kwargs) -> None:
    # Not the first of the month: a balance known on the 1st makes the opening the last day
    # of September, and the history then starts a month earlier.
    from_month(store, D(2026, 10, 2), **kwargs)


class TestTheAxisOfAChartSpanningYears:
    def test_Axis_OverThreeYears_NamesEachJanuaryByItsYear(self, tmp_path):
        svg = chart_of(tmp_path, three_years)

        assert tick_labels(svg, "year") == ["2024", "2025", "2026"]

    def test_Axis_OverThreeYears_PutsEachYearWhereItsJanuaryIsOnTheLine(self, tmp_path):
        svg = chart_of(tmp_path, three_years)

        on_the_line = line_points(svg, "known")
        assert len(on_the_line) == 36
        assert tick_xs(svg, "year") == pytest.approx(
            [on_the_line[2], on_the_line[14], on_the_line[26]], abs=0.06
        )
        assert tick_xs(svg, "year") == pytest.approx(
            [_column_x(2, 36), _column_x(14, 36), _column_x(26, 36)], abs=0.06
        )

    def test_Axis_OverThreeYears_SaysHowLongItCovers(self, tmp_path):
        assert span(chart_of(tmp_path, three_years)) == "3 years"

    def test_Axis_OverEightYears_NamesEveryYearAndTicksEachQuarterUnnamed(self, tmp_path):
        svg = chart_of(tmp_path, eight_years)

        assert tick_labels(svg, "year") == [str(year) for year in range(2019, 2027)]
        assert len(tick_xs(svg, "month")) == 24
        assert tick_labels(svg, "month") == []

    def test_Axis_OverEightYears_SaysHowLongItCovers(self, tmp_path):
        assert span(chart_of(tmp_path, eight_years)) == "7 years 10 months"

    def test_Axis_OverEightYears_KeepsItsLabelsApart(self, tmp_path):
        svg = chart_of(tmp_path, eight_years)

        xs = tick_xs(svg, "year")
        assert all(later - earlier >= 30 for earlier, later in itertools.pairwise(xs))


class TestTheAxisOfAChartUnderAYear:
    def test_Axis_OverEightMonthsWithNoJanuary_NamesEveryMonth(self, tmp_path):
        svg = chart_of(tmp_path, household)

        assert tick_labels(svg, "year") == []
        assert tick_labels(svg, "month") == [
            "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
        ]

    def test_Axis_OverEightMonths_SaysHowLongItCovers(self, tmp_path):
        assert span(chart_of(tmp_path, household)) == "8 months"

    def test_Axis_OfOneMonth_DrawsAndSaysOneMonth(self, tmp_path):
        svg = chart_of(tmp_path, one_month)

        assert span(svg) == "1 month"
        assert tick_labels(svg, "month") == ["Oct"]

    def test_Axis_StillNamesItsFirstAndLastMonthInFull(self, tmp_path):
        svg = chart_of(tmp_path, three_years)

        assert ">2023-11</text>" in svg
        assert ">2026-10</text>" in svg


def key_entries(page: str) -> dict[str, tuple[str, str, str]]:
    """Each key entry by its series: (stroke, dash pattern, the words beside the swatch)."""
    found = re.search(r'<ul class="legend chart-key"[^>]*>(.*?)</ul>', page, re.S)
    if not found:
        return {}
    entries = {}
    for name, swatch, words in re.findall(
        r'<li data-key="([a-z-]+)">(<svg.*?</svg>)\s*([^<]*)</li>', found.group(1), re.S
    ):
        stroke = re.search(r'stroke="([^"]+)"', swatch)
        dash = re.search(r'stroke-dasharray="([^"]+)"', swatch)
        assert stroke, f"the key's swatch for {name} draws no line"
        entries[name] = (stroke.group(1), dash.group(1) if dash else "", words.strip())
    return entries


def lines_drawn(page: str) -> set[tuple[str, str]]:
    """(stroke, dash pattern) of every line the chart draws."""
    svg = CHART.search(page)
    assert svg
    drawn = set()
    for line in re.findall(r"<polyline[^>]*>", svg.group(0)):
        stroke = re.search(r'stroke="([^"]+)"', line)
        dash = re.search(r'stroke-dasharray="([^"]+)"', line)
        assert stroke
        drawn.add((stroke.group(1), dash.group(1) if dash else ""))
    return drawn


class TestTheKeyThatNamesTheLines:
    def test_Key_WithKnownPartialAndProvisionalLines_NamesAllThree(self, tmp_path):
        page = page_of(tmp_path, household, unmasked=True)

        key = key_entries(page)
        assert set(key) == {"known", "partial", "provisional"}
        assert key["known"][2] == "Net worth that is known"
        assert key["partial"][2] == "Net worth in a partial month, which leaves something out"
        assert key["provisional"][2] == (
            "Provisional total, which counts each unknown opening balance as nil"
        )

    def test_Key_DrawsEachSwatchAsItsLineIsDrawn(self, tmp_path):
        page = page_of(tmp_path, household, unmasked=True)

        swatches = {(stroke, dash) for stroke, dash, _ in key_entries(page).values()}
        assert swatches == lines_drawn(page)
        assert len(swatches) == 3

    def test_Key_WithOnlyTheKnownLine_NamesOnlyThatLine(self, tmp_path):
        page = page_of(tmp_path, three_years, unmasked=True)

        assert set(key_entries(page)) == {"known"}
        assert len(lines_drawn(page)) == 1

    def test_Key_WithAnUncountedAccount_AddsTheProvisionalLine(self, tmp_path):
        page = page_of(tmp_path, three_years, unmasked=True, with_uncounted=True)

        assert set(key_entries(page)) == {"known", "provisional"}

    def test_Key_StandsBeforeTheChart(self, tmp_path):
        page = page_of(tmp_path, household, unmasked=True)

        chart = CHART.search(page)
        assert chart
        assert page.index('<ul class="legend chart-key"') < chart.start()

    def test_Key_OnTheMaskedPage_IsAbsentWithTheChart(self, tmp_path):
        page = page_of(tmp_path, household, unmasked=False)

        assert "chart-key" not in page
        assert "<polyline" not in page

    def test_Key_OfAChartDrawnFromSomeAccounts_SaysChosenAndNotNetWorth(self, tmp_path):
        with Store(tmp_path / "p.sqlite3") as store:
            household(store)
            position = read_position(store, labels={}, today=TODAY)
        rest = {item.key for item in position.chart_items} - {"account:hsbc-mortgage"}

        page = render_position(position, unmasked=True, chart_in=rest).decode("utf-8")

        key = key_entries(page)
        assert key["known"][2] == "Total of the chosen accounts that is known"
        assert not any("Net worth" in words for _, _, words in key.values())
