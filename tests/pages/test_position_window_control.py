"""Choosing the chart's window: the control, what it asks, and what it refuses.

The window is a second choice of the kind the account ticks already are: a view, stored
nowhere, riding in the POST that asks for values and in no address. The household and its
figures are `position_window_household` (today 2026-10-04); what is asked of the control
here is worked out BEFORE the first run:

    tap                                   what the page must say
    "Last 12 months"                      12 months ending 2026-10-04: 2025-10-05 to
                                          2026-10-04, one figure per week
    "This tax year"                       This tax year: 2026-04-06 to 2026-10-04, ...
    open "More windows", then "Last
      calendar year"                      held until 2024-03-15 so 2025-01-01 to 2025-12-31,
                                          one figure per week (364 days)
    from 2021-11-01 to 2022-01-31         before anything is held (2024-03-15): no chart
    from 2026-02-01 to 2026-01-31         refused: starts on 2026-02-01, after it ends on
                                          2026-01-31
    length "abc"                          refused: the length must be a whole number
    length 0, -3                          refused: at least 1
    6 weeks ending on 2026-09-30          2026-08-20 to 2026-09-30 (42 days), per day
    Enter in a field, or "Redraw"         the last valid window again
"""

from __future__ import annotations

import re
from html import unescape

import httpx
import pytest

from obdi.ingest.store import Store
from obdi.pages.web_position import WINDOW_FIELDS, render_position, window_choice
from obdi.read.position import Position, read_position
from position_window_household import CARD, TODAY, window_household
from test_position_chart_window import CHART, polylines
from test_position_page import serve

FORM = re.compile(r'<form method="post" action="/position">.*?</form>', re.S)


@pytest.fixture
def held(tmp_path) -> Position:
    with Store(tmp_path / "w.sqlite3") as store:
        window_household(store)
        return read_position(store, today=TODAY)


def asked(position: Position, **fields: str) -> str:
    return unescape(render_position(position, unmasked=True, window_fields=fields).decode())


def form_of(page: str) -> str:
    found = [f for f in FORM.findall(page) if 'name="window"' in f]
    assert len(found) == 1, "the window's controls are in exactly one form"
    return found[0]


class TestTheControl:
    def test_MaskedPage_OffersTheWindowInTheSameFormAsTheTicksAndDrawsNoChart(self, held):
        page = render_position(held, unmasked=False).decode()

        form = form_of(page)
        assert 'name="chart_in"' in form
        assert "<svg" not in page
        assert "Choosing a window shows values" in page

    def test_Page_OffersFourWindowsInOneTapAndTheRestUnderAFold(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        first, rest = form.split('<details class="window-more"')
        for label in ("Everything", "Last 90 days", "Last 12 months", "This tax year"):
            assert f">{label}</button>" in first
        for label in (
            "Last 30 days", "Last 3 months", "Last 6 months", "Last 24 months", "This month",
            "Last month", "This quarter", "Last quarter", "This calendar year",
            "Last calendar year", "Calendar year to date", "Last tax year", "Tax year to date",
        ):
            assert f">{label}</button>" in rest

    def test_EveryChipIsASubmitButtonThatNamesItsOwnWindow(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        chips = re.findall(
            r'<button type="submit" name="window" value="([^"]+)" class="window-chip"', form
        )
        assert len(chips) == 22 and len(set(chips)) == 22

    def test_TheFirstButtonInTheForm_KeepsTheWindowSoABareEnterNeverPicksAnother(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        first_button = re.search(r"<button[^>]*>", form)
        assert first_button is not None
        assert 'value="keep"' in first_button.group(0)
        assert 'tabindex="-1"' in first_button.group(0)

    @pytest.mark.parametrize(
        ("key", "words"),
        [
            ("this-week", "This week: 2026-09-28 to 2026-10-04, one figure per day."),
            ("last-week", "Last week: 2026-09-21 to 2026-09-27, one figure per day."),
            ("last-4-weeks", "Last 4 whole weeks: 2026-08-31 to 2026-09-27, one figure per day."),
        ],
    )
    def test_Page_ForAWeekPeriodOnASunday_NamesItsMondayToSundayDays(self, held, key, words):
        page = asked(held, window=key)

        assert words in page
        assert re.findall(r'value="([^"]+)" class="window-chip" aria-pressed="true"', page) == [
            key
        ]

    def test_Page_SetsTheControlsToTheWindowInForceAndMarksItsChip(self, held):
        page = asked(held, window="m12")

        pressed = re.findall(r'value="([^"]+)" class="window-chip" aria-pressed="true"', page)
        assert pressed == ["m12"]
        assert 'name="window_held" value="m12"' in page

    def test_Page_WithADefaultChoice_MarksEverythingAndOpensNothing(self, held):
        page = render_position(held, unmasked=True).decode()

        assert re.findall(r'value="([^"]+)" class="window-chip" aria-pressed="true"', page) == [
            "all"
        ]
        assert '<details class="window-more">' in page

    def test_TheMoreWindowsFold_IsOpenWhenTheWindowInForceIsInsideIt(self, held):
        assert '<details class="window-more" open>' in asked(held, window="last-tax")
        assert '<details class="window-more" open>' in asked(
            held, window="between", window_from="2026-09-01", window_to="2026-09-10"
        )
        assert '<details class="window-more">' in asked(held, window="d90")

    def test_TheTicks_AreFoldedAndTheirSummarySaysWhatIsDrawn(self, held):
        page = render_position(held, unmasked=True).decode()

        assert re.search(r"<summary>Drawn from everything \(6 items\)</summary>", page)
        narrowed = render_position(
            held, unmasked=True, chart_in={i.key for i in held.chart_items} - {CARD}
        ).decode()
        assert "<summary>Drawn from 5 of 6 items</summary>" in narrowed

    def test_EveryInputHasALabelItIsAssociatedWith(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        for name in (
            "window_count", "window_unit", "window_anchor", "window_on", "window_from", "window_to",
        ):
            assert f'<label for="{name}">' in form
            assert f'id="{name}" name="{name}"' in form

    def test_DateFieldsAreDatePickersWithATypedFallback(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        for name in ("window_on", "window_from", "window_to"):
            tag = re.search(rf'<input id="{name}"[^>]*>', form)
            assert tag is not None
            assert 'type="date"' in tag.group(0)
            assert 'pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}"' in tag.group(0)
            assert 'placeholder="2026-10-04"' in tag.group(0)

    def test_TheLengthFieldIsANumericKeypadThatStillAcceptsAWord(self, held):
        form = form_of(render_position(held, unmasked=True).decode())

        tag = re.search(r'<input id="window_count"[^>]*>', form)
        assert tag is not None
        assert 'type="text"' in tag.group(0) and 'inputmode="numeric"' in tag.group(0)

    def test_NoWindowFieldCarriesAValueThatCouldBeAFigure(self, held):
        page = render_position(held, unmasked=False).decode()

        assert "2026-10-04" in page  # the day the window is read as of is structure
        assert not re.search(r"£\d", form_of(page))


class TestWhatEachTapAsks:
    def test_Last12Months_DrawsAWeeklyChartForTheYearToToday(self, held):
        page = asked(held, window="m12")

        assert (
            "12 months ending 2026-10-04: 2025-10-05 to 2026-10-04, one figure per week." in page
        )
        assert "One figure per week." in page

    def test_ThisTaxYear_DrawsFromTheSixthOfAprilToToday(self, held):
        page = asked(held, window="this-tax")

        assert "This tax year: 2026-04-06 to 2026-10-04, one figure per week." in page

    def test_LastCalendarYear_AfterOpeningTheFold_IsHeldOnlyFromWhenAnythingIsHeld(self, held):
        page = asked(held, window="last-year")

        assert "Last calendar year: 2025-01-01 to 2025-12-31, one figure per week." in page
        assert "The period runs to" not in page

    def test_Last90Days_IsDaily(self, held):
        page = asked(held, window="d90")

        assert "90 days ending 2026-10-04: 2026-07-07 to 2026-10-04, one figure per day." in page

    def test_Last3Years_IsMonthlyAndSaysOneFigurePerMonthEnd(self, held):
        page = asked(held, window="other", window_count="3", window_unit="years")

        assert "one figure per month-end." in page

    def test_BetweenTwoDates_FromBeforeAnythingIsHeld_DrawsNoChartAndSaysWhy(self, held):
        page = asked(held, window="between", window_from="2021-11-01", window_to="2022-01-31")

        assert "<svg role=" not in page.replace("<svg width", "")
        assert "before anything is held" in page
        assert "No chart is drawn." in page

    def test_BetweenTwoDates_InsideWhatIsHeld_DrawsTheDaysBetweenThem(self, held):
        page = asked(held, window="between", window_from="2026-09-10", window_to="2026-09-20")

        assert (
            "From 2026-09-10 to 2026-09-20: 2026-09-10 to 2026-09-20, one figure per day." in page
        )
        assert len(re.findall(r'data-tick="(?:day|week|month)"', page)) == 11

    def test_ALengthEndingOnADay_RunsBackFromThatDay(self, held):
        page = asked(
            held, window="other", window_count="6", window_unit="weeks",
            window_anchor="ending-on", window_on="2026-09-30",
        )

        assert "6 weeks ending 2026-09-30: 2026-08-20 to 2026-09-30, one figure per day." in page

    def test_ALengthStartingOnADay_RunsForwardFromThatDay(self, held):
        page = asked(
            held, window="other", window_count="3", window_unit="days",
            window_anchor="starting-on", window_on="2026-09-12",
        )

        assert "3 days starting 2026-09-12: 2026-09-12 to 2026-09-14, one figure per day." in page

    def test_KeepingTheWindow_DrawsTheLastValidChoiceAgain(self, held):
        keep = asked(held, window="keep", window_held="this-tax")
        chosen = asked(held, window="this-tax", window_held="all")

        assert re.findall(r"<p data-window-words>.*?</p>", keep) == re.findall(
            r"<p data-window-words>.*?</p>", chosen
        )

    def test_KeepingAnUnknownHeldWindow_FallsBackToEverythingAndDoesNotFail(self, held):
        page = asked(held, window="keep", window_held="no-such-window")

        assert "data-window-words" not in page
        assert "<svg" in page

    def test_ANameThatIsNoWindow_IsTakenAsKeepingAndNeverAsAChoice(self, held):
        page = asked(held, window="d90; drop table", window_held="m12")

        assert "12 months ending 2026-10-04" in page

    def test_Everything_IsTheDefaultChartAndNotAWindow(self, held):
        assert "data-window-words" not in asked(held, window="all")

    def test_TheWindowAndTheTicks_ComposeSoALeftOutLiabilityStaysOutOfAWindowedChart(self, held):
        keys = {i.key for i in held.chart_items} - {CARD}

        with_dip = render_position(held, unmasked=True, window_fields={"window": "d90"}).decode()
        without = render_position(
            held, unmasked=True, chart_in=keys, window_fields={"window": "d90"}
        ).decode()

        assert "data-below-nil" in with_dip
        assert "data-below-nil" not in without
        assert "The chart leaves out: <code>card</code>" in without
        assert "One figure per day." in without

    def test_AWindowOverTheDip_AndOneThatEndsBeforeIt_DifferInWhatIsBelowNil(self, held):
        inside = asked(held, window="between", window_from="2026-09-01", window_to="2026-09-30")
        before = asked(held, window="between", window_from="2026-08-01", window_to="2026-09-10")

        assert "data-below-nil" in inside
        assert "data-below-nil" not in before

    def test_ThePointsDrawn_AreTheSameWhetherTheWindowWasTappedOrTypedAsDates(self, held):
        tapped = asked(held, window="d30")
        typed = asked(
            held, window="between", window_from="2026-09-05", window_to="2026-10-04"
        )

        def points(page: str) -> list[list[tuple[float, float]]]:
            return polylines(CHART.search(page).group(0), "known")

        assert points(tapped) == points(typed)


class TestWhatIsRefused:
    def test_FromAfterTo_IsRefusedInASentenceBesideTheDatesAndDrawsNoChart(self, held):
        page = asked(held, window="between", window_from="2026-02-01", window_to="2026-01-31")

        assert (
            "The window starts on 2026-02-01, which is after it ends on 2026-01-31." in page
        )
        between = re.search(r'<fieldset class="window-between">.*?</fieldset>', page, re.S)
        assert between and "data-window-refused" in between.group(0)
        assert "<svg role=" not in page
        assert "no chart is drawn from a choice that was refused" in page

    def test_ARefusedChoice_KeepsWhatWasTypedAndTheLastValidWindow(self, held):
        page = asked(
            held, window="between", window_from="2026-02-01", window_to="2026-01-31",
            window_held="m12",
        )

        assert 'value="2026-02-01"' in page and 'value="2026-01-31"' in page
        assert 'name="window_held" value="m12"' in page
        assert '<details class="window-more" open>' in page

    def test_ANonNumber_IsRefusedAndNothingIsEchoedAsMarkup(self, held):
        raw = render_position(
            held, unmasked=True,
            window_fields={"window": "other", "window_count": '<script>alert(1)</script>'},
        ).decode()

        assert "The length must be a whole number, such as 12." in raw
        assert "<script>" not in raw
        assert "<svg role=" not in raw

    @pytest.mark.parametrize("count", ["0", "-3"])
    def test_AZeroOrNegativeLength_IsRefusedWithTheWindowsOwnSentence(self, held, count):
        page = asked(held, window="other", window_count=count)

        assert "A length must be a whole number of at least 1." in page
        assert "<svg role=" not in page

    def test_ABlankLength_IsRefused(self, held):
        assert "The length must be a whole number" in asked(held, window="other", window_count="")

    def test_AnAbsurdLength_IsClippedToWhatIsHeldNotRefused(self, held):
        page = asked(held, window="other", window_count="99999999999999999999", window_unit="years")

        assert "data-window-refused" not in page
        assert "Nothing is held before 2024-03-15, so the window starts there." in page

    def test_AnEndingOnLengthWithoutADay_IsRefusedAndSaysWhichDay(self, held):
        page = asked(held, window="other", window_count="3", window_anchor="ending-on")

        assert "Give the day the window ends on." in page

    def test_AnUnknownUnit_IsRefusedInASentence(self, held):
        page = asked(held, window="other", window_count="3", window_unit="fortnights")

        assert "Choose days, weeks, months, or years." in page

    def test_ADateThatIsNoDate_IsRefusedInASentence(self, held):
        page = asked(held, window="between", window_from="2026-02-30", window_to="2026-03-01")

        assert "Give each day as year-month-day, such as 2026-10-04." in page

    def test_OneDateBlank_IsRefused(self, held):
        page = asked(held, window="between", window_from="2026-02-01", window_to="")

        assert "Give both days" in page

    def test_ARefusedChoice_ShowsNoWordsAboutAWindowAndNoNowLine(self, held):
        page = asked(held, window="other", window_count="0")

        assert "data-window-words" not in page
        assert "data-window-now" not in page

    def test_AValidChoiceAfterARefusedOne_DrawsTheChartAgain(self, held):
        refused = asked(held, window="other", window_count="0", window_held="all")
        fixed = asked(held, window="other", window_count="30", window_unit="days")

        assert "<svg role=" not in refused
        assert "<svg role=" in fixed

    def test_TheTypedValuesAreTruncatedSoAHugeFieldCannotBloatThePage(self, held):
        page = asked(held, window="other", window_count="7" * 5000)

        assert len(page) < 400_000
        assert "7" * 100 not in page


class TestWhereTheChoiceTravels:
    @pytest.fixture
    def routes(self, tmp_path, monkeypatch):
        httpd, lab = serve(tmp_path, monkeypatch, window_household)
        try:
            yield lab
        finally:
            httpd.shutdown()

    def post(self, lab, **data: str) -> httpx.Response:
        return httpx.post(
            f"{lab.base}/position", data=data, follow_redirects=False, timeout=30
        )

    def test_Post_WithAWindow_AnswersDirectlyNoStoreAndWithoutARedirect(self, routes):
        response = self.post(routes, window="between", window_from="2026-09-10",
                             window_to="2026-09-20")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "Location" not in response.headers
        assert "From 2026-09-10 to 2026-09-20" in unescape(response.text)

    def test_Post_WithARefusedWindow_AnswersNoStoreAndDrawsNoChart(self, routes):
        response = self.post(routes, window="other", window_count="0")

        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "<svg role=" not in response.text
        assert "at least 1" in response.text

    def test_Post_WithTheWindowAndTheTicks_ReadsBoth(self, routes):
        response = self.post(
            routes, window="between", window_from="2026-09-10", window_to="2026-09-20",
            chart_chosen="1", chart_in="account:everyday",
        )

        assert "The chart leaves out" in response.text
        assert "From 2026-09-10 to 2026-09-20" in unescape(response.text)

    def test_Post_WithNoWindow_DrawsTheChartAsBefore(self, routes):
        response = self.post(routes)

        assert "<svg role=" in response.text
        assert "data-window-words" not in response.text

    def test_Get_WithTheWindowsFieldNamesInItsQueryString_DrawsNothingAndDisclosesNothing(
        self, routes
    ):
        plain = httpx.get(f"{routes.base}/position", timeout=30)
        sneaky = httpx.get(
            f"{routes.base}/position",
            params=dict.fromkeys(WINDOW_FIELDS, "2026-09-10") | {"window": "d90"},
            timeout=30,
        )

        assert sneaky.status_code == 200
        assert sneaky.text == plain.text
        assert "<svg role=" not in sneaky.text
        assert "data-window-words" not in sneaky.text
        for figure in ("7,050.00", "705000", "-2,500.00", "3,200.00"):
            assert figure not in sneaky.text

    def test_Get_OffersTheControlsAndNoWindowInTheAddress(self, routes):
        page = httpx.get(f"{routes.base}/position", timeout=30).text

        assert 'name="window"' in page
        assert 'method="post" action="/position"' in page
        assert "?window" not in page and "&window" not in page

    def test_Post_DoesNotLogTheWindowOrRedirectToAddressHoldingIt(self, routes, capfd):
        response = self.post(routes, window="between", window_from="2026-09-10",
                             window_to="2026-09-20")
        captured = capfd.readouterr()

        assert "window_from" not in captured.out + captured.err
        assert "2026-09-10" not in captured.out + captured.err
        assert str(response.url) == f"{routes.base}/position"


class TestTheChoiceAsData:
    def test_WindowChoice_ForEachPreset_ResolvesToAWindowOrEverything(self, held):
        for key in ("all", "d30", "d90", "m3", "m6", "m12", "m24", "this-month", "last-month",
                    "this-quarter", "last-quarter", "this-year", "last-year", "year-to-date",
                    "this-tax", "last-tax", "tax-to-date"):
            choice = window_choice(held, {"window": key})

            assert choice.refusal == "" and choice.held == key
            assert (choice.spec is None) == (key == "all")
