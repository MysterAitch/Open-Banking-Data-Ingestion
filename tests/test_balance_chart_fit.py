"""The masked timeline fits the screen, says where the changes are, and says it in words.

The owner opened the year 2022 on a phone and saw one uniform bar: the strip was
ten thousand pixels wide, the first change was weeks in, and a phone shows a
fortieth of that. Every check here is over `invented_balance_account`, whose
changes are decided in that module before any page is drawn.

Pages are parsed with the standard HTML parser, never matched as strings beyond a
literal marker.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from html.parser import HTMLParser
from itertools import pairwise
from urllib.parse import parse_qs, urlparse

import pytest

import invented_balance_account as inv
from obdi.web_balance_chart import render_balance_chart

REF = "invented-main"
YEAR = (date(2022, 1, 1), date(2022, 12, 31))
MONTH = (date(2022, 4, 1), date(2022, 4, 30))
VIEWBOX_WIDTH = 360


class Page(HTMLParser):
    """Elements with attributes, text, links with their words, and tables as cell text."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.pieces: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.rows: list[list[str]] = []
        self._link: list[str] | None = None
        self._href = ""
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self.svg_text: list[str] = []
        self._svg_depth = 0
        self.forms: list[tuple[dict[str, str], dict[str, str]]] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        found = {k: v or "" for k, v in attrs}
        self.elements.append((tag, found))
        if tag == "svg":
            self._svg_depth += 1
        if tag == "form":
            self.forms.append((found, {}))
        if tag == "input" and self.forms and "name" in found:
            self.forms[-1][1][found["name"]] = found.get("value", "")
        if tag == "a":
            self._link, self._href = [], found.get("href", "")
        if tag == "tr":
            self._row = []
        if tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "svg":
            self._svg_depth -= 1
        if tag == "a" and self._link is not None:
            self.links.append((self._href, "".join(self._link).strip()))
            self._link = None
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        self.pieces.append(data)
        if self._svg_depth:
            self.svg_text.append(data)
        for sink in (self._link, self._cell):
            if sink is not None:
                sink.append(data)

    def find(self, tag: str, **where: str) -> list[dict[str, str]]:
        return [
            a for name, a in self.elements
            if name == tag and all(a.get(k.rstrip("_").replace("_", "-")) == v
                                   for k, v in where.items())
        ]

    @property
    def text(self) -> str:
        return " ".join(" ".join(self.pieces).split())


def masked(scale: int = 1, window: tuple[date, date] | None = None) -> str:
    start, end = window or (None, None)
    return render_balance_chart(
        inv.invented_chart(scale), unmasked=False, start=start, end=end
    ).decode()


def shown(window: tuple[date, date] | None = None) -> str:
    start, end = window or (None, None)
    return render_balance_chart(
        inv.invented_chart(), unmasked=True, start=start, end=end
    ).decode()


def strip(page: Page) -> dict[str, str]:
    (found,) = [a for a in page.find("svg") if a.get("aria-labelledby", "").startswith("bc-strip")]
    return found


def counts_table(page: Page) -> list[list[str]]:
    return [row for row in page.rows if row and row[0] and row[0][0].isalnum()
            and any(link[1] == row[0] for link in page.links)]


class TestTheFixtureHoldsItsKnownAnswer:
    def test_Account_WhenWalked_HoldsExactlyTheChangesItWasBuiltWith(self):
        chart = inv.invented_chart()
        steps = chart.structure.whole.steps
        changes = [s for s in steps if s.partner < 0 or steps[s.partner].day > s.day]

        assert len(changes) == inv.TOTAL_CHANGES
        assert Counter(s.kind for s in changes) == inv.BY_KIND
        assert Counter(s.day.year for s in changes) == inv.BY_YEAR
        assert len(chart.days) > 2_500


class TestTheStripFitsTheWidthOfTheScreen:
    @pytest.mark.parametrize("window", [None, YEAR, MONTH], ids=["held", "year", "month"])
    def test_Strip_WhateverTheRange_ScalesToTheWholeWidthOfItsContainer(self, window):
        svg = strip(Page(masked(window=window)))

        assert svg["width"] == "100%"
        assert "height" not in svg
        assert svg["viewbox"].split()[2] == str(VIEWBOX_WIDTH)

    @pytest.mark.parametrize("window", [None, YEAR, MONTH], ids=["held", "year", "month"])
    def test_Page_WhateverTheRange_NothingOnItScrollsSideways(self, window):
        page = Page(masked(window=window))

        # The chart does not scroll. The counts table below it may, inside the box
        # every wide table is given (marked `data-table-scroll`), and that box
        # holds no chart.
        assert [r for r in page.find("div", role="region") if "data-table-scroll" not in r] == []
        for _, attrs in page.elements:
            if "data-table-scroll" in attrs:
                continue
            assert "overflow" not in attrs.get("style", "")
            assert "scroll" not in attrs.get("class", "").split()
            assert "max-width:none" not in attrs.get("style", "")

    @pytest.mark.parametrize("window", [None, YEAR, MONTH], ids=["held", "year", "month"])
    def test_Strip_WhateverTheRange_EveryMarkLiesInsideTheFirstScreenful(self, window):
        page = Page(masked(window=window))

        marks = [a for a in page.find("rect") if a.get("class") == "mark"]
        assert marks
        for mark in marks:
            left, width = float(mark["x"]), float(mark["width"])
            assert left >= 0
            assert left + width <= VIEWBOX_WIDTH

    def test_Strip_WhenTheFirstChangeIsWeeksIn_ItIsStillInTheLeftThirdOfTheYear(self):
        page = Page(masked(window=YEAR))

        xs = sorted(float(a["x"]) for a in page.find("rect") if a.get("class") == "mark")
        plot_left = float(strip(page)["data-plot-left"])
        plot_right = float(strip(page)["data-plot-right"])
        assert xs[0] - plot_left < (plot_right - plot_left) / 3

    def test_Page_WhenMasked_NoLongerQuotesAPixelScale(self):
        text = Page(masked()).text

        assert "pixels a day" not in text


class TestBinsCarryTheRightCounts:
    def test_Strip_ForTheBusyYear_DrawsOneMarkPerMonthPerKindWithItsCount(self):
        page = Page(masked(window=YEAR))

        marks = [a for a in page.find("rect") if a.get("class") == "mark"]
        got = {(m["data-kind"], m["data-bin"]): int(m["data-count"]) for m in marks}
        assert got[("unexplained", "2022-04-01")] == 3
        assert got[("explained", "2022-04-01")] == 1
        assert got[("transient", "2022-06-01")] == 1
        assert sum(c for (kind, _), c in got.items() if kind == "unexplained") == 11
        assert sum(c for (kind, _), c in got.items() if kind == "explained") == 4
        assert sum(c for (kind, _), c in got.items() if kind == "transient") == 4

    def test_Strip_ForOneMonth_DrawsOneMarkPerDayWithoutACountWhereTheDayHoldsOne(self):
        page = Page(masked(window=MONTH))

        marks = [a for a in page.find("rect") if a.get("class") == "mark"]
        assert sorted(m["data-bin"] for m in marks) == [
            "2022-04-04", "2022-04-05", "2022-04-06", "2022-04-30"
        ]
        assert {m["data-count"] for m in marks} == {"1"}

    def test_Strip_WhereAMarkHoldsSeveral_TheCountIsDrawnAndWhereItHoldsOneItIsNot(self):
        page = Page(masked(window=YEAR))

        digits = [p.strip() for p in page.pieces if p.strip() in {"1", "2", "3", "4", "5"}]
        assert "3" in digits

    def test_Strip_ForEverythingHeld_UsesCoarserBinsSoNeighboursNeverOverlap(self):
        page = Page(masked())

        marks = sorted(
            (float(a["x"]), float(a["width"])) for a in page.find("rect")
            if a.get("class") == "mark" and a["data-kind"] == "unexplained"
        )
        for (left, width), (right, _) in pairwise(marks):
            assert left + width <= right

    def test_Strip_EveryMarkOfAKindIsTheSameHeightWhateverItsCount(self):
        page = Page(masked(window=YEAR))

        marks = [a for a in page.find("rect") if a.get("class") == "mark"]
        assert {m["height"] for m in marks} and len({m["height"] for m in marks}) == 1
        assert len({m["width"] for m in marks}) == 1

    def test_Strip_WhenEverySizeIsSevenTimesAsLarge_EveryRangeRendersToTheSameBytes(self):
        for window in (None, YEAR, MONTH):
            assert masked(1, window) == masked(7, window)


class TestTheRangeIsSaidBeforeTheChart:
    def test_Page_ForTheBusyYear_CountsTheChangesByKindAndNamesTheFirstAndLastDay(self):
        text = Page(masked(window=YEAR)).text

        assert "19 changes in this range" in text
        assert "4 timing pairs" in text and "11 unexplained" in text and "4 explained" in text
        assert "the first on 2022-02-09" in text and "the last on 2022-12-23" in text

    def test_Page_ForEverythingHeld_CountsAllThirtyNineChanges(self):
        text = Page(masked()).text

        assert "39 changes in this range" in text
        assert "6 timing pairs" in text and "26 unexplained" in text and "7 explained" in text

    def test_Page_WhenOneChangeIsInRange_SaysSoInTheSingular(self):
        text = Page(masked(window=(date(2022, 7, 15), date(2022, 7, 25)))).text

        assert "1 change in this range" in text
        assert "1 explained" in text

    def test_Page_WhenTheRangeHoldsNoChange_SaysSoAndDrawsNoStrip(self):
        page = Page(masked(window=(date(2023, 1, 1), date(2023, 12, 31))))

        assert "No change falls in this range" in page.text
        assert [a for a in page.find("svg") if "bc-strip" in a.get("aria-labelledby", "")] == []
        assert counts_table(page) == []

    def test_Page_WhenTheRangeHoldsNoChange_StillOffersWaysToLeaveIt(self):
        page = Page(masked(window=(date(2023, 1, 1), date(2023, 12, 31))))

        assert any("Draw everything held" in text for _, text in page.links)


class TestTheCountsTableSaysWhereInTimeTheChangesAre:
    def test_Table_ForEverythingHeld_CountsPerYearAndTheCountsSumToTheTotal(self):
        page = Page(masked())

        rows = counts_table(page)
        by_year = {int(r[0]): sum(int(c or 0) for c in r[1:]) for r in rows}
        assert by_year == inv.BY_YEAR
        assert sum(by_year.values()) == inv.TOTAL_CHANGES

    def test_Table_ForEverythingHeld_ListsEmptyYearsInOneLineNotAsEmptyRows(self):
        page = Page(masked())

        assert "Nothing in 2020, 2023, and 2024" in page.text
        assert {r[0] for r in counts_table(page)}.isdisjoint({"2020", "2023", "2024"})

    def test_Table_ForTheBusyYear_CountsPerMonthAndTheCountsSumToNineteen(self):
        page = Page(masked(window=YEAR))

        rows = counts_table(page)
        by_month = {r[0]: sum(int(c or 0) for c in r[1:]) for r in rows}
        assert by_month == {
            f"{date(2022, m, 1):%b %Y}": n for m, n in inv.BUSY_BY_MONTH.items()
        }
        assert sum(by_month.values()) == 19

    def test_Table_ForTheBusyYear_HasOneColumnPerKindPresentAndItsCellsAreRight(self):
        page = Page(masked(window=YEAR))

        head = next(r for r in page.rows if r and r[0] == "Period")
        april = next(r for r in counts_table(page) if r[0] == "Apr 2022")
        cells = dict(zip(head[1:], april[1:], strict=True))
        assert cells["Unexplained"] == "3" and cells["Explained"] == "1"
        assert cells["Timing pair"] in ("", "0", "-")

    def test_Table_ForTheBusyYear_ListsTheEmptyMonthsInOneLine(self):
        text = Page(masked(window=YEAR)).text

        assert "Nothing in Jan 2022, May 2022, Aug 2022, and Nov 2022" in text

    def test_Table_ForOneMonth_CountsPerDayAndRunsOfEmptyDaysAreSaidOnce(self):
        page = Page(masked(window=MONTH))

        days = {r[0]: sum(int(c or 0) for c in r[1:]) for r in counts_table(page)}
        assert days == {"2022-04-04": 1, "2022-04-05": 1, "2022-04-06": 1, "2022-04-30": 1}
        assert "Nothing from 2022-04-01 to 2022-04-03" in page.text

    def test_Table_EachPeriodLinksToThatPeriodAsAMaskedRange(self):
        page = Page(masked())

        links = {text: href for href, text in page.links if "from=" in href}
        query = parse_qs(urlparse(links["2022"]).query)
        assert query == {"ref": [REF], "from": ["2022-01-01"], "to": ["2022-12-31"]}
        query = parse_qs(urlparse(links["2019"]).query)
        assert (query["from"], query["to"]) == (["2019-01-01"], ["2019-12-31"])

    def test_Table_ForTheBusyYear_MonthLinksOpenThatMonth(self):
        page = Page(masked(window=YEAR))

        links = {text: href for href, text in page.links if "from=" in href}
        query = parse_qs(urlparse(links["Feb 2022"]).query)
        assert (query["from"], query["to"]) == (["2022-02-01"], ["2022-02-28"])

    def test_Table_FollowingALink_ShowsExactlyTheCountTheTableGaveForThatPeriod(self):
        page = Page(masked(window=YEAR))
        links = {text: href for href, text in page.links if "from=" in href}
        query = parse_qs(urlparse(links["Apr 2022"]).query)
        window = (date.fromisoformat(query["from"][0]), date.fromisoformat(query["to"][0]))

        opened = Page(masked(window=window))

        assert "4 changes in this range" in opened.text


class TestLevelsAreVisibleDivisions:
    def test_Levels_WhenSeveralAreInView_AlternateInShadeFromOneToTheNext(self):
        page = Page(masked(window=YEAR))

        levels = [a for a in page.find("rect") if a.get("class") == "level"]
        assert len(levels) > 10
        shades = [a["fill-opacity"] for a in levels]
        assert all(a != b for a, b in pairwise(shades))

    def test_Levels_WhenSeveralAreInView_HaveADividerDrawnAtEachBoundary(self):
        page = Page(masked(window=YEAR))

        levels = [a for a in page.find("rect") if a.get("class") == "level"]
        dividers = [a for a in page.find("line") if a.get("class") == "level-divider"]
        assert len(dividers) == len(levels) - 1

    def test_Levels_UnderTheStrip_NamesTheLongestLevelInViewWithItsSpanInWords(self):
        page = Page(masked(window=YEAR))

        assert (
            "The longest level in this range runs from 2022-10-12 until 2022-12-10 (59 days)"
            in page.text
        )

    def test_Levels_TheLongestLevelLabelIsNotInsideTheStrip(self):
        page = Page(masked(window=YEAR))

        assert "longest level" in page.text
        assert "longest level" not in " ".join(page.svg_text).lower()


class TestEverythingElseTheTimelineHadIsKept:
    def test_Page_WhenMasked_KeepsTheLegendTheStructureInWordsAndTheValuesForm(self):
        page = Page(masked(window=YEAR))

        assert page.find("ul", class_="legend")
        assert "The structure in words" in page.text
        # The window's own control is the page's other form; the one that asks for values
        # is the one that opens a new tab.
        ((form, hidden),) = [f for f in page.forms if f[0].get("target") == "_blank"]
        assert (form["method"], form["action"], form["target"]) == (
            "post", "/balance-chart", "_blank"
        )
        assert hidden == {"ref": REF, "from": "2022-01-01", "to": "2022-12-31"}

    def test_Page_WhenMasked_CarriesNoMoneySymbolAndNoWordForIt(self):
        for window in (None, YEAR, MONTH):
            source = masked(window=window)
            assert "£" not in source and "amount" not in source.lower()


class TestTheValuesChartKeepsItsWidthAndGainsWaysIn:
    def test_Values_ForTheBusyYear_SaysHowManyChangesAreInRangeAndWhenTheFirstIs(self):
        text = Page(shown(YEAR)).text

        assert "19 changes in this range" in text and "the first on 2022-02-09" in text

    def test_Values_ForTheBusyYear_IsStillDrawnWideAndScrollingWithFigures(self):
        page = Page(shown(YEAR))

        (region,) = page.find("div", role="region")
        assert "overflow-x:auto" in region["style"]
        assert "£" in page.text

    def test_Values_ForTheBusyYear_OffersOnePostedLinkPerChangeAroundThatChange(self):
        forms = [attrs for attrs, _ in Page(shown(YEAR)).forms if "data-change" in attrs]
        assert len(forms) == 19
        assert {a["method"] for a in forms} == {"post"}
        assert {a["action"] for a in forms} == {"/balance-chart"}

    def test_Values_EachChangeLink_OpensAWindowOfAFewDaysAroundThatChange(self):
        forms = {a["data-change"]: f for a, f in Page(shown(YEAR)).forms if "data-change" in a}

        hidden = forms["2022-02-09"]
        assert hidden["ref"] == REF
        assert date.fromisoformat(hidden["from"]) == date(2022, 2, 9) - timedelta(days=3)
        assert date.fromisoformat(hidden["to"]) == date(2022, 2, 9) + timedelta(days=3)

    def test_Values_ForAPairLink_TheWindowCoversBothDaysOfThePair(self):
        forms = {a["data-change"]: f for a, f in Page(shown(YEAR)).forms if "data-change" in a}

        hidden = forms["2022-06-29"]
        assert date.fromisoformat(hidden["from"]) <= date(2022, 6, 29)
        assert date.fromisoformat(hidden["to"]) >= date(2022, 7, 2)

    def test_Values_ForEverythingHeld_CapsTheLinksAtTwentyAndSaysHowManyMore(self):
        page = Page(shown())

        assert len([a for a, _ in page.forms if "data-change" in a]) == 20
        assert "and 19 more" in page.text

    def test_Values_WhenMaskedAgain_OffersNoPostedChangeLinksAtAll(self):
        page = Page(masked(window=YEAR))

        assert [a for a, _ in page.forms if "data-change" in a] == []
