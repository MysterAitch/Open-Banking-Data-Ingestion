"""The period reconciliation at the size of a real household, in a real browser, on a phone.

The report is built from `Period` and `AccountPeriods` directly (the lab in
`test_period_reconciliation` builds one account through the application's doors, which is the
behaviour; this builds twelve accounts of fourteen statements each, which is the size). The
fourth, eighth, and twelfth period of every account (n = 3, 7, 11 from zero) does not add up, with
a leftover on each side that sum alike, so the report holds 168 periods of which 36 differ.

Measured at 390 px with every fold closed (2026-10-06), in screens of 800 px, against the old
page, which was the report's text in one block (see the commit that made this page):

    old text block    50.8 screens
    new page          2.37 screens: the summary names the twelve accounts that differ (about a
                      screen), and the record is a closed fold for each account

The allowance below sits a little above the new measurement. The old page was one preformatted
block, so the repeated-line count cannot be read from it; its words were 9,637 and the new page's
3,431, most of them behind the folds.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest

from coverage_page_world import repeated_lines
from obdi.callback import render_page
from obdi.period_reconciliation import (
    AccountPeriods,
    Leftover,
    Period,
    PeriodKind,
    PeriodReport,
)
from obdi.web_period_reconciliation import period_reconciliation_body
from page_dom import elements, parse
from test_phone_layout import sync_api

ACCOUNTS = 12
STATEMENTS = 14
SCREEN = 800
PERIOD_SCREENS = 3.0


def left(side: str, day: date, minor: int) -> Leftover:
    return Leftover(
        side, "santander-cc-pdf" if side == "statement" else "truelayer", day, minor, "x"
    )


def report() -> PeriodReport:
    accounts = []
    for a in range(ACCOUNTS):
        periods = []
        start = date(2025, 9, 12) + timedelta(days=a)
        for n in range(STATEMENTS):
            first, last = start + timedelta(days=30 * n), start + timedelta(days=30 * n + 29)
            kind = PeriodKind.FIRST if n == 0 else PeriodKind.BETWEEN
            if n % 4 == 3:
                periods.append(
                    Period(
                        kind,
                        first,
                        last,
                        10_000,
                        10_777,
                        9,
                        "truelayer",
                        True,
                        (left("statement", first + timedelta(days=3), 777),),
                        (
                            left("feed", first + timedelta(days=4), 300),
                            left("feed", first + timedelta(days=5), 477),
                        ),
                        (),
                    )
                )
            else:
                periods.append(
                    Period(kind, first, last, 10_000, 10_000, 9, "truelayer", True, (), (), ())
                )
        accounts.append(
            AccountPeriods(f"account-{a:02d}", STATEMENTS, tuple(periods), ("truelayer",))
        )
    return PeriodReport(tuple(accounts))


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def measure(browser: Any, page_html: str) -> tuple[float, float]:
    page = browser.new_page(viewport={"width": 390, "height": SCREEN})
    try:
        page.set_content(page_html)
        height = float(page.evaluate("document.documentElement.scrollHeight"))
        width = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        return height, float(page.evaluate(width))
    finally:
        page.close()


class TestAtTheSizeOfARealHousehold:
    def test_Summary_CountsEveryPeriodAndNamesEveryAccountThatDiffers(self) -> None:
        page = parse(render_page("x", period_reconciliation_body(report(), masked=True)).decode())

        assert (
            "12 accounts with a held statement; 168 periods tested, 132 add up and 36 do not."
            in (page.text())
        )
        assert (
            len([li for li in elements(page, "li") if "bad" in li.classes and "of 14" in li.text()])
            == 12
        )

    def test_Page_RepeatsNoLineOfThreeWordsMoreThanTwice(self) -> None:
        page = parse(render_page("x", period_reconciliation_body(report(), masked=True)).decode())

        assert repeated_lines(page) == {}

    def test_Page_WithEveryFoldClosed_IsWithinItsBudget(self, browser: Any) -> None:
        html = render_page("x", period_reconciliation_body(report(), masked=True)).decode()

        height, sideways = measure(browser, html)

        assert sideways <= 0
        assert height <= PERIOD_SCREENS * SCREEN, f"{height}px is {height / SCREEN:.2f} screens"
