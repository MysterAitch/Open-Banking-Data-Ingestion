"""The recurring-payments page with thirty series fits a phone: three screens, no sideways scroll.

THE BUDGET: at 390 px by 844 px, thirty series in five accounts, with a third of them marked
(stopped, changed, from another account, a transfer, income, a varying amount) and the longest
payee names a bank prints, the page is at most three screens tall. Measured with the values
masked and with them shown, which is the taller. The allowance is recorded beside the
measurement in `MEASURED_HEIGHT`. Twelve of the thirty are marked, which is more than a real
store is likely to hold: a mark that wraps costs a line. Marking twenty-four of the thirty
(measured 2026-10-07) made the masked page 2,733 px, which is over three screens.

Set RECURRING_SHOTS_DIR to keep a picture of each rendering for a person to look at. Skipped
where Playwright or its browser is not installed (`playwright install chromium`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import pytest

from obdi.connections import ConnectionStore
from obdi.recurring import RecurringFindings, Series
from obdi.web import AuthorisationSession, ConnectionHandler, WebConfig

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 3

#: The tallest rendering of the thirty, in pixels, as last measured; the budget is SCREENS tall.
MEASURED_HEIGHT = 2530  # values shown; masked is about 110 px shorter. Three screens is 2532.

TODAY = date(2026, 10, 7)

_NAMES = (
    "direct debit british gas ref",
    "netflix com",
    "github subscription",
    "acme ltd salary",
    "transfer to savings pot",
    "council tax leeds city council",
    "gym membership so ref",
    "water services",
    "music streaming",
    "window cleaner",
)


def _series(index: int) -> Series:
    base = Series(
        account=f"acct-{index % 5}",
        off_account=0,
        other_account="",
        shape=_NAMES[index % len(_NAMES)],
        label=_NAMES[index % len(_NAMES)].upper(),
        currency="GBP",
        direction="out",
        cadence="monthly",
        usual_day=1 + index % 28,
        usual_month=0,
        weekday=None,
        usual_minor=1999 + 100 * index,
        min_minor=1999 + 100 * index,
        max_minor=1999 + 100 * index,
        latest_minor=1999 + 100 * index,
        drift_percent=0.0,
        steady=True,
        count=6 + index % 7,
        missed=0,
        first_seen=date(2026, 1, 1 + index % 28),
        last_seen=date(2026, 9, 1 + index % 28),
        next_expected=date(2026, 10, 1 + index % 28),
        is_transfer=False,
        is_income=False,
        stopped=False,
        changed=False,
    )
    match index % 15:
        case 1:
            return replace(base, stopped=True, next_expected=date(2026, 6, 3))
        case 2:
            return replace(base, changed=True, drift_percent=18.2, latest_minor=2999 + index)
        case 3:
            return replace(base, direction="in", is_income=True)
        case 4:
            return replace(base, is_transfer=True, other_account="acct-4")
        case 5:
            return replace(base, off_account=2)
        case 6:
            return replace(base, steady=False, min_minor=500, max_minor=9000)
        case 7:
            return replace(base, cadence="yearly", usual_month=10, usual_day=14)
        case 8:
            return replace(base, cadence="weekly", weekday=4, usual_day=0)
        case _:
            return base


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    findings = RecurringFindings([_series(i) for i in range(30)], TODAY)
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(Path(os.devnull)),
        recurring_data=lambda: findings,
    )
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def _measure(browser, served, *, shown: bool, label: str) -> tuple[int, int]:
    context = browser.new_context(viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    page = context.new_page()
    # A deployment says what it is; without that every page carries a warning banner that a
    # real one does not, which would be measured as part of this page.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OBDI_INSTANCE_LABEL", "obdi")
        patch.setenv("OBDI_INSTANCE_ROLE", "production")
        page.goto(f"{served}/recurring")
        if shown:
            with page.expect_navigation():
                page.click("form[action='/recurring'] button")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("RECURRING_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"recurring-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestThirtySeriesOnAPhone:
    @pytest.mark.parametrize("shown", [False, True])
    def test_RecurringPage_WithThirtySeries_FitsThreeScreensAndNeverScrollsSideways(
        self, browser, served, shown
    ):
        height, overflow = _measure(
            browser, served, shown=shown, label="shown" if shown else "masked"
        )

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= SCREENS * PHONE_HEIGHT, height
        assert height <= MEASURED_HEIGHT, height
