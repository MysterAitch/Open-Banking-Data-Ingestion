"""The This month page with thirty commitments fits a phone: bounded screens, no sideways scroll.

THE BUDGET: at 390 px by 844 px, thirty commitments across twelve days in five accounts, with the
longest payee names a bank prints, a third of them overdue or ended, one account short, and one
that cannot be judged, the page is at most `SCREENS` screens tall. Measured with the values masked
and with them shown, which is the taller. The allowance is recorded beside the measurements in
`MEASURED_MASKED` and `MEASURED_SHOWN`.

Set THIS_MONTH_SHOTS_DIR to keep a picture of each rendering for a person to look at. Skipped
where Playwright or its browser is not installed (`playwright install chromium`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import pytest

from obdi.analysis.free_position import CONFIRMED, AccountFigures, FigureTotal, FreeFigures
from obdi.analysis.this_month import (
    DUE,
    ENDED,
    OVERDUE,
    PAID,
    CalendarLine,
    MonthCounts,
    ThisMonth,
)
from obdi.ingest.connections import ConnectionStore
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.read.ledger import Money

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 4

#: The rendering's height in pixels as last measured, with the allowance a change must stay within.
#: Measured 2026-10-08 (thirty commitments, five accounts, folds closed): masked 3,188 and shown
#: 3,193, under four screens (3,376).
MEASURED_MASKED = 3250
MEASURED_SHOWN = 3250

_NAMES = (
    "Hartsholme Gas And Electricity Direct Debit Reference",
    "Cedarwick Fernside Gymnasium Membership",
    "Northfold Mutual Insurance Premium",
    "Zephyrine Quokka Subscriptions",
    "Pennywhistle Water Services",
    "Brindlewick Payroll",
)


def _figures(index: int) -> AccountFigures:
    short = index == 1
    cannot = index == 2
    money = Money(120_00 + index, "GBP")
    return AccountFigures(
        ref=f"account-number-{index}",
        label=f"Everyday account number {index} with a long label",
        is_card=False,
        held_basis="stated by you on 2026-09-13",
        held_known=not cannot,
        held=None if cannot else money,
        held_direction="" if cannot else "in",
        owed=None,
        income_from=CONFIRMED,
        income_on="2026-09-25",
        income_said="Next income 2026-09-25, from a confirmed income.",
        committed=money,
        committed_said="2 confirmed to leave before 2026-09-25.",
        due=(),
        unplaced=0,
        free=None if cannot else Money(30_00, "GBP"),
        free_short=short,
        free_said=(
            "Cannot be worked out: the balance is not known." if cannot else "Held less committed."
        ),
    )


def _line(index: int) -> CalendarLine:
    state = (PAID, DUE, OVERDUE, DUE, ENDED)[index % 5]
    day = 1 + (index * 7) % 28
    account = index % 5
    return CalendarLine(
        name=_NAMES[index % len(_NAMES)],
        account=f"account-number-{account}",
        account_label=f"Everyday account number {account} with a long label",
        direction="in" if index % 10 == 9 else "out",
        due=date(2026, 9, day).isoformat(),
        state=state,
        paid_on=date(2026, 9, day).isoformat() if state == PAID else "",
        amount=Money(1999 + 100 * index, "GBP"),
        said={
            PAID: f"Paid 2026-09-{day:02d}.",
            OVERDUE: "Last paid 2026-08-05.",
            ENDED: "The commitment ended on this day.",
        }.get(state, ""),
    )


def _month() -> ThisMonth:
    accounts = tuple(_figures(i) for i in range(5))
    total = FigureTotal(Money(1, "GBP"), "in", 5, 5)
    free = FreeFigures("2026-09-15", accounts, total, total, total, total)
    lines = tuple(sorted((_line(i) for i in range(30)), key=lambda line: (line.due, line.name)))
    counts = MonthCounts(
        total=sum(line.state != ENDED for line in lines),
        paid=sum(line.state == PAID for line in lines),
        due=sum(line.state == DUE for line in lines),
        overdue=sum(line.state == OVERDUE for line in lines),
        not_taken=0,
        ended=sum(line.state == ENDED for line in lines),
    )
    return ThisMonth(
        as_of="2026-09-15",
        month="2026-09",
        ahead=False,
        lines=lines,
        counts=counts,
        free=free,
        judged=tuple(a.ref for a in accounts),
    )


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    month = _month()
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(Path(os.devnull)),
        this_month_data=lambda ahead: month,
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


def _measure(browser, served, *, shown: bool) -> tuple[int, int]:
    context = browser.new_context(viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    page = context.new_page()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OBDI_INSTANCE_LABEL", "obdi")
        patch.setenv("OBDI_INSTANCE_ROLE", "production")
        page.goto(f"{served}/this-month")
        if shown:
            with page.expect_navigation():
                page.click("form[action='/this-month'] button")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("THIS_MONTH_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        label = "shown" if shown else "masked"
        page.screenshot(path=str(Path(shots) / f"this-month-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestThirtyCommitmentsOnAPhone:
    @pytest.mark.parametrize("shown", [False, True])
    def test_ThisMonthPage_WithThirtyCommitments_FitsTheScreensAndNeverScrollsSideways(
        self, browser, served, shown
    ):
        height, overflow = _measure(browser, served, shown=shown)

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= SCREENS * PHONE_HEIGHT, height
        assert height <= (MEASURED_SHOWN if shown else MEASURED_MASKED), height
