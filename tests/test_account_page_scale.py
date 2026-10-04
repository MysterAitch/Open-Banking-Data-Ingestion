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

#: The most screens the whole page may fill, every disclosure closed, for a month of fifty rows.
#: It was 24 for the held account before the page was rebuilt; a row has to be about 60 px for
#: fifty of them and everything round them to fit, which is why a row is two lines.
WHOLE_PAGE_SCREENS = 6


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
    def test_HeldAccount_At390By800_HoldsTheNameTheRailTheVerdictAndTheBox(
        self, browser, base
    ):
        page = opened(browser, base, HELD)
        try:
            assert top_of(page, "h1") >= 0
            for selector in ("h1", "svg.rail", ".verdict", ".held"):
                assert 0 < bottom_of(page, selector) <= SCREEN, f"{selector} is below the fold"
        finally:
            page.close()

    def test_AgreeingAccount_At390By800_HoldsTheNameTheRailAndTheVerdictAndNoBox(
        self, browser, base
    ):
        page = opened(browser, base, AGREEING)
        try:
            for selector in ("h1", "svg.rail", ".verdict"):
                assert 0 < bottom_of(page, selector) <= SCREEN, f"{selector} is below the fold"
            assert top_of(page, ".held") == -1
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

    def test_HeldAccount_LeavesTheKnownBalancesOpenWhichIsTheOneThingThatMakesItLonger(
        self, browser, base
    ):
        page = opened(browser, base, HELD)
        try:
            opened_total = float(page.evaluate("document.documentElement.scrollHeight"))
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            closed_total = float(page.evaluate("document.documentElement.scrollHeight"))
            assert opened_total > closed_total + SCREEN, "the explanation is open for the reader"
        finally:
            page.close()
        calm = opened(browser, base, AGREEING)
        try:
            as_loaded = float(calm.evaluate("document.documentElement.scrollHeight"))
            calm.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = false)")
            closed = float(calm.evaluate("document.documentElement.scrollHeight"))
            assert as_loaded == closed, "an account in agreement opens nothing"
        finally:
            calm.close()


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
