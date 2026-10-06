"""The Connections and Position pages laid out at 390 px, with every disclosure closed.

A SCREEN is 800 px tall. Measured in a real browser over invented data (2026-10-06); each bound
sits a little above its measurement so a page that grows is noticed. Skipped where Playwright or
its browser is not installed.

    Connections, a consent lapsed and one running out, Actual differing, its press shown:
        1376 px, 1.72 screens, nothing scrolling sideways
    Connections, one consent fine, Actual agreeing: 800 px, which is the viewport itself, so
        the page is no taller than one screen
    Position, the five-account household, values masked, over invented trust (two accounts
        resting on nothing checked, open; two that add up, folded behind a count; the month
        table folded while masked): 2573 px, 3.2 screens
    The same page over the generated corpus (3 accounts, 2 assets), measured against a running
        server: 5.92 screens before the month table was folded while masked, 4.43 before the
        accounts that add up were folded, and about the same after, since that corpus holds no
        account that adds up

The owner's real store was 12 screens and 202 words in repeated lines before this change
(2026-10-05); the open list is now only the accounts the figure is least sure of, so the page
does not grow with the accounts that are in order. This file has no measurement of that store.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from obdi.position import read_position
from obdi.store import Store
from obdi.web_position import render_position
from obdi.web_sections import render_connections
from position_window_household import TODAY, window_household
from test_connections_page import (
    ACCOUNTS,
    ANSWERED,
    EXPIRING,
    FED,
    FINE,
    LAPSED,
    NOW,
    actual_hooks,
    store_of,
)
from test_phone_layout import sync_api
from test_position_trust import trusts

SCREEN = 800
PHONE = 390

CONNECTIONS_BUSY_SCREENS = 1.85
CONNECTIONS_QUIET_SCREENS = 1.0
POSITION_SCREENS = 3.4
#: One counted account's row at 390 px: its name, the bar, and its sentence.
ROW_PX_MAX = 110


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def measured(browser: Any, page_html: str) -> tuple[float, float]:
    page = browser.new_page(viewport={"width": PHONE, "height": SCREEN})
    try:
        page.set_content(page_html)
        height = float(page.evaluate("document.documentElement.scrollHeight"))
        width = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        return height, float(page.evaluate(width))
    finally:
        page.close()


def connections_html(tmp_path, *connections, **hooks) -> str:
    hooks.setdefault("connection_last_answered", lambda: ANSWERED)
    hooks.setdefault("source_connections", lambda: FED)
    hooks.setdefault("account_names", lambda: ACCOUNTS)
    hooks.setdefault("now", NOW)
    return render_connections(store_of(tmp_path, *connections), **hooks).decode()


class TestConnectionsFitsAPhone:
    def test_Connections_WithAnExpiredConsentAndActualDiffering_StaysUnderItsBudget(
        self, browser, tmp_path
    ):
        page = connections_html(
            tmp_path,
            FINE,
            EXPIRING,
            LAPSED,
            **actual_hooks("differs_three_orphans", align_available=True),
        )
        height, sideways = measured(browser, page)

        assert height <= CONNECTIONS_BUSY_SCREENS * SCREEN, height
        assert sideways <= 0

    def test_Connections_WhenEverythingIsFine_FitsOneScreen(self, browser, tmp_path):
        height, sideways = measured(
            browser, connections_html(tmp_path, FINE, **actual_hooks("agrees"))
        )

        assert height <= CONNECTIONS_QUIET_SCREENS * SCREEN, height
        assert sideways <= 0


class TestPositionFitsAPhone:
    @pytest.fixture
    def page(self, tmp_path) -> str:
        with Store(tmp_path / "p.sqlite3") as store:
            window_household(store)
            position = read_position(store, today=TODAY)
        return render_position(position, unmasked=False, accounts=trusts(), today=TODAY).decode()

    def test_Position_Masked_StaysUnderItsBudgetAndNothingScrollsSideways(self, browser, page):
        height, sideways = measured(browser, page)

        assert height <= POSITION_SCREENS * SCREEN, height
        assert sideways <= 0

    def test_Position_EachCountedAccountCostsOneShortRow(self, browser, page):
        probe = browser.new_page(viewport={"width": PHONE, "height": SCREEN})
        try:
            probe.set_content(page)
            tallest = float(
                probe.evaluate(
                    "Math.max(...[...document.querySelectorAll('.alist li')]"
                    ".filter(e => e.offsetParent !== null)"
                    ".map(e => e.getBoundingClientRect().height))"
                )
            )
        finally:
            probe.close()

        assert tallest <= ROW_PX_MAX, tallest
