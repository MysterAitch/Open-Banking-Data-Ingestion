"""The Goals page with twelve goals fits a phone: bounded screens, no sideways scroll.

THE BUDGET: at 390 px by 844 px, twelve goals across five accounts, with long goal and account
names, a mix of debts, funds, and savings, ahead and behind, dated and not, one that cannot be
measured and one reached, the page is at most `SCREENS` screens tall. Measured with the values
masked and with them shown, which is the taller. The allowance is recorded beside the
measurements in `MEASURED_MASKED` and `MEASURED_SHOWN`.

Set GOALS_SHOTS_DIR to keep a picture of each rendering for a person to look at. Skipped where
Playwright or its browser is not installed (`playwright install chromium`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path

import pytest

from obdi.analysis.goals import AHEAD, BEHIND, GoalAccount, GoalProgress, GoalsView
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.goal_records import BUILD, CLEAR, SAVE
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig
from obdi.read.ledger import Money

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 5

#: The rendering's height in pixels as last measured, with the allowance a change must stay within.
#: Measured 2026-10-08 (twelve goals, five accounts, folds closed): masked 3,276 and shown 3,366,
#: under five screens (4,220).
MEASURED_MASKED = 3330
MEASURED_SHOWN = 3420

_NAMES = (
    "Clear the long-standing balance on the everyday credit card",
    "Rainy day fund covering six months of essential spending",
    "Planned kitchen renovation including the new boiler and flooring",
    "Summer holiday abroad with the whole family",
    "Boiler",
    "Christmas",
)


def _gbp(minor: int) -> Money:
    return Money(minor, "GBP")


def _goal(index: int) -> GoalProgress:
    kind = (CLEAR, BUILD, SAVE)[index % 3]
    dated = index % 4 != 1
    measured = index != 5
    done = index == 7
    stance = "" if not dated or not measured or done else (AHEAD if index % 2 else BEHIND)
    account = index % 5
    return GoalProgress(
        id=index + 1,
        name=_NAMES[index % len(_NAMES)],
        kind=kind,
        account=f"account-number-{account}",
        account_label=f"Everyday account number {account} with a long label",
        target_date="2027-05-15" if dated else "",
        declared_on="2026-05-15",
        stance=stance,
        done=done,
        measured=measured,
        slipped=False,
        said="" if measured else "The balance held is not known, so progress cannot be measured.",
        months_left=8 if dated else 0,
        target=_gbp(1_200_00 + index),
        start=_gbp(500_00) if kind != SAVE else None,
        now=_gbp(400_00) if measured else None,
        achieved=_gbp(400_00) if measured else None,
        to_go=_gbp(800_00) if measured else None,
        per_month=_gbp(100_00) if dated and measured and not done else None,
        gap=_gbp(33_34) if stance else None,
        share=_gbp(100_00) if stance else None,
    )


def _view() -> GoalsView:
    goals = tuple(_goal(i) for i in range(12))
    return GoalsView(
        as_of="2026-09-15",
        goals=goals,
        share_total=_gbp(600_00),
        sharing=sum(1 for g in goals if g.share),
        accounts=tuple(
            GoalAccount(f"account-number-{i}", f"Everyday account number {i} with a long label")
            for i in range(5)
        ),
    )


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    view = _view()
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(Path(os.devnull)),
        goals_data=lambda: view,
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
        page.goto(f"{served}/goals")
        if shown:
            with page.expect_navigation():
                page.click("form[action='/goals'] button")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("GOALS_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        label = "shown" if shown else "masked"
        page.screenshot(path=str(Path(shots) / f"goals-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestTwelveGoalsOnAPhone:
    @pytest.mark.parametrize("shown", [False, True])
    def test_GoalsPage_WithTwelveGoals_FitsTheScreensAndNeverScrollsSideways(
        self, browser, served, shown
    ):
        height, overflow = _measure(browser, served, shown=shown)

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= SCREENS * PHONE_HEIGHT, height
        assert height <= (MEASURED_SHOWN if shown else MEASURED_MASKED), height
