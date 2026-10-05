"""The balance chart and its window control are usable on a phone.

The owner reads these pages on a phone: the page must not scroll sideways, every control must
be a thumb tall, and a window short enough to fit the chart's column must not scroll inside
it. The account is `invented_balance_account`, served by the real handler with today fixed at
2026-06-30.

Known answers, worked out before the first run from `choose_scale` (14 units a day, and no
less than 250 wide): a window of 1 or 7 days is 250 wide, and the chart's column beside the
figures is 360 less the page's margins less the figures' 78: at least 250 on a 360 px phone,
so those two fit it; 30 days is 468 wide and does not, and scrolls inside its own region and
nowhere else.

Skipped where Playwright or its browser is not installed, as `test_phone_layout` is.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import pytest

import invented_balance_account as inv
import obdi.web_balance_chart as web_balance_chart
from obdi.balance_chart import BalanceChart, empty_chart
from obdi.connections import ConnectionStore
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig
from test_phone_layout import _assert_fits, _measure

REF = "invented-main"
TODAY = date(2026, 6, 30)
WIDTHS = [360, 390]
HEIGHT = 780

sync_api = pytest.importorskip("playwright.sync_api")


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
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root: Path = tmp_path_factory.mktemp("balance-phone")
    originally = web_balance_chart._today
    web_balance_chart._today = lambda: TODAY  # type: ignore[assignment]
    charts: dict[str, BalanceChart] = {REF: inv.invented_chart()}
    config = WebConfig(
        client_id="client-1",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(root / "c.json"),
        balance_chart_data=lambda ref: charts.get(ref) or empty_chart(ref, "", "unknown"),
    )
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        web_balance_chart._today = originally  # type: ignore[method-assign]


def masked_page(browser: object, base: str, width: int, query: str = "") -> object:
    page = browser.new_page(viewport={"width": width, "height": HEIGHT})  # type: ignore[attr-defined]
    page.goto(f"{base}/balance-chart?ref={REF}{query}", wait_until="load")
    return page


def values_page(browser: object, base: str, width: int, **fields: str) -> object:
    """The page with values, reached the way a person reaches it: a POST from the page."""
    page = browser.new_page(viewport={"width": width, "height": HEIGHT})  # type: ignore[attr-defined]
    page.goto(f"{base}/balance-chart?ref={REF}", wait_until="load")
    with page.expect_navigation():
        page.evaluate(
            """(fields) => {
              const form = document.createElement('form');
              form.method = 'post'; form.action = '/balance-chart';
              for (const [name, value] of Object.entries(fields)) {
                const input = document.createElement('input');
                input.type = 'hidden'; input.name = name; input.value = value;
                form.appendChild(input);
              }
              document.body.appendChild(form);
              setTimeout(() => form.submit(), 0);
            }""",
            {"ref": REF, **fields},
        )
    return page


def control_heights(page: object) -> list[tuple[str, float]]:
    page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")  # type: ignore[attr-defined]
    return page.evaluate(  # type: ignore[attr-defined,no-any-return]
        """() => [...document.querySelectorAll(
            '.window-control button, .window-control input, .window-control select,'
            + ' .window-control summary')]
          .map(e => [e.tagName + ' ' + (e.name || e.textContent.trim().slice(0, 24)),
                     e.getBoundingClientRect().height])"""
    )


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize(
    "query",
    ["", "&window=m12&window_held=m12", "&window=d90&window_held=d90",
     "&window=between&window_from=2022-06-30&window_to=2022-07-31"],
    ids=["default", "year", "ninety-days", "pair-begun-before"],
)
def test_MaskedBalanceChart_WithOrWithoutAWindow_DoesNotScrollSideways(
    browser: object, base: str, width: int, query: str
) -> None:
    page = masked_page(browser, base, width, query)
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")  # type: ignore[attr-defined]
        _assert_fits(_measure(page))
    finally:
        page.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("width", WIDTHS)
def test_MaskedBalanceChart_WindowControls_AreEachAtLeastAThumbTall(
    browser: object, base: str, width: int
) -> None:
    page = masked_page(browser, base, width, "&window=m12&window_held=m12")
    try:
        heights = control_heights(page)
        assert heights, "the window's controls were not found"
        short = [name for name, height in heights if height < 43.5]
        assert short == [], f"controls under 44px tall: {short}"
    finally:
        page.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("width", WIDTHS)
def test_ValuesPage_WindowControls_AreEachAtLeastAThumbTall(
    browser: object, base: str, width: int
) -> None:
    page = values_page(browser, base, width, window="m12", window_held="m12")
    try:
        short = [name for name, height in control_heights(page) if height < 43.5]
        assert short == [], f"controls under 44px tall: {short}"
    finally:
        page.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("days", [1, 7, 30, 90])
def test_ValuesPageOfAWindow_AtPhoneWidth_NeverScrollsTheDocumentSideways(
    browser: object, base: str, width: int, days: int
) -> None:
    page = values_page(
        browser, base, width, window="other", window_count=str(days), window_unit="days"
    )
    try:
        _assert_fits(_measure(page))
    finally:
        page.close()  # type: ignore[attr-defined]


def region_overflow(page: object) -> tuple[int, int]:
    """(width of the drawing, width of the column it is drawn in) in pixels."""
    found = page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const region = document.querySelector('div[role=region]');
          return [region.scrollWidth, region.clientWidth];
        }"""
    )
    return int(found[0]), int(found[1])


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("days", [1, 7])
def test_ValuesPage_OfAWeekOrLess_FitsTheChartsColumnWithoutScrollingInIt(
    browser: object, base: str, width: int, days: int
) -> None:
    page = values_page(
        browser, base, width, window="other", window_count=str(days), window_unit="days"
    )
    try:
        drawn, column = region_overflow(page)
        assert drawn <= column, f"{drawn}px of chart in a {column}px column"
    finally:
        page.close()  # type: ignore[attr-defined]


@pytest.mark.parametrize("width", WIDTHS)
def test_ValuesPage_OfThirtyDays_ScrollsInsideItsOwnColumnAndNowhereElse(
    browser: object, base: str, width: int
) -> None:
    page = values_page(
        browser, base, width, window="other", window_count="30", window_unit="days"
    )
    try:
        drawn, column = region_overflow(page)
        assert drawn == 468
        assert column < drawn
        _assert_fits(_measure(page))
    finally:
        page.close()  # type: ignore[attr-defined]


def test_ValuesPage_VeryWideWindow_KeepsTheOldMaximumWidth(browser: object, base: str) -> None:
    page = values_page(
        browser, base, 390, window="m12", window_held="m12", wide="1"
    )
    try:
        drawn, _ = region_overflow(page)
        assert drawn == 10_048
    finally:
        page.close()  # type: ignore[attr-defined]
