"""Drawing the position chart from some accounts and not others.

A mortgage a hundred times the size of anything else flattens every other
movement in a net-worth line, so the chart can be drawn without chosen
accounts. Only the chart narrows: the headline and every total stay whole.

The invented household, worked out by hand BEFORE the first run (pence, today
2026-10-02, each account's opening derived from its one stated balance):

    everyday   stated 2026-03-31: 450.00 (45,000)
        rows   03-05 +200.00   04-05 -50.00   05-05 +120.00   06-05 -30.00
        month-ends  03: 45,000  04: 40,000  05: 52,000  06 onwards: 49,000
    mortgage   (hsbc-mortgage, "Mortgage") stated 2026-03-31: owes 150,000.00
        rows   03-15 +500.00   04-15 +500.00   05-15 +500.00   06-15 +500.00
        month-ends  03: -15,000,000  04: -14,950,000  05: -14,900,000
                    06 onwards: -14,850,000
    saver      stated 2026-04-30: 1,000.00 (100,000)
        rows   04-20 +100.00   05-20 +100.00
        month-ends  04: 100,000  05 onwards: 110,000   (nothing before April)
    mystery    no stated balance, so NOT COUNTED: rows 05-20 +70.00, 07-01 -20.00
        moved   05: 7,000  06: 7,000  07 onwards: 5,000

    everything drawn (known)       03: -14,955,000  04: -14,810,000  05: -14,738,000
                                   06 onwards: -14,691,000
    mortgage left out (known)      03: 45,000  04: 140,000  05: 162,000  06 onwards: 159,000
    everything drawn (provisional) 03: -14,955,000  04: -14,810,000  05: -14,731,000
                                   06: -14,684,000  07 onwards: -14,686,000
    mortgage left out (provisional) 03: 45,000  04: 140,000  05: 169,000  06: 166,000
                                   07 onwards: 164,000
"""

from __future__ import annotations

import re
from datetime import date

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import record_stated_anchor
from obdi.masking import MASKED_TOTAL
from obdi.position import Position, chart_series, read_position
from obdi.store import Store
from obdi.valuations import Asset, AssetKind, record_observation
from obdi.web_position import render_position
from stylesheet_support import length_px
from test_ledger import land, txn
from test_position_page import EVIL, serve

D = date
TODAY = D(2026, 10, 2)
MORTGAGE = "account:hsbc-mortgage"
EVERYDAY = "account:everyday"
SAVER = "account:saver"
MYSTERY = "account:mystery"


def household(store: Store) -> None:
    land(
        store,
        "d-everyday",
        txn("everyday", "s", "e1", D(2026, 3, 5), 20000, "PAY"),
        txn("everyday", "s", "e2", D(2026, 4, 5), -5000, "SHOP"),
        txn("everyday", "s", "e3", D(2026, 5, 5), 12000, "PAY"),
        txn("everyday", "s", "e4", D(2026, 6, 5), -3000, "SHOP"),
    )
    land(
        store,
        "d-mortgage",
        *(
            txn("hsbc-mortgage", "s", f"m{month}", D(2026, month, 15), 50000, "PAYMENT")
            for month in (3, 4, 5, 6)
        ),
    )
    land(
        store,
        "d-saver",
        txn("saver", "s", "s1", D(2026, 4, 20), 10000, "SAVED"),
        txn("saver", "s", "s2", D(2026, 5, 20), 10000, "SAVED"),
    )
    land(
        store,
        "d-mystery",
        txn("mystery", "s", "y1", D(2026, 5, 20), 7000, "IN"),
        txn("mystery", "s", "y2", D(2026, 7, 1), -2000, "OUT"),
    )
    store.declare_account(AccountRecord(ref=AccountRef("hsbc-mortgage"), label="Mortgage"))
    record_stated_anchor(store, "everyday", "2026-03-31", "450.00")
    record_stated_anchor(store, "hsbc-mortgage", "2026-03-31", "-150000.00")
    record_stated_anchor(store, "saver", "2026-04-30", "1000.00")


def household_with_house(store: Store) -> None:
    household(store)
    record_observation(
        store, Asset("house", AssetKind.PROPERTY),
        observed_at=D(2026, 4, 15), source="valuation", value_minor=25000000,
    )


def months_of(points) -> dict[str, int]:
    return {p.month: p.net_worth.minor for p in points}


def provisional_of(points) -> dict[str, int]:
    return {p.month: p.total.minor for p in points}


@pytest.fixture
def position(tmp_path) -> Position:
    with Store(tmp_path / "p.sqlite3") as store:
        household(store)
        return read_position(store, labels={}, today=TODAY)


@pytest.fixture
def position_with_house(tmp_path) -> Position:
    with Store(tmp_path / "p.sqlite3") as store:
        household_with_house(store)
        return read_position(store, labels={}, today=TODAY)


def every_key(position: Position) -> set[str]:
    return {item.key for item in position.chart_items}


class TestTheChartsSeriesWhenEverythingIsDrawn:
    def test_Series_WithEveryItemDrawn_AreTheHistoryTheHouseholdAlreadyHad(self, position):
        drawn = chart_series(position, every_key(position))

        assert drawn.history == position.history
        assert drawn.complete_from == position.complete_from
        assert drawn.provisional == position.provisional_history

    def test_Series_WithNoChoiceMade_AreTheHistoryTheHouseholdAlreadyHad(self, position):
        drawn = chart_series(position, None)

        assert drawn.history == position.history
        assert drawn.provisional == position.provisional_history

    def test_Series_WithEveryItemDrawn_MatchTheHandWorkedFigures(self, position):
        drawn = chart_series(position, every_key(position))
        known = months_of(drawn.history)

        assert known["2026-03"] == -14955000
        assert known["2026-04"] == -14810000
        assert known["2026-05"] == -14738000
        assert known["2026-06"] == known["2026-10"] == -14691000
        guess = provisional_of(drawn.provisional)
        assert guess["2026-05"] == -14731000
        assert guess["2026-06"] == -14684000
        assert guess["2026-07"] == guess["2026-10"] == -14686000

    def test_Series_WithEveryItemDrawn_AreTheSameWhenAnAssetIsHeld(self, position_with_house):
        drawn = chart_series(position_with_house, every_key(position_with_house))

        assert drawn.history == position_with_house.history
        assert drawn.provisional == position_with_house.provisional_history
        assert months_of(drawn.history)["2026-04"] == -14810000 + 25000000


class TestTheChartsSeriesWithTheMortgageLeftOut:
    def test_Known_IsTheSumOfTheRestMonthByMonth(self, position):
        rest = every_key(position) - {MORTGAGE}

        known = months_of(chart_series(position, rest).history)

        assert known == {
            "2026-03": 45000, "2026-04": 140000, "2026-05": 162000, "2026-06": 159000,
            "2026-07": 159000, "2026-08": 159000, "2026-09": 159000, "2026-10": 159000,
        }

    def test_Provisional_IsTheSumOfTheRestPlusWhatTheUncountedAccountMoved(self, position):
        rest = every_key(position) - {MORTGAGE}

        guess = provisional_of(chart_series(position, rest).provisional)

        assert guess == {
            "2026-03": 45000, "2026-04": 140000, "2026-05": 169000, "2026-06": 166000,
            "2026-07": 164000, "2026-08": 164000, "2026-09": 164000, "2026-10": 164000,
        }

    def test_Known_SaysWhichMonthsAreCompleteForTheItemsStillDrawn(self, position):
        rest = every_key(position) - {MORTGAGE}

        drawn = chart_series(position, rest)

        assert drawn.complete_from == "2026-04"
        first = drawn.history[0]
        assert (first.month, first.included, first.of, first.partial) == ("2026-03", 1, 2, True)

    def test_Series_AreNotTheFullTotalLessAGuess_ButEachMonthsOwnFigures(self, position):
        # A month in which the saver has no figure yet must not be filled with a nil
        # the saver never had: March is the current account alone.
        drawn = chart_series(position, {EVERYDAY, SAVER})

        assert months_of(drawn.history)["2026-03"] == 45000
        assert drawn.history[0].included == 1

    def test_Series_ForAnItemWithNoFigureUntilLater_StartWhereTheFirstDrawnItemDoes(self, position):
        drawn = chart_series(position, {SAVER})

        assert drawn.history[0].month == "2026-04"
        assert months_of(drawn.history)["2026-04"] == 100000
        assert drawn.provisional == ()


class TestTheChartsSeriesWithOtherChoices:
    def test_Provisional_WithTheUncountedAccountLeftOut_IsNotDrawnAtAll(self, position):
        drawn = chart_series(position, every_key(position) - {MYSTERY})

        assert drawn.provisional == ()
        assert months_of(drawn.history) == months_of(position.history)

    def test_Series_WithNothingDrawn_AreEmpty(self, position):
        drawn = chart_series(position, set())

        assert drawn.history == ()
        assert drawn.provisional == ()
        assert drawn.complete_from == ""

    def test_Series_WithOnlyNamesThatMatchNothing_AreEmptyAndDoNotFail(self, position):
        drawn = chart_series(position, {"account:nobody", "asset:../x", ""})

        assert drawn.history == ()

    def test_Series_WithAnAssetLeftOut_KeepTheLiabilityAndDropTheAsset(self, position_with_house):
        drawn = chart_series(position_with_house, every_key(position_with_house) - {"asset:house"})

        assert months_of(drawn.history)["2026-04"] == -14810000

    def test_Headline_IsUnchangedByWhatTheChartIsDrawnFrom(self, position):
        # The figures are read from the position, which the choice never touches.
        before = (position.net_worth, position.provisional_total, position.groups)
        chart_series(position, {SAVER})

        assert (position.net_worth, position.provisional_total, position.groups) == before


def page_of(position: Position, chart_in=None, *, unmasked: bool = True) -> str:
    return render_position(position, unmasked=unmasked, chart_in=chart_in).decode()


def chart_block(page: str) -> str:
    return page.split("<h2>History, month by month</h2>")[1]


def above_history(page: str) -> str:
    return page.split("<h2>History, month by month</h2>")[0]


class TestThePageWhenTheMortgageIsLeftOut:
    def test_Page_NamesWhatTheChartLeavesOut(self, position):
        page = page_of(position, every_key(position) - {MORTGAGE})

        assert "The chart leaves out: Mortgage (hsbc-mortgage)." in page

    def test_Page_WhenNothingIsLeftOut_DoesNotSayTheChartLeavesAnythingOut(self, position):
        page = page_of(position, None)

        assert "The chart leaves out" not in page

    def test_Page_WhenEveryTickIsPostedBack_DoesNotSayTheChartLeavesAnythingOut(self, position):
        page = page_of(position, every_key(position))

        assert "The chart leaves out" not in page

    def test_Page_SaysTheHeadlineAndTotalsStillCountEverything(self, position):
        page = page_of(position, every_key(position) - {MORTGAGE})

        assert (
            "The headline and every total on this page still count everything held; only "
            "the chart is narrower, so its lines are not the household's net worth." in page
        )

    def test_Page_WhenNothingIsLeftOut_DoesNotSayTheTotalsAreWhole(self, position):
        assert "still count everything held" not in page_of(position, None)

    def test_Headline_AndEveryTotalAbove_AreIdenticalWithAndWithoutTheMortgageDrawn(self, position):
        whole = page_of(position, every_key(position))
        narrowed = page_of(position, every_key(position) - {MORTGAGE})

        assert above_history(whole) == above_history(narrowed)

    def test_MonthTable_IsIdenticalWithAndWithoutTheMortgageDrawn(self, position):
        whole = page_of(position, every_key(position))
        narrowed = page_of(position, every_key(position) - {MORTGAGE})

        assert re.findall(r"<table>.*?</table>", whole, re.S) == re.findall(
            r"<table>.*?</table>", narrowed, re.S
        )

    def test_Chart_IsDrawnFromTheNarrowedFigures(self, position):
        page = page_of(position, every_key(position) - {MORTGAGE})
        chart = re.search(r'<svg role="img".*?</svg>', chart_block(page), re.S)

        assert chart is not None
        assert "latest £1,590.00" in chart.group(0)
        assert "provisional latest £1,640.00" in chart.group(0)
        assert "-£146,910.00" not in chart.group(0)

    def test_Chart_WithEverythingDrawn_IsTheChartTheHouseholdHadBefore(self, position):
        before = page_of(position, None)
        after = page_of(position, every_key(position))

        assert re.search(r"<svg.*?</svg>", before, re.S)
        assert re.search(r"<svg.*?</svg>", before, re.S).group(0) == re.search(
            r"<svg.*?</svg>", after, re.S
        ).group(0)
        assert "latest -£146,910.00" in before

    def test_Chart_WhenNarrowed_IsLabelledAsTheChosenAccountsAlone(self, position):
        page = page_of(position, every_key(position) - {MORTGAGE})

        assert "Chosen accounts only" in page
        assert "Chosen accounts only" not in page_of(position, None)

    def test_Legend_WhenNarrowed_DoesNotCallTheLineTheNetWorth(self, position):
        legend = re.findall(r'<p class="muted">One figure per month-end\..*?</p>', page_of(
            position, every_key(position) - {MORTGAGE}
        ))

        assert len(legend) == 1
        assert "net worth" not in legend[0]

    def test_Caption_WhenSeveralAreLeftOut_ListsThemWithTheSerialComma(self, position):
        page = page_of(position, {SAVER})

        assert "The chart leaves out: everyday, Mortgage (hsbc-mortgage), and mystery." in page

    def test_Caption_WhenTwoAreLeftOut_JoinsThemWithAnd(self, position):
        page = page_of(position, {SAVER, EVERYDAY})

        assert "The chart leaves out: Mortgage (hsbc-mortgage) and mystery." in page


class TestTheTicks:
    def test_Ticks_AreOnePerAccountAndAssetAndAllTickedByDefault(self, position_with_house):
        page = page_of(position_with_house, None, unmasked=False)

        ticks = re.findall(
            r'<input type="checkbox" name="chart_in" value="([^"]*)"( checked)?>', page
        )
        assert [value for value, _ in ticks] == [
            "account:everyday", "account:hsbc-mortgage", "account:saver",
            "asset:house", "account:mystery",
        ]
        assert all(checked for _, checked in ticks)

    def test_Ticks_AfterTheMortgageIsLeftOut_ShowItUntickedAndTheRestTicked(self, position):
        page = page_of(position, every_key(position) - {MORTGAGE})

        ticks = dict(
            re.findall(r'<input type="checkbox" name="chart_in" value="([^"]*)"( checked)?>', page)
        )
        assert ticks[MORTGAGE] == ""
        assert all(ticks[key] for key in ticks if key != MORTGAGE)

    def test_Ticks_AreInAFormThatPostsToThePositionPageAndSaysAChoiceWasMade(self, position):
        page = page_of(position, None)

        forms = re.findall(r'<form method="post" action="/position">.*?</form>', page, re.S)
        ticked = [f for f in forms if 'name="chart_in"' in f]
        assert len(ticked) == 1
        assert '<input type="hidden" name="chart_chosen" value="1">' in ticked[0]

    def test_Ticks_WhenAnAssetIsHeld_SayLeavingOutALiabilityLeavesItsAssetIn(
        self, position_with_house
    ):
        page = page_of(position_with_house, None, unmasked=False)

        assert (
            "Leaving out a liability leaves the asset it is secured against in the chart "
            "unless that is unticked too." in page
        )

    def test_Ticks_WhenNoAssetIsHeld_DoNotMentionAssets(self, position):
        assert "secured against" not in page_of(position, None, unmasked=False)

    def test_Ticks_ShowTheNameTheReferenceAndWhichWayTheBalanceSits(self, position):
        page = page_of(position, None, unmasked=False)

        assert "Mortgage" in page and "hsbc-mortgage" in page
        assert re.search(r'value="account:hsbc-mortgage"[^>]*>.*?overdrawn or owed', page, re.S)

    def test_Ticks_LabelsAreThumbTall(self, position):
        page = page_of(position, None, unmasked=False)

        assert 'class="tick"' in page
        floor = re.search(r"label\.tick \{[^}]*min-height: ([^;]+);", page)
        assert floor and length_px(page, floor.group(1)) >= 44


class TestTheMaskedPage:
    def test_MaskedGet_ShowsTheTicksButNoFigureAndNoChart(self, position):
        page = page_of(position, None, unmasked=False)

        assert 'name="chart_in"' in page
        assert "<svg" not in page
        for figure in ("148,500.00", "14850000", "1,590.00", "159000", "146,910.00", "490.00"):
            assert figure not in page
        assert MASKED_TOTAL in page

    def test_MaskedGet_OffersToShowValuesWithTheChoiceMade(self, position):
        page = page_of(position, None, unmasked=False)

        assert "Show values, chart drawn from these" in page

    def test_MaskedPage_IgnoresAChoiceItIsHanded(self, position):
        # The masked rendering never reads a choice, so a query string cannot narrow it either.
        assert page_of(position, {SAVER}, unmasked=False) == page_of(position, None, unmasked=False)


class TestNothingDrawn:
    def test_Page_WithEveryTickRemoved_DrawsNoChartAndSaysWhy(self, position):
        page = page_of(position, set())

        assert "<svg" not in page
        assert "Nothing is ticked, so no chart is drawn." in page

    def test_Page_WithEveryTickRemoved_StillShowsTheTicksToPutThemBack(self, position):
        page = page_of(position, set())

        assert page.count('name="chart_in"') == 4
        assert " checked>" not in page

    def test_Page_WithOnlyUnknownNamesTicked_DrawsNoChartAndDoesNotFail(self, position):
        page = page_of(position, {"account:nobody", "<b>x</b>"})

        assert "<svg" not in page
        assert "<b>x</b>" not in page
        assert "Nothing is ticked, so no chart is drawn." in page

    def test_Page_WithAnUnknownNameBesideRealOnes_DrawsAsIfTheUnknownWereAbsent(self, position):
        real = every_key(position) - {MORTGAGE}

        assert page_of(position, real | {"account:nobody"}) == page_of(position, real)

    def test_Page_WithAHostileNameTicked_NeverEchoesItAsMarkup(self, position):
        page = page_of(position, {'"><script>alert(1)</script>'})

        assert "<script>" not in page


class TestTheRoutes:
    @pytest.fixture
    def lab(self, tmp_path, monkeypatch):
        httpd, lab = serve(tmp_path, monkeypatch, household_with_house)
        try:
            yield lab
        finally:
            httpd.shutdown()

    def post(self, lab, data, **kwargs) -> httpx.Response:
        return httpx.post(
            f"{lab.base}/position", data=data, follow_redirects=False, timeout=30, **kwargs
        )

    def test_Post_WithNoChoice_DrawsTheChartFromEverythingAsBefore(self, lab):
        response = self.post(lab, {})

        assert response.status_code == 200
        assert "The chart leaves out" not in response.text
        assert "<svg" in response.text
        assert response.headers["Cache-Control"] == "no-store"

    def test_Post_WithTheMortgageUnticked_RedrawsTheChartForTheRestAndSaysSo(self, lab):
        response = self.post(
            lab,
            {
                "chart_chosen": "1",
                "chart_in": ["account:everyday", "account:saver", "asset:house", "account:mystery"],
            },
        )

        assert response.status_code == 200
        assert "The chart leaves out: Mortgage (hsbc-mortgage)." in response.text
        assert "<svg" in response.text
        assert "Chosen accounts only" in response.text

    def test_Post_WithTheMortgageUnticked_KeepsTheHeadlineWhole(self, lab):
        whole = self.post(lab, {}).text
        narrowed = self.post(
            lab, {"chart_chosen": "1", "chart_in": ["account:everyday", "account:saver"]}
        ).text

        assert above_history(whole) == above_history(narrowed)

    def test_Post_WithEveryTickRemoved_DrawsNoChart(self, lab):
        response = self.post(lab, {"chart_chosen": "1"})

        assert response.status_code == 200
        assert "<svg" not in response.text
        assert "Nothing is ticked, so no chart is drawn." in response.text

    def test_Post_WithForgedTickNames_IsAnsweredNotFailed(self, lab):
        response = self.post(
            lab,
            {"chart_chosen": "1", "chart_in": ["account:ghost", "asset:", "x" * 5000, "\x00"]},
        )

        assert response.status_code == 200
        assert "Nothing is ticked" in response.text

    def test_Post_WithTicksButNoMarkerThatAChoiceWasMade_DrawsEverything(self, lab):
        response = self.post(lab, {"chart_in": ["account:everyday"]})

        assert "The chart leaves out" not in response.text

    def test_Post_DrivenByAnotherSite_IsRefusedWhateverTheChoice(self, lab):
        response = self.post(
            lab, {"chart_chosen": "1", "chart_in": ["account:everyday"]}, headers=EVIL
        )

        assert response.status_code == 403
        assert "<svg" not in response.text
        assert "The chart leaves out" not in response.text

    def test_Get_ShowsTheTicksAndNoFigure_AndReadsNothingFromTheQueryString(self, lab):
        response = httpx.get(
            f"{lab.base}/position",
            params={"chart_chosen": "1", "chart_in": "account:everyday"},
            timeout=30,
        )

        assert response.status_code == 200
        assert response.text.count('name="chart_in"') == 5
        assert response.text.count(" checked>") == 5
        assert "<svg" not in response.text
        assert "The chart leaves out" not in response.text


sync_api = pytest.importorskip("playwright.sync_api")

PHONE = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


class TestTheTicksOnAPhone:
    def render(self, browser, markup: str):
        page = browser.new_page(viewport=PHONE)
        page.set_content(markup)
        return page

    def test_Ticks_AtPhoneWidth_AreThumbTallAndTheirBoxesAreNotTiny(self, browser, position):
        page = self.render(browser, page_of(position, every_key(position) - {MORTGAGE}))
        try:
            sizes = page.evaluate(
                """() => [...document.querySelectorAll('label.tick')].map(l => {
                    const box = l.querySelector('input').getBoundingClientRect();
                    return [l.getBoundingClientRect().height, box.width, box.height];
                })"""
            )
        finally:
            page.close()

        assert len(sizes) == 4
        assert all(row >= 44 and width >= 24 and height >= 24 for row, width, height in sizes)

    def test_Page_AtPhoneWidth_WithTheChartNarrowed_DoesNotScrollSideways(self, browser, position):
        page = self.render(browser, page_of(position, every_key(position) - {MORTGAGE}))
        try:
            scroll, view = page.evaluate(
                "() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]"
            )
        finally:
            page.close()

        assert scroll <= view
