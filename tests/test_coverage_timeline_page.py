"""The coverage timeline page, served over the household drawn by hand in `coverage_timeline_world`.

Known answers, worked out from that docstring and the scale rule before the first run. The
default window is the last 12 months, which is cut at the first day anything is held (2026-07-01)
and today (2026-10-05): 97 days, drawn at 14 units a day from x = 24 (the balance chart's margin),
so a day D sits at x = 24 + 14 * (D - 2026-07-01). The feed's lane is the third row down: the axis
is 34 high and the verification row 32, so its bar begins at y = 34 + 32 + 7 = 73.

  verdict   3 sources (feed, aggregator, export; typed entries are not a source that reaches),
            2 gaps to fill (the aggregator's 08-11 to 08-19, the export's 08-26 to 09-11),
            2 seams to check (the red one on 07-30, the amber one on 09-20),
            3 things to look at (agreement held back from 09-28, the unreproduced balance on
            09-28, the aggregator's missing row on 08-25).
  feed bar  from x(07-01) = 24 to the end of 08-20 at 9/24 of a day: 24 + 50 * 14 + 14 * 9/24
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from coverage_timeline_serve import served
from coverage_timeline_world import AGGREGATOR, EXPORTS, TODAY, build_household

PER_DAY = 14.0
LEFT = 24.0
FEED_Y = "73.0"

PRIVATE = (
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet",
    "Kilo", "Quince", "Lima", "Mike", "November", "Oscar", "Grocer", "Fuel", "Cafe", "Shop",
    "925.00", "1000.00", "792.00", "745.00", "700.00", "10.00", "26.00",
)


def x_of(day: str) -> float:
    return LEFT + PER_DAY * (date.fromisoformat(f"2026-{day}") - date(2026, 7, 1)).days


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db = build_household(tmp_path_factory.mktemp("page"))
    with served(db, TODAY) as address:
        yield address


@pytest.fixture(scope="module")
def page(base: str) -> str:
    return httpx.get(f"{base}/coverage-timeline?ref=main", timeout=60).text


def svg_of(text: str) -> str:
    return text[text.index("<svg class=\"cov-svg\" role=\"img\"") : text.index("</svg>") + 6]


def rects(svg: str, cls: str) -> list[dict[str, str]]:
    return [
        dict(re.findall(r'(\w+)="([^"]*)"', tag))
        for tag in re.findall(rf'<rect class="{cls}"[^>]*>', svg)
    ]


class TestPage:
    def test_Page_ForAKnownAccount_LoadsWithItsVerdictCountedByHand(self, page):
        assert (
            "3 sources, 2026-07-01 to 2026-10-05: 2 gaps to fill, 2 seams to check, "
            "3 things to look at."
        ) in page

    def test_Page_ForAnUnknownAccount_IsNotFound(self, base):
        assert httpx.get(f"{base}/coverage-timeline?ref=nobody", timeout=60).status_code == 404

    def test_Page_WithNoAccount_IsNotFound(self, base):
        assert httpx.get(f"{base}/coverage-timeline", timeout=60).status_code == 404

    def test_Page_HoldsNoAmountAndNoDescription_EvenInTitles(self, page):
        for private in PRIVATE:
            assert private not in page, private
        body = page.split("</style>", 1)[1]
        assert not re.search(r"\d[\d,]*\.\d\d(?![\d])", body)

    def test_Page_FetchListNamesTheDaysBeforeTheChart(self, page):
        before_chart = page[: page.index('class="cov-frame"')]
        assert "Aggregator: 2026-08-11 to 2026-08-19" in before_chart
        assert "Export file: 2026-08-26 to 2026-09-11" in before_chart


class TestAxisAndBars:
    def test_FeedBar_BeginsAtItsFirstDayAndEndsAtTheFractionOfItsLastDay(self, page):
        bar = next(r for r in rects(svg_of(page), "cov-bar") if r["y"] == FEED_Y)
        assert float(bar["x"]) == pytest.approx(x_of("07-01"), abs=0.1)
        expected_end = x_of("08-20") + PER_DAY * 9 / 24
        assert float(bar["x"]) + float(bar["width"]) == pytest.approx(expected_end, abs=0.2)

    def test_AggregatorBars_LeaveTheUnaskedDaysEmpty(self, page):
        bars = [r for r in rects(svg_of(page), "cov-bar") if r["y"] == "111.0"]
        assert len(bars) == 2
        first_end = float(bars[0]["x"]) + float(bars[0]["width"])
        assert first_end == pytest.approx(x_of("08-10") + PER_DAY * 13 / 24, abs=0.2)
        assert float(bars[1]["x"]) == pytest.approx(x_of("08-20"), abs=0.1)

    def test_ExportsLastDay_IsDrawnHollowBecauseItsTimeIsNotRecorded(self, page):
        hollow = rects(svg_of(page), "cov-bar-possible")
        assert any(float(r["x"]) == pytest.approx(x_of("08-25"), abs=0.1) for r in hollow)

    def test_ListedDays_AreMarkedOnTheLaneOnTheDaysRowsWereListed(self, page):
        # The listed mark sits along the bar's foot: 73 + 24 high - 5.
        listed = [r for r in rects(svg_of(page), "cov-listed") if r["y"] == "92.0"]
        starts = {round(float(r["x"]) - 0.5, 1) for r in listed}
        assert x_of("07-02") in starts
        assert x_of("07-20") not in starts

    def test_Axis_NamesTheMonthAtTheTopAndTheBottom(self, page):
        svg = svg_of(page)
        assert svg.count("Jul 2026") == 2
        assert svg.count("Aug 2026") == 2

    def test_Today_IsRuledAndNamed(self, page):
        svg = svg_of(page)
        assert 'class="cov-today"' in svg and ">today</text>" in svg

    def test_Bands_ArePaintedBeforeTheBarsTheyLieBehind(self, page):
        svg = svg_of(page)
        assert svg.index("cov-band-held") < svg.index('class="cov-bar"')


class TestMarksAndTheList:
    def test_EveryMark_LinksToAnEntryThatNamesItsDateAndLinksBack(self, page):
        marks = re.findall(r'<a href="#e-(\w+)" id="m-\1"><title>([^<]*)</title>', page)
        assert len(marks) == 7
        for ident, title in marks:
            entry = re.search(rf'<div class="cov-entry" id="e-{ident}"><p>([^<]*)</p>', page)
            assert entry is not None, ident
            assert entry.group(1).replace("&#x27;", "'") == title.replace("&#x27;", "'")
            assert f'href="#m-{ident}"' in page
            assert re.search(r"2026-\d\d-\d\d", entry.group(1))

    def test_RedSeam_IsSaidInCountsAndAnchoredToItsDay(self, page):
        assert (
            "1 row on 2026-07-30 is held from the bank feed and aggregator and not from this "
            "export file"
        ) in page

    def test_Entries_AreGroupedAsTheOwnerWouldAskForThem(self, page):
        order = [page.index(f"<h3>{t}</h3>") for t in ("What to fetch", "Where to look", "Seams")]
        assert order == sorted(order)

    def test_Key_ListsExactlyTheMarksDrawn(self, page):
        begins = page.index('class="cov-key"')
        key = page[begins : page.index("</ul>", begins)]
        for label in (
            "A seam where another source holds rows this capture lacks",
            "A seam with no other source to compare it with",
            "A known balance the rows do not reproduce",
            "Days to fill",
        ):
            assert label in key
        assert "Two sources state different balances" not in key
        assert "Due now" not in key
        assert "Stated edge" not in key


class TestWindow:
    def test_Window_RoundTripsThroughTheAddress(self, base):
        text = httpx.get(
            f"{base}/coverage-timeline?ref=main&window=d30&window_held=d30", timeout=60
        ).text
        assert "Window: 30 days ending 2026-10-05" in text
        assert "2026-09-06 to 2026-10-05" in text

    def test_Window_WhenTwoDatesAreGiven_ShowsThoseDays(self, base):
        text = httpx.get(
            f"{base}/coverage-timeline?ref=main&window=between&window_held=between"
            "&window_from=2026-07-25&window_to=2026-08-12",
            timeout=60,
        ).text
        assert "2026-07-25 to 2026-08-12" in text

    def test_Window_WhenRefused_DrawsNothingAndSaysWhy(self, base):
        text = httpx.get(
            f"{base}/coverage-timeline?ref=main&window=between&window_held=between"
            "&window_from=2026-08-12&window_to=2026-07-25",
            timeout=60,
        ).text
        assert 'class="cov-frame"' not in text
        assert "starts on 2026-08-12, which is after it ends" in text


class TestOppositeScenarios:
    def test_Page_WhenEveryGapIsFilledAndEverySeamCovered_SaysNothingNeedsDoing(self, tmp_path):
        filler = ("2026-08-21T12:00:00+00:00", "from=2026-08-10&to=2026-08-20", ("R6",))
        exports = (
            ("R1", "R2", "R3", "R3b"), ("R3b", "R4", "R5"), ("R5", "R6", "R7", "R8"),
            ("R8", "R9", "R10", "R12", "R13"), ("R13", "R14", "R15"),
        )
        db = build_household(tmp_path, exports=exports,
                             aggregator=(AGGREGATOR[0], filler, AGGREGATOR[1]))
        with served(db, TODAY) as address:
            text = httpx.get(f"{address}/coverage-timeline?ref=main", timeout=60).text
        assert "0 gaps to fill, 0 seams to check" in text
        assert 'class="cov-next"' not in text

    def test_Page_WhenOnlyTheExportSeamIsOverlapped_TheRedSeamGoes(self, tmp_path):
        exports = (EXPORTS[0], ("R3b", *EXPORTS[1]), *EXPORTS[2:])
        db = build_household(tmp_path, exports=exports)
        with served(db, TODAY) as address:
            text = httpx.get(f"{address}/coverage-timeline?ref=main", timeout=60).text
        assert "is held from the bank feed" not in text
        assert "1 seam to check" in text
        # The row is still in no export, and the page says so as a row to look at, which the
        # red seam had been saying for it.
        assert "1 row on 2026-07-30 is listed by another source" in text


def test_Fixture_Path_ReadsAsAPath() -> None:
    assert isinstance(Path("x"), Path)
