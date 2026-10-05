"""The balance chart over a window of days, chosen with the Position page's own control.

The account is `invented_balance_account` (known balances every day from 2019-01-01 to
2026-06-30; 39 changes in the difference, decided in that module). Today is passed in and is
2026-06-30, a Tuesday, unless a test says otherwise. What each window must hold was worked
out by hand from that module's change list BEFORE the first run:

    key           window asked                   days shown                  changes
    d30           30 days ending today           2026-06-01 to 2026-06-30        0
    d90           90 days                        2026-04-02 to 2026-06-30        1  (05-20)
    m3            3 months                       2026-03-31 to 2026-06-30        1  (05-20)
    m6            6 months                       2025-12-31 to 2026-06-30        2  (02-03, 05-20)
    m12           12 months                      2025-07-01 to 2026-06-30        4  (1 pair)
    m24           24 months                      2024-07-01 to 2026-06-30        5
    this-week     Monday to today                2026-06-29 to 2026-06-30        0
    last-week     the last whole week            2026-06-22 to 2026-06-28        0
    last-4-weeks  four whole weeks               2026-06-01 to 2026-06-28        0
    this-month    the month so far               2026-06-01 to 2026-06-30        0
    last-month    May                            2026-05-01 to 2026-05-31        1
    this-quarter  April to June                  2026-04-01 to 2026-06-30        1
    last-quarter  January to March               2026-01-01 to 2026-03-31        1  (02-03)
    this-year     2026, cut at today             2026-01-01 to 2026-06-30        2
    last-year     2025                           2025-01-01 to 2025-12-31        3
    year-to-date  2026 to date                   2026-01-01 to 2026-06-30        2
    this-tax      6 April 2026 on, cut           2026-04-06 to 2026-06-30        1
    last-tax      the tax year before            2025-04-06 to 2026-04-05        3
    tax-to-date   6 April 2026 to today          2026-04-06 to 2026-06-30        1

A timing pair is one change, counted in the window of its EARLIER step. The pair on
2022-06-29 has its other step on 2022-07-02, so a window of 2022-06-30 to 2022-07-31 begins
BETWEEN them: it holds one change (the explained step on 2022-07-20), and the pair began
before it. Over 2022-06-20 to 2022-07-31 the pair is inside and the window holds two. Over
2022-06-30 to 2022-07-01 it holds none, and the pair, which ends after the window, still began
before it. The difference in force on a window's first day began at the last change before it:
2025-03-03 for 12 months, 2026-02-03 for 90 days, 2019-06-25 for all of 2020.

The values chart is drawn at `choose_scale`'s fit: width is 2 x 24 + days x pixels a day, and
a window of more than 40 and fewer than 2,739 days is drawn about 10,000 wide:
everything 2,738 days at 3.65 = 10,042; 12 months 365 days at 27.40 = 10,048; 90 days at 111.11
= 10,048.

Where a window may travel: the masked GET takes it in its query, the POST in its body, and
the POST's answer is `no-store` with no `Location`.
"""

from __future__ import annotations

import re
import threading
from datetime import date
from html import unescape
from http.server import HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import invented_balance_account as inv
from obdi.balance_chart import BalanceChart, empty_chart
from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.web_balance_chart import render_balance_chart
from test_balance_chart_fit import Page

REF = "invented-main"
TODAY = date(2026, 6, 30)

PRESETS = [
    # key, first, last, changes
    ("d30", "2026-06-01", "2026-06-30", 0),
    ("d90", "2026-04-02", "2026-06-30", 1),
    ("m3", "2026-03-31", "2026-06-30", 1),
    ("m6", "2025-12-31", "2026-06-30", 2),
    ("m12", "2025-07-01", "2026-06-30", 4),
    ("m24", "2024-07-01", "2026-06-30", 5),
    ("this-week", "2026-06-29", "2026-06-30", 0),
    ("last-week", "2026-06-22", "2026-06-28", 0),
    ("last-4-weeks", "2026-06-01", "2026-06-28", 0),
    ("this-month", "2026-06-01", "2026-06-30", 0),
    ("last-month", "2026-05-01", "2026-05-31", 1),
    ("this-quarter", "2026-04-01", "2026-06-30", 1),
    ("last-quarter", "2026-01-01", "2026-03-31", 1),
    ("this-year", "2026-01-01", "2026-06-30", 2),
    ("last-year", "2025-01-01", "2025-12-31", 3),
    ("year-to-date", "2026-01-01", "2026-06-30", 2),
    ("this-tax", "2026-04-06", "2026-06-30", 1),
    ("last-tax", "2025-04-06", "2026-04-05", 3),
    ("tax-to-date", "2026-04-06", "2026-06-30", 1),
]

BETWEEN_THE_PAIRS_STEPS = {
    "window": "between", "window_from": "2022-06-30", "window_to": "2022-07-31",
}


def window_page(*, unmasked: bool = False, today: date = TODAY, **fields: str) -> str:
    return render_balance_chart(
        inv.invented_chart(), unmasked=unmasked, window_fields=fields, today=today
    ).decode()


def words(html: str) -> str:
    """The page's text, tags removed and spaces collapsed, as a reader meets it."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split()).replace(" ;", ";").replace(
        " ,", ","
    ).replace(" .", ".")


def window_words(html: str) -> str:
    found = re.search(r"<p data-window-words>(.*?)</p>", html, re.S)
    assert found, "the page states the window in words"
    return words(found.group(1))


def strip_description(html: str) -> str:
    found = re.search(r'<desc id="bc-strip-d">(.*?)</desc>', html, re.S)
    assert found, "the masked page draws a strip"
    return unescape(found.group(1))


def changes_said(html: str) -> int:
    text = words(html)
    if "No change falls in this window" in text:
        return 0
    found = re.search(r"(\d+) changes? in this window", text)
    assert found, "the page counts the changes in the window"
    return int(found.group(1))


class TestEachNamedPeriodAndLength:
    @pytest.mark.parametrize(("key", "first", "last", "count"), PRESETS)
    def test_MaskedPage_ForEachWindow_CountsExactlyTheChangesInsideIt(
        self, key, first, last, count
    ):
        page = window_page(window=key)

        assert f"{first} to {last}" in window_words(page)
        assert changes_said(page) == count

    @pytest.mark.parametrize(("key", "first", "last", "count"), PRESETS)
    def test_ValuesPage_ForEachWindow_DrawsTheSameDaysAndCountsTheSameChanges(
        self, key, first, last, count
    ):
        page = window_page(unmasked=True, window=key)

        assert f"{first} to {last}" in window_words(page)
        assert changes_said(page) == count
        assert f"From {first} to {last}." in re.search(
            r'<desc id="bc-values-d">(.*?)</desc>', page, re.S
        ).group(1)

    @pytest.mark.parametrize(
        ("key", "first", "last"), [(k, f, to) for k, f, to, count in PRESETS if count]
    )
    def test_MaskedPage_ForWindowsWithChanges_DrawsAStripOverExactlyTheWindowsDays(
        self, key, first, last
    ):
        assert strip_description(window_page(window=key)).startswith(f"From {first} to {last}:")

    def test_MaskedPage_ForAWindowWithNoChange_SaysSoAndDrawsNoStrip(self):
        page = window_page(window="d30")

        assert "No change falls in this window" in words(page)
        assert "bc-strip-t" not in page

    def test_MaskedPage_WithNoWindowChosen_IsEverythingHeldAsItAlwaysWas(self):
        page = render_balance_chart(inv.invented_chart(), unmasked=False, today=TODAY).decode()

        assert f"{inv.TOTAL_CHANGES} changes in this range" in words(page)
        assert "data-window-words" not in page
        assert "Window: Everything held" in words(page)
        assert strip_description(page).startswith("From 2019-01-01 to 2026-06-30:")

    def test_MaskedPage_ForEverythingChosenByTheControl_IsThatSameWholeAccount(self):
        chosen = window_page(window="all")
        plain = render_balance_chart(inv.invented_chart(), unmasked=False, today=TODAY).decode()

        assert strip_description(chosen) == strip_description(plain)
        assert f"{inv.TOTAL_CHANGES} changes in this range" in words(chosen)


class TestWhatTheWindowSaysIsOfTheWindow:
    def test_Counts_OverOneYear_AreThatYearsAndNotTheAccounts(self):
        page = window_page(window="last-year")

        text = words(page)
        assert "3 changes in this window" in text
        assert "1 timing pair and 2 unexplained" in text
        assert "the first on 2025-03-03, the last on 2025-11-18" in text
        assert f"{inv.TOTAL_CHANGES} changes" not in text

    def test_TheWindowIsSaidToBeNarrowerThanEveryDayHeld(self):
        assert "are of this window and not of every day held" in words(window_page(window="m12"))

    def test_TheDifferenceInForce_IsSaidToHaveBegunBeforeTheWindowWithItsRealDay(self):
        assert (
            "in force on the window's first day began on 2025-03-03, before the window"
            in words(window_page(window="m12"))
        )
        assert "began on 2026-02-03, before the window" in words(window_page(window="d90"))

    def test_AWindowWithNoChange_StillSaysWhenTheDifferenceItIsAtBegan(self):
        page = window_page(window="between", window_from="2020-01-01", window_to="2020-12-31")

        text = words(page)
        assert "No change falls in this window" in text
        assert "began on 2019-06-25, before the window" in text

    def test_AWindowOfTheFirstDaysHeld_HasNoEarlierDifferenceToSay(self):
        page = window_page(window="between", window_from="2019-01-01", window_to="2019-01-20")

        assert "before the window" not in words(page)


class TestADifferenceThatBeganBeforeTheWindow:
    def test_PairBetweenItsTwoSteps_IsSaidToHaveBegunEarlierWithItsRealFirstDay(self):
        page = window_page(**BETWEEN_THE_PAIRS_STEPS)

        text = words(page)
        assert (
            "A timing pair began before this window, on 2022-06-29; its other step is on "
            "2022-07-02, in the window." in text
        )
        assert "1 change in this window" in text

    def test_PairBetweenItsTwoSteps_IsDrawnAsAJoinFromTheEdgeWithNoMarkOfItsOwn(self):
        page = Page(window_page(**BETWEEN_THE_PAIRS_STEPS))

        kinds = [m["data-kind"] for m in page.find("rect", class_="mark")]
        assert kinds == ["explained"], "the pair's earlier step is not in view, so no mark"
        assert len(page.find("rect", class_="cap")) == 1
        (join,) = page.find("path", class_="join")
        first_x = float(join["d"].split()[0].lstrip("M").split(",")[0])
        assert first_x == pytest.approx(80 + 0.5 / 32 * 272, abs=0.2), (
            "the join starts at the middle of the window's first day, not before the plot"
        )

    def test_MaskedPage_PutsTheStripBeforeTheExplanationSoItIsOnTheFirstScreenOfAPhone(self):
        page = window_page(**BETWEEN_THE_PAIRS_STEPS)

        assert page.index('id="bc-strip-t"') < page.index("data-window-note")
        assert page.index('id="bc-strip-t"') < page.index("A timing pair began before")
        assert page.index("data-window-words") < page.index('id="bc-strip-t"')

    def test_PairBetweenItsTwoSteps_HasATitleSayingItBeganBeforeTheWindow(self):
        html = window_page(**BETWEEN_THE_PAIRS_STEPS)

        assert "It began before the window, so it is drawn from the window's first day." in html

    def test_PairInsideTheWindow_IsCountedAndSaidToHaveBeganNowhereEarlier(self):
        page = window_page(window="between", window_from="2022-06-20", window_to="2022-07-31")

        text = words(page)
        assert "2 changes in this window" in text
        assert "A timing pair began before this window" not in text

    def test_PairSpanningTheWholeWindow_IsStillDrawnAndSaidToEndAfterIt(self):
        page = window_page(window="between", window_from="2022-06-30", window_to="2022-07-01")

        text = words(page)
        assert "No change falls in this window" in text
        assert "its other step is on 2022-07-02, after the window" in text
        assert "bc-strip-t" in page

    def test_WindowStartingAfterBothStepsOfThePair_HasNoPairToCarry(self):
        page = window_page(window="between", window_from="2022-07-03", window_to="2022-07-31")

        text = words(page)
        assert "A timing pair began before this window" not in text
        assert "1 change in this window" in text
        assert "began on 2022-07-02, before the window" in text

    def test_Values_ForTheCarriedPair_SaysSoTooAndStillShowsTheLaterStepInTheTable(self):
        page = window_page(unmasked=True, **BETWEEN_THE_PAIRS_STEPS)

        assert "A timing pair began before this window, on 2022-06-29" in words(page)
        rows = Page(page).rows
        assert [r[0] for r in rows if r and r[0] == "2022-07-02"] == ["2022-07-02"]


class TestTheWindowIsCutWhereTheAccountStopsBeingKnown:
    def test_Today_AfterTheLastKnownBalance_CutsTheWindowThereAndSaysSo(self):
        page = window_page(today=date(2026, 10, 4), window="m12")

        text = window_words(page)
        assert "2025-10-05 to 2026-06-30" in text
        assert "The last known balance is on 2026-06-30, so the window ends there." in text
        assert changes_said(page) == 3

    def test_Today_AfterTheLastKnownBalance_LeavesAWindowWhollyAfterItEmpty(self):
        page = window_page(today=date(2026, 10, 4), window="d90")

        text = window_words(page)
        assert "after the last day held, 2026-06-30" in text
        assert "No chart is drawn." in text
        assert "bc-strip-t" not in page

    def test_Today_TheSameAsTheLastKnownBalance_CutsNothing(self):
        assert "so the window ends there" not in window_words(window_page(window="m12"))

    def test_ThisYear_SaysItRunsToADayThatHasNotCome(self):
        text = window_words(window_page(window="this-year"))

        assert "The period runs to 2026-12-31, which has not come, so it is shown to today." in text

    def test_Window_StartingBeforeAnyBalanceIsKnown_StartsWhereTheFirstIs(self):
        text = window_words(
            window_page(window="between", window_from="2018-06-01", window_to="2019-02-28")
        )

        assert "No balance is known before 2019-01-01, so the window starts there." in text
        assert "2019-01-01 to 2019-02-28" in text


class TestARefusedChoice:
    def test_BackToFrontDates_AreRefusedInTheSentenceTheOtherChartUsesAndNoChartIsDrawn(self):
        page = window_page(window="between", window_from="2026-02-01", window_to="2026-01-31")

        assert 'data-window-refused' in page
        assert (
            "The window starts on 2026-02-01, which is after it ends on 2026-01-31."
            in words(page)
        )
        assert "The window was not changed, and no chart is drawn" in words(page)
        assert "bc-strip-t" not in page
        assert "changes in this" not in words(page)

    def test_ALengthThatIsNotANumber_IsRefusedBesideTheLengthControl(self):
        page = window_page(window="other", window_count="abc")

        assert "The length must be a whole number, such as 12." in words(page)
        assert "bc-strip-t" not in page

    def test_ARefusedChoiceOnTheValuesPage_DrawsNoChartEither(self):
        page = window_page(
            unmasked=True, window="between", window_from="2026-02-01", window_to="2026-01-31"
        )

        assert "data-window-refused" in page
        assert "bc-values-t" not in page
        assert "£" not in page

    def test_AChoiceThatIsFine_IsNotRefusedAndDrawsItsChart(self):
        page = window_page(window="other", window_count="6", window_unit="weeks")

        assert "data-window-refused" not in page
        assert "bc-strip-t" in page or "No change falls in this window" in words(page)


class TestTheControlIsTheOneTheOtherChartUses:
    def test_Page_OffersTheSameControlWithEverythingInOneTap(self):
        page = window_page(window="m12")

        assert 'class="position-window"' in page
        assert re.findall(r'value="([^"]+)" class="position-chip" aria-pressed="true"', page) == [
            "m12"
        ]
        assert 'name="window" value="all" class="position-chip"' in page
        assert 'name="window_held" value="m12"' in page

    def test_MaskedPage_ControlIsAGetFormAndTheValuesPagesIsAPostForm(self):
        masked = window_page(window="m12")
        shown = window_page(unmasked=True, window="m12")

        assert re.search(r'<form method="get" action="/balance-chart" data-window-form>', masked)
        assert re.search(r'<form method="post" action="/balance-chart" data-window-form>', shown)

    def test_MaskedControl_CarriesTheAccountAndNoRangeOrValue(self):
        form = re.search(r'<form method="get".*?</form>', window_page(window="m12"), re.S)
        assert form
        names = re.findall(r'<input[^>]*name="([^"]+)"', form.group(0))

        assert "ref" in names
        assert "from" not in names and "to" not in names

    def test_WeekPeriods_AreOnTheControlsFold(self):
        page = window_page()

        for key in ("this-week", "last-week", "last-4-weeks"):
            assert f'value="{key}" class="position-chip"' in page

    def test_ValuesPage_OffersWindowAndEverythingToo(self):
        page = window_page(unmasked=True, window="d90")

        assert re.findall(r'value="([^"]+)" class="position-chip" aria-pressed="true"', page) == [
            "d90"
        ]
        assert 'name="window" value="all" class="position-chip"' in page


class TestTheValuesChartIsDrawnAtTheExistingFit:
    @staticmethod
    def drawn_width(page: str) -> int:
        found = re.search(r'<svg role="img" aria-labelledby="bc-values-t[^>]*width="(\d+)"', page)
        assert found
        return int(found.group(1))

    def test_Everything_IsDrawnAboutTenThousandUnitsWide(self):
        page = render_balance_chart(
            inv.invented_chart(), unmasked=True, today=TODAY
        ).decode()

        assert self.drawn_width(page) == 10_042

    def test_TwelveMonths_IsDrawnAtTheSameTargetWidthButAtEightTimesTheScalePerDay(self):
        page = window_page(unmasked=True, window="m12")

        assert self.drawn_width(page) == 10_048
        assert "at 27.40 pixels a day" in words(page)

    def test_NinetyDays_IsDrawnAtTheSameTargetWidthAtThirtyTimesTheScalePerDay(self):
        page = window_page(unmasked=True, window="d90")

        assert self.drawn_width(page) == 10_048
        assert "at 111.11 pixels a day" in words(page)

    def test_AnExplicitRangeOfTheSameDays_IsDrawnExactlyAsTheWindowIs(self):
        window = window_page(unmasked=True, window="m12")
        ranged = render_balance_chart(
            inv.invented_chart(),
            unmasked=True,
            start=date(2025, 7, 1),
            end=date(2026, 6, 30),
            today=TODAY,
        ).decode()

        def drawing(html: str) -> str:
            return re.search(r'<svg role="img" aria-labelledby="bc-values-t.*?</svg>', html, re.S
                             ).group(0)

        assert drawing(window) == drawing(ranged)


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr("obdi.web_balance_chart._today", lambda: TODAY)
    charts: dict[str, BalanceChart] = {REF: inv.invented_chart()}

    def balance_chart_data(ref: str) -> BalanceChart:
        return charts.get(ref) or empty_chart(ref, "", "unknown")

    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(tmp_path / "c.json"),
        balance_chart_data=balance_chart_data,
    )
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()


class TestWhereTheWindowTravels:
    def test_Get_WithAWindowInTheQuery_DrawsTheMaskedStructureForThatWindow(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": REF, "window": "m12", "window_held": "m12"},
        )

        assert response.status_code == 200
        assert "no-store" not in response.headers.get("cache-control", "")
        assert "VALUES ARE SHOWN" not in response.text
        assert "£" not in response.text
        assert "2025-07-01 to 2026-06-30" in window_words(response.text)
        assert changes_said(response.text) == 4

    def test_Get_WithTheChipsOwnQuery_IsAWorkingLink(self, served):
        page = httpx.get(f"{served}/balance-chart", params={"ref": REF, "window": "m12"}).text
        form = re.search(r'<form method="get".*?</form>', page, re.S)
        assert form

        assert 'name="window" value="d90"' in form.group(0)
        again = httpx.get(
            f"{served}/balance-chart", params={"ref": REF, "window": "d90", "window_held": "m12"}
        )
        assert "2026-04-02 to 2026-06-30" in window_words(again.text)

    def test_Get_WithAnUnknownFieldBeside_NeverEchoesItAndNeverUnmasks(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": REF, "window": "m12", "unmask": "1", "values": "yes", "note": "zz9"},
        )

        assert "zz9" not in response.text
        assert "VALUES ARE SHOWN" not in response.text

    def test_Get_WithARefusedWindow_AnswersTheSentenceAndTheStatusOk(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": REF, "window": "between", "window_from": "2026-02-01",
                    "window_to": "2026-01-31"},
        )

        assert response.status_code == 200
        assert "data-window-refused" in response.text

    def test_Post_WithAWindowInTheBody_AnswersValuesWithNoLocationAndNoStore(self, served):
        response = httpx.post(
            f"{served}/balance-chart",
            data={"ref": REF, "window": "m12", "window_held": "m12"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "location" not in response.headers
        assert "no-store" in response.headers["cache-control"]
        assert "VALUES ARE SHOWN" in response.text
        assert "£" in response.text
        assert "2025-07-01 to 2026-06-30" in window_words(response.text)

    def test_Post_ItsOwnAddress_CarriesNothingOfTheWindow(self, served):
        response = httpx.post(
            f"{served}/balance-chart", data={"ref": REF, "window": "m12"}, follow_redirects=False
        )

        assert str(response.url) == f"{served}/balance-chart"
        assert response.request.url.query == b""

    def test_Post_ItsLinkBackToTheMaskedPage_MayCarryTheWindowBecauseThatPageShowsNoFigure(
        self, served
    ):
        page = httpx.post(
            f"{served}/balance-chart", data={"ref": REF, "window": "m12", "window_held": "m12"}
        ).text
        (href,) = re.findall(r'<a class="button secondary" href="([^"]+)">Hide values</a>', page)

        query = parse_qs(urlparse(unescape(href)).query)
        assert query["window"] == ["m12"] and query["ref"] == [REF]
        back = httpx.get(f"{served}{unescape(href)}")
        assert "£" not in back.text and "VALUES ARE SHOWN" not in back.text
        assert "2025-07-01 to 2026-06-30" in window_words(back.text)

    def test_MaskedPageWithAWindow_OffersValuesByAPostThatCarriesTheSameWindow(self, served):
        page = httpx.get(
            f"{served}/balance-chart", params={"ref": REF, "window": "m12", "window_held": "m12"}
        ).text
        (opened,) = re.findall(r'<form[^>]*target="_blank".*?</form>', page, re.S)

        assert 'method="post"' in opened
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', opened))
        assert fields["window"] == "m12" and fields["ref"] == REF
        assert "from" not in fields
        shown = httpx.post(f"{served}/balance-chart", data=fields)
        assert "2025-07-01 to 2026-06-30" in window_words(shown.text)
        assert "VALUES ARE SHOWN" in shown.text

    def test_Post_WithNoWindow_IsEverythingHeldAsBefore(self, served):
        response = httpx.post(f"{served}/balance-chart", data={"ref": REF})

        assert "data-window-words" not in response.text
        assert f"{inv.TOTAL_CHANGES} changes in this range" in words(response.text)

    def test_Get_WithBothARangeAndAWindow_ReadsTheWindow(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": REF, "window": "d90", "from": "2022-01-01", "to": "2022-12-31"},
        )

        assert "2026-04-02 to 2026-06-30" in window_words(response.text)

    def test_Get_WithOnlyARange_IsReadAsItAlwaysWasAndShownInTheControl(self, served):
        response = httpx.get(
            f"{served}/balance-chart",
            params={"ref": REF, "from": "2022-01-01", "to": "2022-12-31"},
        )

        text = words(response.text)
        assert "19 changes in this range" in text
        assert "Window: From 2022-01-01 to 2022-12-31" in text
        assert "data-window-words" not in response.text
