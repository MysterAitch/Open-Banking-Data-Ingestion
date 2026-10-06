"""How long a range of dates is, written after the range on a page, quietly.

Known answers decided before the first run: a chosen window of 2026-02-01 to 2026-02-28 is 28
days, so "a month"; a served page never carries a raw mark (a private-use character), whatever
text it was handed; and the words are never bold or orange (`age`, `warn`) because they are an
affordance and not a request.
"""

from __future__ import annotations

from datetime import date

from obdi.callback import render_page
from obdi.page_times import SPAN_CLOSE, SPAN_OPEN, range_with_span
from obdi.stylesheet import SERVED_STYLESHEET
from obdi.window_control import window_choice, window_controls
from page_dom import elements, parse

TODAY = date(2026, 10, 4)


class TestTheChosenWindowHeading:
    def test_WindowNow_WhenTwoDatesAreChosen_SaysTheirLengthInAMutedSpan(self):
        choice = window_choice(
            {"window": "between", "window_from": "2026-02-01", "window_to": "2026-02-28"},
            today=TODAY,
            held_from=date(2019, 1, 5),
        )
        root = parse(window_controls(choice, today=TODAY.isoformat()))

        now = next(e for e in elements(root, "p") if "window-now" in e.classes)
        assert now.text() == "Window: From 2026-02-01 to 2026-02-28 (a month)"
        assert [s.text() for s in elements(now, "span") if "span-words" in s.classes] == [
            "(a month)"
        ]

    def test_WindowNow_WhenALengthIsChosen_AddsNothing(self):
        choice = window_choice({"window": "m12"}, today=TODAY, held_from=date(2019, 1, 5))
        root = parse(window_controls(choice, today=TODAY.isoformat()))

        now = next(e for e in elements(root, "p") if "window-now" in e.classes)
        assert not [s for s in elements(now, "span") if "span-words" in s.classes]


class TestTheServedPage:
    def test_RenderPage_WhenTextCarriesAMarkedLength_ServesTheSpanAndNoRawMark(self):
        body = f"<p>Held {range_with_span(date(2026, 7, 11), date(2026, 8, 10))}.</p>"

        served = render_page("A page", body).decode()

        assert 'Held 2026-07-11 to 2026-08-10 <span class="span-words">(a month)</span>.' in served
        assert SPAN_OPEN not in served
        assert SPAN_CLOSE not in served

    def test_Stylesheet_ForTheLengthWords_IsMutedSmallAndNeverBold(self):
        lines = (line.lstrip() for line in SERVED_STYLESHEET.splitlines())
        rule = next(line for line in lines if line.startswith(".span-words"))

        assert "var(--ink-2)" in rule
        assert "var(--text-sm)" in rule
        assert "font-weight: 400" in rule
        assert "var(--warn)" not in rule
