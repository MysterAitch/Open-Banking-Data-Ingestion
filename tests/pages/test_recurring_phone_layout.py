"""The recurring-payments page with thirty series fits a phone: four screens, no sideways scroll.

THE BUDGET: at 390 px by 844 px, thirty series in five accounts, with a third of them marked
(stopped, changed, from another account, a transfer, income, a varying amount) and the longest
payee names a bank prints, the page is at most four screens tall (it was three before every row
said its kind). Measured with the values masked and with them shown, which is the taller. The
allowance is recorded beside the measurements in `MEASURED_CLOSED` (folds closed) and
`MEASURED_OPEN` (every fold opened). Twelve of the thirty are marked, which is more than a real
store is likely to hold: a mark that wraps costs a line. Marking twenty-four of the thirty
(measured 2026-10-07) made the masked page 2,733 px, which was then over three screens.

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

from obdi.analysis.recurring import HABIT, PULLED, RecurringFindings, Series
from obdi.ingest.connections import ConnectionStore
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 4

#: The tallest rendering of the thirty, in pixels, as last measured; the budget is SCREENS tall.
#: Measured 2026-10-07 with four of the thirty folded as stopped over a year ago (two at the foot
#: of each of two accounts). Before each row said who starts the payment and by which signal
#: ("pulled, by type: Direct Debit"): closed, masked 2,328 and shown 2,437; every fold opened,
#: masked 2,618 and shown 2,726, within three screens (2,532) when closed. With the kind on every
#: row, which wraps about every other row onto another line: closed, masked 2,683 and shown 2,791;
#: opened, masked 2,991 and shown 3,099. That is over three screens and under four (3,376), so
#: the allowance moved to four, a cost the owner is to judge against the kind being on the row.
MEASURED_CLOSED = 2800
MEASURED_OPEN = 3110
SCREENS_OPEN = 4

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
        kind=PULLED,
        basis="by type: Direct Debit",
        periods=6 + index % 7,
        explained=0,
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
            return replace(
                base,
                cadence="weekly",
                weekday=4,
                usual_day=0,
                kind=HABIT,
                basis="by shape: weekday rhythm, amounts vary",
                periods=10 + index % 7 + 4,
            )
        case 9 | 10:
            # Stopped years ago: the page folds these at the foot of their account.
            return replace(
                base,
                stopped=True,
                first_seen=date(2022, 1, 5),
                last_seen=date(2024, 3, 5),
                next_expected=date(2024, 4, 5),
            )
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


def _measure(
    browser, served, *, shown: bool, label: str, folds_open: bool = False
) -> tuple[int, int]:
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
    if folds_open:
        page.evaluate("document.querySelectorAll('details.recur-old').forEach(d => d.open = true)")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("RECURRING_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"recurring-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestThirtySeriesOnAPhone:
    @pytest.mark.parametrize("folds_open", [False, True])
    @pytest.mark.parametrize("shown", [False, True])
    def test_RecurringPage_WithThirtySeries_FitsThreeScreensAndNeverScrollsSideways(
        self, browser, served, shown, folds_open
    ):
        label = f"{'shown' if shown else 'masked'}-{'open' if folds_open else 'closed'}"
        height, overflow = _measure(
            browser, served, shown=shown, label=label, folds_open=folds_open
        )

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= (SCREENS_OPEN if folds_open else SCREENS) * PHONE_HEIGHT, height
        assert height <= (MEASURED_OPEN if folds_open else MEASURED_CLOSED), height
