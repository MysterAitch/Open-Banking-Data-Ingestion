"""The account page at the size of a real account: three years, a thousand and more rows.

The page used to be 24 screens tall at 390 px wide for this account, with the first transaction
9,474 px down. These lay it out in a real browser and hold what a person on a phone needs: the
name, the verdict and what holds the account back on the first screen; the month's transactions
within three screens; nothing that scrolls sideways at 390 px, or at 320 px with text enlarged to
200%; every control thumb-sized. Skipped where Playwright or its browser is not installed.

A SCREEN is 800 px tall. The corpus is `account_page_corpus`; its newest month holds fifty rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from account_page_corpus import (
    AGREEING,
    HELD,
    NEWEST_MONTH_ROWS,
    corpus_environment,
    served_corpus,
)
from test_phone_layout import _assert_fits, _measure, sync_api

SCREEN = 800
PHONE = 390
REFLOW = 320

#: THE PAGE'S BUDGET at 390 px, every disclosure closed, for a month of fifty transactions.
#: It was 24 screens for the held account before the page was first rebuilt and 6.35 after; with a
#: transaction one line of about 45 px it is, measured with the strip, the things to do, and the
#: five folds (2026-10-06):
#:
#:   held account    3858 px, 4.82 screens: 1247 px before the first transaction, 2250 px of
#:                   transactions, 361 px for the entry by hand and the five folds
#:   agreeing        3611 px, 4.51 screens
#:
#: The fifty transactions are most of it, so the bound that holds the redesign's target ("under
#: two phone screens for an ordinary month") is on the page APART FROM the month's list of
#: transactions, `FURNITURE_SCREENS`; the whole page is bound by `WHOLE_PAGE_SCREENS`, a tenth of
#: a screen above the held account's measurement.
WHOLE_PAGE_SCREENS = 4.95
FURNITURE_SCREENS = 2.0

#: THE WINDOWS' BUDGET. The page opens on the last 30 days or 50 transactions, whichever is wider,
#: and the day is fixed at the corpus's last day (`served_corpus`), so the 30 days are 2026-09-01
#: to 2026-09-30, the corpus's newest month, which holds exactly 50: the days win. With the day
#: moved to 2027-03-01 (the same account quiet for five months, measured with the same method)
#: the 50 win and the page is the newest 50 transactions: 3684 px, 4.61 screens, against 4.54.
#: Measured at 390 px, every disclosure closed (2026-10-06), in px and screens:
#:
#:                  held account                agreeing
#:   30 days    50 transactions  3635, 4.54     50  3558, 4.45     the default
#:   90 days   116              6605, 8.26     122  6798, 8.50
#:   180 days  215             11060, 13.82    215 10983, 13.73
#:
#: The invented account holds 1.7 transactions a day and the owner's main account about 2.3, so 30
#: days is about 70 of his: a page of some six screens. 90 days of his would be twelve or more,
#: which is why 90 is a tap away and not where the page opens. The "choose another window" fold
#: closed costs 44 px, one thumb-height line (page without it: 3591 and 3514 px).
WINDOW_FOLD_PX = 44
NINETY_DAYS_MAX_SCREENS = 9.0
NINETY_DAYS_TRANSACTIONS = (100, 135)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("account-scale")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    with served_corpus(root) as address:
        yield address


@pytest.fixture(autouse=True)
def _corpus_environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in corpus_environment(root).items():
        monkeypatch.setenv(name, value)


def opened(browser: object, base: str, ref: str, *, width: int = PHONE, height: int = SCREEN,
           values: bool = False) -> object:
    page = browser.new_page(viewport={"width": width, "height": height})  # type: ignore[attr-defined]
    page.goto(f"{base}/ledger?ref={ref}", wait_until="load")
    if values:
        page.get_by_role("button", name="Show values").first.click()
        page.wait_for_load_state("load")
    return page


def top_of(page: object, selector: str) -> float:
    return float(
        page.evaluate(  # type: ignore[attr-defined]
            "(s) => { const e = document.querySelector(s); return e ? "
            "e.getBoundingClientRect().top + window.scrollY : -1; }",
            selector,
        )
    )


def bottom_of(page: object, selector: str) -> float:
    return float(
        page.evaluate(  # type: ignore[attr-defined]
            "(s) => { const e = document.querySelector(s); return e ? "
            "e.getBoundingClientRect().bottom + window.scrollY : -1; }",
            selector,
        )
    )


class TestTheFirstScreen:
    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_Account_At390By800_HoldsTheNameTheTrustSentenceAndTheFirstThingToDoWithItsControl(
        self, browser, base, ref
    ):
        page = opened(browser, base, ref)
        try:
            control = ".todo a.button, .todo button.button, .todo form button"
            for selector in ("h1", "p.trust", ".todo", control):
                assert 0 < bottom_of(page, selector) <= SCREEN, f"{selector} is below the fold"
            assert top_of(page, ".held") == -1, "no tinted box: the thing to do says it"
        finally:
            page.close()

    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_FirstTransactionOfTheMonth_IsWithinTheFirstThreeScreens(self, browser, base, ref):
        page = opened(browser, base, ref)
        try:
            first = top_of(page, "li.txn")
            assert 0 < first <= 3 * SCREEN, f"the first transaction is {first:.0f} px down"
        finally:
            page.close()

    def test_Desktop_At1280_ShowsTheFirstTransactionOnTheFirstScreenBesideTheState(
        self, browser, base
    ):
        page = opened(browser, base, HELD, width=1280, height=900)
        try:
            assert 0 < top_of(page, "li.txn") <= 900
            state_right = page.evaluate(
                "document.querySelector('.acct-state').getBoundingClientRect().right"
            )
            rows_left = page.evaluate(
                "document.querySelector('.acct-txns').getBoundingClientRect().left"
            )
            assert rows_left >= state_right, "the transactions are not beside the state"
        finally:
            page.close()


class TestBetweenAPhoneAndADesk:
    """A phone asked for the desktop site lays the page out about 980 px wide.

    The owner sent that view: two columns, the transactions in a right-hand column of some 480
    px, and each transaction laid out as four cells that need more than that, so the description
    was squeezed to one character a line and a single row ran to a screen and a half. The left
    column's sections were also spread down the page with a screen of nothing between them,
    because the tall right-hand column shared its height out among their rows.
    """

    @pytest.mark.parametrize("width", [960, 980, 1100, 1240])
    def test_Rows_WhenTheTransactionsColumnIsNarrow_AreEachAFewLinesTall(
        self, browser, base, width
    ):
        page = opened(browser, base, AGREEING, width=width, height=900)
        try:
            tallest = float(
                page.evaluate(
                    "Math.max(...[...document.querySelectorAll('li.txn .t-row')]"
                    ".map(e => e.getBoundingClientRect().height))"
                )
            )
            assert tallest <= 120, f"a transaction is {tallest:.0f} px tall at {width} px"
        finally:
            page.close()

    @pytest.mark.parametrize("width", [980, 1280])
    def test_LeftColumn_BesideALongListOfTransactions_KeepsItsSectionsTogether(
        self, browser, base, width
    ):
        page = opened(browser, base, AGREEING, width=width, height=900)
        try:
            gap = top_of(page, ".ledger-more") - bottom_of(page, ".acct-state")
            assert 0 <= gap <= 48, f"{gap:.0f} px between the state and the folds"
        finally:
            page.close()


class TestTheWholePage:
    @pytest.mark.parametrize("ref", [AGREEING, HELD])
    def test_PageForAMonthOfFiftyRows_WithEveryDisclosureClosed_FillsUnderSixScreens(
        self, browser, base, ref
    ):
        page = opened(browser, base, ref)
        try:
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            total = float(page.evaluate("document.documentElement.scrollHeight"))
            count = page.evaluate("document.querySelectorAll('li.txn').length")
            assert count == NEWEST_MONTH_ROWS
            assert total < WHOLE_PAGE_SCREENS * SCREEN, f"{total / SCREEN:.2f} screens"
        finally:
            page.close()

    @pytest.mark.parametrize("ref", [AGREEING, HELD])
    def test_PageApartFromTheMonthsTransactions_FillsUnderTwoPhoneScreens(
        self, browser, base, ref
    ):
        page = opened(browser, base, ref)
        try:
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            total = float(page.evaluate("document.documentElement.scrollHeight"))
            listed = float(
                page.evaluate(
                    "(() => { const e = document.querySelector('ul.txns'); "
                    "return e ? e.getBoundingClientRect().height : 0; })()"
                )
            )
            assert listed > 40 * 40, "the month's fifty transactions are what was taken out"
            assert (total - listed) < FURNITURE_SCREENS * SCREEN, (
                f"{(total - listed) / SCREEN:.2f} screens beside the transactions"
            )
        finally:
            page.close()

    @pytest.mark.parametrize("ref", [AGREEING, HELD])
    def test_Account_OpensNothingByItself_AnAccountThatDoesNotAddUpIncluded(
        self, browser, base, ref
    ):
        page = opened(browser, base, ref)
        try:
            as_loaded = float(page.evaluate("document.documentElement.scrollHeight"))
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            closed = float(page.evaluate("document.documentElement.scrollHeight"))
            assert as_loaded == closed, "thirty-five known balances are not opened by the page"
        finally:
            page.close()


class TestTheWindowAtAPhone:
    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_Page_OpeningOnTheDefault_IsTheThirtyDaysAndNothingLonger(self, browser, base, ref):
        default = opened(browser, base, ref)
        chosen = browser.new_page(viewport={"width": PHONE, "height": SCREEN})  # type: ignore[attr-defined]
        try:
            chosen.goto(f"{base}/ledger?ref={ref}&window=d30", wait_until="load")
            heading = default.evaluate("document.querySelector('.txhead h2').textContent")
            assert heading == "Last 30 days, 2026-09-01 to 2026-09-30"
            assert default.evaluate("document.querySelectorAll('li.txn').length") == (
                NEWEST_MONTH_ROWS
            )
            assert default.evaluate("document.documentElement.scrollHeight") == chosen.evaluate(
                "document.documentElement.scrollHeight"
            )
        finally:
            default.close()
            chosen.close()

    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_NinetyDays_AreAboutThreeMonthsOfTransactionsAndUnderNineScreens(
        self, browser, base, ref
    ):
        page = browser.new_page(viewport={"width": PHONE, "height": SCREEN})  # type: ignore[attr-defined]
        try:
            page.goto(f"{base}/ledger?ref={ref}&window=d90", wait_until="load")
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            count = page.evaluate("document.querySelectorAll('li.txn').length")
            total = float(page.evaluate("document.documentElement.scrollHeight"))
            low, high = NINETY_DAYS_TRANSACTIONS
            assert low <= count <= high, f"{count} transactions in 90 days"
            assert total < NINETY_DAYS_MAX_SCREENS * SCREEN, f"{total / SCREEN:.2f} screens"
            assert total > WHOLE_PAGE_SCREENS * SCREEN, "90 days would not have fitted the budget"
        finally:
            page.close()

    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_WindowChoice_WhenClosed_CostsOneThumbHeightLine(self, browser, base, ref):
        page = opened(browser, base, ref)
        try:
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            height = bottom_of(page, "details.windows") - top_of(page, "details.windows")
            assert height == pytest.approx(WINDOW_FOLD_PX, abs=2)
        finally:
            page.close()

    def test_WindowChoice_WhenOpen_HoldsEveryChipAtThumbHeightAndFitsThePhone(self, browser, base):
        page = opened(browser, base, HELD)
        try:
            page.evaluate("() => document.querySelector('details.windows').open = true")
            chips = page.evaluate(
                "() => [...document.querySelectorAll('details.windows button.window-chip')]"
                ".filter(e => e.getClientRects().length > 0)"
                ".map(e => [e.textContent, e.getBoundingClientRect().height])"
            )
            assert [name for name, _ in chips[:7]] == [
                "Last 30 days or 50 transactions, whichever is wider",
                "Last 60 days or 50 transactions, whichever is wider",
                "Last 30 days",
                "Last 60 days",
                "Last 90 days",
                "Last 180 days",
                "Last 12 months",
            ]
            assert all(height >= 44 for _, height in chips), chips
            _assert_fits(_measure(page))
        finally:
            page.close()


class TestNoSidewaysScroll:
    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    @pytest.mark.parametrize("values", [False, True])
    def test_Page_At390_DoesNotScrollSideways(self, browser, base, ref, values):
        page = opened(browser, base, ref, values=values)
        try:
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
            _assert_fits(_measure(page))
        finally:
            page.close()

    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    def test_Page_At320WithTextAtTwoHundredPercent_DoesNotScrollSideways(self, browser, base, ref):
        page = opened(browser, base, ref, width=REFLOW)
        try:
            page.add_style_tag(content="html { font-size: 200%; }")
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
            _assert_fits(_measure(page))
        finally:
            page.close()


_CONTROLS = """() => {
  const visible = e => e.getClientRects().length > 0;
  const reach = e => {
    const r = e.getBoundingClientRect();
    const after = getComputedStyle(e, '::after');
    const extended = e.tagName === 'A' && after.content !== 'none' && after.position === 'absolute';
    return [r.width, r.height + (extended ? 24 : 0)];
  };
  return [...document.querySelectorAll(
      'main button, main summary, main a, main select, main input:not([type=hidden])')]
    .filter(visible)
    .map(e => [e.tagName + ' ' + (e.textContent || '').trim().slice(0, 28), ...reach(e)]);
}"""


class TestThumbSizedTargets:
    @pytest.mark.parametrize("ref", [HELD, AGREEING])
    @pytest.mark.parametrize("values", [False, True])
    def test_EveryControl_At390_ReachesFortyFourPixelsTall(self, browser, base, ref, values):
        page = opened(browser, base, ref, values=values)
        try:
            controls = page.evaluate(_CONTROLS)
            assert controls
            small = [(name, round(height)) for name, _, height in controls if height < 44]
            assert not small, small
        finally:
            page.close()

    def test_MonthCells_At390_AreAtLeastFortyFourPixelsEachWay(self, browser, base):
        page = opened(browser, base, HELD)
        try:
            page.evaluate("() => document.querySelector('details.months').open = true")
            cells = page.evaluate(
                "() => [...document.querySelectorAll('.monthgrid a')].map(a => {"
                " const r = a.getBoundingClientRect(); return [r.width, r.height]; })"
            )
            assert len(cells) == 3 + 12 + 12 + 9, "a link for each of the 36 months with rows"
            assert all(width >= 44 and height >= 44 for width, height in cells), cells[:3]
        finally:
            page.close()
