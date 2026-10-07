"""The collapsed timeline page, drawn from the long account of `test_coverage_timeline_quiet`.

Worked out by hand before the first run, for the window "everything" (2019-01-01 to 2026-10-05,
2,835 days) with the two quiet stretches found there (1,895 and 893 days):

  drawn days   2,835 - 1,895 - 893 = 47, at 14 units a day (a window that short is drawn at the
               readable scale), so the chart is 24 + 47 * 14 + 2 * 72 + 24 = 850 units wide.
  x of days    2019-01-11 is the last day before the first cut: 24 + 10 * 14 = 164, its end 178;
               the cut is 178 to 250; 2024-03-21 begins at 250. The second cut begins where the
               25 days after it end: 250 + 25 * 14 = 600, to 672. Today is the 47th drawn day:
               its rule is at 24 + 11 * 14 + 72 + 25 * 14 + 72 + 10 * 14 + 7 = 819.
  collapsed    2,788 days in two stretches, which is "7 years 7 months" counted from the window's
               start (2019-01-01 to 2026-08-19).
  expanded     One stretch opened leaves 1,942 drawn days, drawn to about 10,000 units (5.149 a
               day), so 48 + 10,000 + 72 = 10,120 units wide.
  every day    2,835 days at 3.65 units a day (a window that long is drawn to about 10,000 units
               and no less than 3.65 a day): 48 + 10,347.75 = 10,396 units wide.
"""

from __future__ import annotations

import re
from datetime import date

from obdi.pages.web_coverage_timeline import render_account_timeline
from test_coverage_timeline_quiet import LONG_GAP, LONG_RUNS, view

EVERYTHING = {"window": "all", "window_held": "all"}


def page(**more: object) -> str:
    return render_account_timeline(
        view(runs=LONG_RUNS, gaps=(LONG_GAP,)), fields=EVERYTHING, **more  # type: ignore[arg-type]
    ).decode()


def svg_width(text: str) -> str:
    found = re.search(r'<svg class="cov-svg" role="img"[^>]*viewBox="0 0 (\d+) ', text)
    assert found is not None
    return found.group(1)


class TestCollapsed:
    def test_Chart_IsAsWideAsItsDrawnDaysAndItsTwoBreaks(self):
        assert svg_width(page()) == "850"

    def test_TodayRule_SitsWhereTheMappingPutsItAfterBothBreaks(self):
        today = re.search(r'<line class="cov-today" x1="([\d.]+)"', page())
        assert today is not None
        assert float(today.group(1)) == 819.0

    def test_EachCut_IsAZigzagThroughTheWholeHeightAtTheEdgesOfItsStretch(self):
        cuts = re.findall(r'<polyline class="cov-break-cut" points="([^"]*)"', page())
        assert len(cuts) == 4
        assert {round(float(p.split(",")[0])) for p in cuts[0].split()} == {175, 181}
        assert {round(float(p.split(",")[0])) for p in cuts[1].split()} == {247, 253}
        assert cuts[0].split()[0].split(",")[1] == "0.0"

    def test_Labels_SayHowLongWasSkippedInTheChartAndInFullInTheTitleAndList(self):
        text = page()
        assert ">5 years<" in text and ">2 months<" in text
        assert ">2 years<" in text and ">5 months<" in text
        assert "2019-01-12 to 2024-03-20 - 5 years 2 months, nothing changed" in text
        assert "2024-04-15 to 2026-09-24 - 2 years 5 months, nothing changed" in text

    def test_Verdict_AndDescription_SayTheChartIsNotToScale(self):
        text = page()
        verdict = re.search(r'<p class="cov-verdict" data-verdict>([^<]*)</p>', text)
        assert verdict is not None
        assert "2 quiet stretches collapsed, 7 years 7 months in all" in verdict.group(1)
        assert "the chart is not to scale" in verdict.group(1)
        desc = re.search(r'<desc id="cov-d">([^<]*)</desc>', text)
        assert desc is not None
        assert "2 quiet stretches collapsed, 7 years 7 months in all" in desc.group(1)

    def test_TickLabels_NeverSitOnACut(self):
        cuts = [(175.0, 253.0), (597.0, 675.0)]
        found = re.findall(
            r'<text x="([\d.]+)" y="[\d.]+" font-size="11"[^>]*>([^<]*)</text>', page()
        )
        assert found
        for x, words in found:
            left = float(x)
            right = left + (62 if re.search(r"[A-Za-z]", words) else 16)
            for cut_left, cut_right in cuts:
                assert right < cut_left or left > cut_right, (words, x)


class TestExpanding:
    def test_EachCollapsedStretch_IsALinkThatRedrawsWithItInFull(self):
        links = re.findall(r'<a href="([^"]*expand=[^"]*)"><title>', page())
        assert len(links) == 2
        assert "expand=2019-01-12_2024-03-20" in links[0]

    def test_Expanded_DropsThatBreakOnlyAndWidensTheChartByWhatItHeld(self):
        text = page(expanded=[(date(2019, 1, 12), date(2024, 3, 20))])
        assert text.count('class="cov-break-cut"') == 2
        assert svg_width(text) == "10120"

    def test_ShowEveryDay_DrawsNoBreakAndSaysNothingWasCollapsed(self):
        text = page(every_day=True)
        assert "cov-break-" not in text.split("</style>", 1)[1]
        assert svg_width(text) == "10396"
        assert "quiet stretches collapsed" not in text
        assert "Collapse the quiet stretches" in text

    def test_Collapsing_IsOnByDefaultWithAnAffordanceToTurnItOff(self):
        text = page()
        assert "Show every day" in text and "days=all" in text


class TestWholeWindowQuiet:
    def test_Window_WhenNothingChangesInIt_IsASentenceAndAShortStripNotAChartOfBreak(self):
        text = render_account_timeline(
            view(runs=LONG_RUNS, gaps=(LONG_GAP,)),
            fields={"window": "between", "window_held": "between",
                    "window_from": "2020-01-01", "window_to": "2020-12-31"},
        ).decode()
        verdict = re.search(r'<p class="cov-verdict" data-verdict>([^<]*)</p>', text)
        assert verdict is not None
        assert verdict.group(1).startswith(
            "Nothing changed from 2020-01-01 to 2020-12-31 (1 year)"
        )
        assert "cov-break-" not in text.split("</style>", 1)[1]
        assert svg_width(text) == "244"
        assert "Collapse the quiet stretches" in text


def test_Window_WhenItIsOnlyAFewDays_HasNoBreaksAndNoCollapseLinks():
    text = render_account_timeline(
        view(runs=LONG_RUNS, gaps=(LONG_GAP,)),
        fields={"window": "between", "window_held": "between",
                "window_from": "2026-09-20", "window_to": "2026-10-05"},
    ).decode()
    assert "cov-break-" not in text.split("</style>", 1)[1]
    assert "Show every day" not in text
