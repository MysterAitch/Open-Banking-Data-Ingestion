"""The kept statements page at the size of a real household, on a phone.

Built from the entries the page's own hook hands it (`kept_statements`), forty-five of them: twelve
waiting for an account, all read by one parser and all naming the same issuer names (the shape
that said "Santander 13, Mastercard 1, Visa 1" seven times), two recognised but refused for their
arithmetic, one covering several accounts, three with no parser, and twenty-seven assigned across
nine accounts. Measured at 390 px with every fold closed (2026-10-06); the real page was 14
screens for a house this size.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from coverage_page_world import repeated_lines
from obdi.pages.callback import render_page
from obdi.pages.web_statements import statements_body
from obdi.read.account_names import AccountShown, AccountsShown
from page_dom import elements, parse
from test_phone_layout import sync_api

UNASSIGNED = "(unassigned)"
SCREEN = 800
STATEMENT_SCREENS = 3.0


def entry(number: int, *, account: str = UNASSIGNED, **more: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": number,
        "origin": f"2025.{number:02d} - Card statement.pdf",
        "fetched_at": f"2026-09-{1 + number % 28:02d}T09:{number % 60:02d}:00",
        "account_ref": account,
        "parser": "santander-cc-pdf",
        "rows": 12 + number,
        "refusal": "",
        "listed_days": ["2025-01-02", "2025-01-30"],
        "names": [("Santander", 13), ("Mastercard", 1), ("Visa", 1)],
        "sections": [],
    }
    return {**base, **more}


def household() -> list[dict[str, object]]:
    found = [entry(n) for n in range(1, 13)]
    found += [entry(n, refusal="its rows do not carry its balances") for n in (13, 14)]
    found.append(
        entry(
            15,
            sections=[
                {"token": "a", "label": "Regular Saver", "rows": 4, "refusal": "", "account": ""},
                {
                    "token": "b",
                    "label": "Car Loan",
                    "rows": 2,
                    "refusal": "",
                    "account": "account-3",
                },
            ],
        )
    )
    found += [entry(n, parser=None, rows=None, names=[]) for n in (16, 17, 18)]
    found += [entry(n, account=f"account-{n % 9}") for n in range(19, 46)]
    return found


NAMES = AccountsShown([AccountShown.named(f"account-{n}", f"Account {n}") for n in range(9)])


def body() -> str:
    return statements_body(
        household(),
        names=NAMES,
        options={"account-1": "Account 1"},
        can_assign=True,
        can_section_assign=True,
        can_move=True,
    )


class TestNothingIsSaidTwice:
    def test_NoLineOfThreeWordsIsRepeatedMoreThanTwice(self) -> None:
        page = parse(render_page("Kept statements", body()).decode())
        # A chooser lists the same options each time; that is data, not a sentence said again.
        for node in list(elements(page, "select")):
            node.children.clear()

        assert repeated_lines(page) == {}


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


class TestFitsAPhone:
    def test_Page_With45Statements_AndEveryFoldClosed_IsWithinItsBudget(self, browser: Any) -> None:
        page = browser.new_page(viewport={"width": 390, "height": SCREEN})
        try:
            page.set_content(render_page("Kept statements", body()).decode())
            height = float(page.evaluate("document.documentElement.scrollHeight"))
            width = "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            sideways = float(page.evaluate(width))
        finally:
            page.close()

        assert sideways <= 0
        assert height <= STATEMENT_SCREENS * SCREEN, f"{height}px is {height / SCREEN:.2f} screens"
