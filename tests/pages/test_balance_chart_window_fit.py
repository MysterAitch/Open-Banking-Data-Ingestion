"""A window the reader chose is drawn at a readable scale, so it is the span he sees.

The owner's complaint about these charts was that they "cover too large a time period to be
able to detect small offsets"; his answer was a very wide chart to pan across. A window he
chose himself says "this is the span I want to look at", so its width follows the window.
The rule is `choose_scale`'s: units a day are `READABLE_PIXELS_PER_DAY` (14: measured over
three steps on consecutive days, where 8 overlaps the marks, 12 clears them but names no day,
and 14 is where the axis names a day a week), no less than fills `WINDOW_FIT_WIDTH` (250) and
no more than the same days as a range would be. The drawn width is 2 x 24 + days x per day.
Worked out by hand BEFORE the first run:

    days     per day                       width
    everything (no window)  3.65           2,738 x 3.65 + 48 = 10,042    (unchanged)
    1        202/1 = 202 (fills the 250)    250
    7        202/7 = 28.86                  250
    14       202/14 = 14.43                 250
    15       202/15 = 13.5, so 14           15 x 14 + 48 = 258
    30       14                             420 + 48 = 468
    90       14                             1,260 + 48 = 1,308
    365      14                             5,110 + 48 = 5,158
    1,000    14 would be 14,000, but the same days as a range are 10 a day: 10,048
    "wide" (the range's own width), 365 days: 10,048; 90 days: 10,048; 30 days: 240 x 30 +
    48 = 7,248.
    A range of the same 365 days (from/to), as always: 10,048.

At 390 px a page's chart column is about 280 units beside the figures' column, so 30 days is
about one and two-thirds screens and 90 about four and two-thirds: neither fits, which is the
cost of 14 a day; the masked strip is the view that fits.

The axis names the month and year of a window that begins mid-month (no month tick falls at
its left edge) by naming its first day in full, and leaves out a day label too near it.
"""

from __future__ import annotations

import re
from datetime import date
from itertools import pairwise

import httpx
import pytest

import invented_balance_account as inv
from obdi.pages.web_balance_chart import (
    READABLE_PIXELS_PER_DAY,
    WINDOW_FIT_WIDTH,
    choose_scale,
    render_balance_chart,
)
from test_balance_chart_window import REF, TODAY, served, window_page, words  # noqa: F401

FIRST, LAST = inv.FIRST, inv.LAST


def drawn_width(page: str) -> int:
    found = re.search(r'<svg role="img" aria-labelledby="bc-values-t[^>]*width="(\d+)"', page)
    assert found
    return int(found.group(1))


def chart_texts(page: str) -> list[tuple[float, float, str]]:
    svg = re.search(r'<svg role="img" aria-labelledby="bc-values-t.*?</svg>', page, re.S)
    assert svg
    return [
        (float(x), float(y), text)
        for x, y, text in re.findall(
            r'<text x="([\d.]+)" y="([\d.]+)"[^>]*>([^<]*)</text>', svg.group(0)
        )
    ]


def last_n_days(n: int) -> dict[str, str]:
    return {"window": "other", "window_count": str(n), "window_unit": "days"}


WIDTHS = [
    (1, 250),
    (7, 250),
    (14, 250),
    (15, 258),
    (30, 468),
    (90, 1_308),
    (365, 5_158),
    (1_000, 10_048),
]


class TestAWindowIsDrawnAtAReadableScale:
    @pytest.mark.parametrize(("days", "width"), WIDTHS)
    def test_Window_OfThatManyDays_IsDrawnThatWide(self, days, width):
        page = window_page(unmasked=True, **last_n_days(days))

        assert drawn_width(page) == width

    def test_Everything_IsDrawnAsItAlwaysWas(self):
        page = render_balance_chart(inv.invented_chart(), unmasked=True, today=TODAY).decode()

        assert drawn_width(page) == 10_042
        assert "at 3.65 pixels a day" in words(page)

    def test_AWindowOfOneYear_SaysItIsDrawnAtFourteenAUnitsADay(self):
        assert "at 14.00 pixels a day" in words(window_page(unmasked=True, window="m12"))

    def test_ARangeOfTheSameDays_IsStillStretchedToTheTargetWidth(self):
        ranged = render_balance_chart(
            inv.invented_chart(), unmasked=True, start=date(2025, 7, 1), end=date(2026, 6, 30),
            today=TODAY,
        ).decode()

        assert drawn_width(ranged) == 10_048
        assert "at 27.40 pixels a day" in words(ranged)
        assert "data-scale-switch" not in ranged

    @pytest.mark.parametrize(("days", "width"), [(365, 10_048), (90, 10_048), (30, 7_248)])
    def test_WideWindow_IsDrawnAsWideAsTheSameDaysAsARangeWould_ToPanAcross(self, days, width):
        page = render_balance_chart(
            inv.invented_chart(), unmasked=True, window_fields=last_n_days(days), today=TODAY,
            wide=True,
        ).decode()

        assert drawn_width(page) == width

    def test_WideWindow_IsDrawnExactlyAsTheRangeOfTheSameDaysIs(self):
        wide = render_balance_chart(
            inv.invented_chart(), unmasked=True, window_fields={"window": "m12"}, today=TODAY,
            wide=True,
        ).decode()
        ranged = render_balance_chart(
            inv.invented_chart(), unmasked=True, start=date(2025, 7, 1), end=date(2026, 6, 30),
            today=TODAY,
        ).decode()

        def drawing(html: str) -> str:
            found = re.search(r'<svg role="img" aria-labelledby="bc-values-t.*?</svg>', html, re.S)
            assert found
            return found.group(0)

        assert drawing(wide) == drawing(ranged)

    @pytest.mark.parametrize("unmasked", [False, True])
    def test_PageWithNoWindow_IsTheSameBytesWhateverTheWideSwitchSays(self, unmasked):
        plain = render_balance_chart(inv.invented_chart(), unmasked=unmasked, today=TODAY)
        wide = render_balance_chart(
            inv.invented_chart(), unmasked=unmasked, today=TODAY, wide=True
        )
        year = {"start": date(2022, 1, 1), "end": date(2022, 12, 31)}

        assert plain == wide
        assert render_balance_chart(
            inv.invented_chart(), unmasked=unmasked, today=TODAY, **year
        ) == render_balance_chart(
            inv.invented_chart(), unmasked=unmasked, today=TODAY, wide=True, **year
        )

    def test_Scale_ForAWindow_IsNeverWiderThanTheSameDaysAsARange(self):
        for days in (1, 5, 40, 41, 100, 700, 2_000, 2_738):
            end = date.fromordinal(FIRST.toordinal() + days - 1)
            ranged = choose_scale(FIRST, LAST, FIRST, end)
            fitted = choose_scale(FIRST, LAST, FIRST, end, fitted=True)

            assert fitted.width <= max(ranged.width, WINDOW_FIT_WIDTH) + 1e-9, days

    def test_Scale_ForAWindowOfAYear_IsTheReadableWidthAndNoLess(self):
        scale = choose_scale(FIRST, LAST, date(2025, 7, 1), date(2026, 6, 30), fitted=True)

        assert scale.per_day == READABLE_PIXELS_PER_DAY
        assert scale.fitted is True


class TestTheTwoWaysToDrawAWindow:
    def test_MaskedPageOfAWindow_OffersValuesFittedAndValuesVeryWideInOneForm(self):
        page = window_page(window="m12")

        (form,) = re.findall(r'<form[^>]*target="_blank".*?</form>', page, re.S)
        assert "Show values, fitted to the window (opens in a new tab)" in form
        assert "Show values, very wide to pan across (opens in a new tab)" in form
        assert form.count('name="wide"') == 1

    def test_MaskedPageOfARange_OffersTheOneButtonItAlwaysDid(self):
        page = render_balance_chart(
            inv.invented_chart(), unmasked=False, start=date(2022, 1, 1), end=date(2022, 12, 31),
            today=TODAY,
        ).decode()

        (form,) = re.findall(r'<form[^>]*target="_blank".*?</form>', page, re.S)
        assert "Show values (opens in a new tab)" in form
        assert 'name="wide"' not in form

    def test_FittedValuesPage_OffersTheWideDrawingInANewTab(self):
        page = window_page(unmasked=True, window="m12")

        (switch,) = re.findall(r"<form[^>]*data-scale-switch.*?</form>", page, re.S)
        assert 'target="_blank"' in switch
        assert "Draw this window very wide to pan across (opens in a new tab)" in switch
        assert 'name="wide" value="1"' in switch
        assert 'name="window" value="m12"' in switch

    def test_WideValuesPage_OffersTheFittedDrawingBack(self):
        page = render_balance_chart(
            inv.invented_chart(), unmasked=True, window_fields={"window": "m12"}, today=TODAY,
            wide=True,
        ).decode()

        (switch,) = re.findall(r"<form[^>]*data-scale-switch.*?</form>", page, re.S)
        assert "Fit this window to the screen (opens in a new tab)" in switch
        assert 'name="wide"' not in switch

    def test_ValuesPageOfEverything_HasNoSwitchSinceItIsNotAWindow(self):
        page = render_balance_chart(inv.invented_chart(), unmasked=True, today=TODAY).decode()

        assert "data-scale-switch" not in page

    def test_Post_WithWide_AnswersTheWindowVeryWideAndWithoutItFitted(self, served):  # noqa: F811
        fields = {"ref": REF, "window": "m12", "window_held": "m12"}

        fitted = httpx.post(f"{served}/balance-chart", data=fields)
        wide = httpx.post(f"{served}/balance-chart", data={**fields, "wide": "1"})

        assert (drawn_width(fitted.text), drawn_width(wide.text)) == (5_158, 10_048)
        assert "no-store" in wide.headers["cache-control"]

    def test_Post_WithWideButNoWindow_IsIgnored(self, served):  # noqa: F811
        page = httpx.post(f"{served}/balance-chart", data={"ref": REF, "wide": "1"})

        assert drawn_width(page.text) == 10_042

    def test_Get_WithWide_NeverMakesTheMaskedPageDrawAnything(self, served):  # noqa: F811
        page = httpx.get(
            f"{served}/balance-chart", params={"ref": REF, "window": "m12", "wide": "1"}
        )

        assert "bc-values-t" not in page.text and "£" not in page.text


AXIS_WINDOWS = {
    "1 day": ("2022-04-10", "2022-04-10"),
    "7 days": ("2022-04-10", "2022-04-16"),
    "7 days over a month's end": ("2022-04-28", "2022-05-04"),
    "30 days from a 1st": ("2022-04-01", "2022-04-30"),
    "30 days from mid-month": ("2022-04-10", "2022-05-09"),
    "90 days": ("2022-02-01", "2022-05-01"),
    "365 days": ("2021-07-01", "2022-06-30"),
}


class TestTheAxisOfAWindowNamesDaysAndMonthsWithoutCollision:
    def page_of(self, name: str) -> str:
        first, last = AXIS_WINDOWS[name]
        return window_page(
            unmasked=True, window="between", window_from=first, window_to=last
        )

    @pytest.mark.parametrize("name", list(AXIS_WINDOWS))
    def test_Axis_OfEachWindow_HasNoTwoLabelsOnOneRowCloserThanTheyAreWide(self, name):
        texts = chart_texts(self.page_of(name))

        by_row: dict[float, list[tuple[float, str]]] = {}
        for x, y, text in texts:
            by_row.setdefault(y, []).append((x, text))
        for row in by_row.values():
            row.sort()
            for (left, text), (right, _) in pairwise(row):
                assert right - left >= 5.2 * len(text), (name, text, left, right)

    @pytest.mark.parametrize("name", list(AXIS_WINDOWS))
    def test_Axis_OfEachWindow_NamesTheMonthAndYearItBeginsIn(self, name):
        first = date.fromisoformat(AXIS_WINDOWS[name][0])
        labels = {text for _, _, text in chart_texts(self.page_of(name))}

        named = f"{first:%b} {first.year}" if first.day == 1 else (
            f"{first.day} {first:%b} {first.year}"
        )
        assert named in labels

    def test_Axis_OfAWindowFromMidMonth_LeavesOutTheDayLabelTooNearTheNamedFirstDay(self):
        labels = [text for _, _, text in chart_texts(self.page_of("30 days from mid-month"))]

        assert "10 Apr 2022" in labels
        assert "15" not in labels
        assert "22" in labels

    def test_Axis_OfAWindowFromA1st_NamesNoFirstDayInFullBecauseTheMonthTickDoesIt(self):
        labels = [text for _, _, text in chart_texts(self.page_of("30 days from a 1st"))]

        assert "Apr 2022" in labels
        assert not [text for text in labels if text.startswith("1 Apr")]

    def test_Axis_OfARange_IsNotGivenAFullFirstDayAsItNeverWas(self):
        page = render_balance_chart(
            inv.invented_chart(), unmasked=True, start=date(2022, 4, 10), end=date(2022, 5, 9),
            today=TODAY,
        ).decode()

        assert not [t for _, _, t in chart_texts(page) if "Apr 2022" in t and t[0].isdigit()]
