"""The entity page with twelve names, three rules, and a trial fits a phone: no sideways scroll.

THE BUDGET: at 390 px by 844 px, an entity holding twelve names (four attached by hand and eight
by rule, each opening to three transactions) with the longest names a bank prints, three rules,
one entity under it, and the answer to a dry run listing six names, is at most three screens tall
with its folds closed and no more than the allowance below with them opened, measured with values
masked and with them shown, the taller of which is held. Set ENTITY_SHOTS_DIR to keep a picture of
each rendering for a person to look at. Skipped where Playwright or its browser is not installed
(`playwright install chromium`).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import HTTPServer
from pathlib import Path

import pytest

from obdi.analysis.entities import (
    Covered,
    EntityPage,
    RuleLine,
    RuleTrial,
    entity_page_of,
)
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.entity_records import BEGINS, CONTAINS, Entity, EntityRule
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 3

#: MEASURED 2026-10-07 at 390 px: masked 844 closed and 1,202 open; shown 2,244 closed and 5,088
#: open (every one of the twelve names' folds listing its three transactions, which is all of the
#: difference and an act the owner chooses); shown with a dry run answered 2,495. The closed
#: allowance is inside three screens (2,532); the open one is about six screens.
MEASURED_CLOSED = 2300
MEASURED_OPEN = 5200

_TOWNS = (
    "southampton", "scotland", "local edinburgh", "high street kensington", "ballymacarrett",
    "llanfairpwllgwyngyll", "newcastle upon tyne", "bramblewick garden centre",
    "oakmere coffee house", "tarnside pharmacy", "lanternfield books", "zephyr stationers",
)


def _page() -> EntityPage:
    shapes = tuple(f"sainsburys {town}" for town in _TOWNS)
    by_rule = shapes[4:]
    entity = Entity(1, "Sainsburys Supermarkets Incorporated", None, shapes, by_rule=by_rule)
    child = Entity(2, "Sainsburys Bank", 1, ("sainsburys bank",))
    covers = {
        shape: tuple(
            Covered(
                date(2026, 9, 10 - n), "current-main", "", -1234 - n, "GBP",
                f"{shape.upper()} 1041 LONDON GB", "0123456789ab",
            )
            for n in range(3)
        )
        for shape in shapes
    }
    counts = {shape: 5 + index for index, shape in enumerate(shapes)}
    rules = [
        EntityRule(1, 1, BEGINS, "sainsburys"),
        EntityRule(2, 1, CONTAINS, "local sainsburys supermarkets"),
        EntityRule(3, 1, BEGINS, "sainsburys local extra"),
    ]
    page = entity_page_of(1, [entity, child], rules, counts, covers)
    assert page is not None
    return page


def _trial() -> RuleTrial:
    return RuleTrial(
        attach=tuple(f"sainsburys glasgow branch {word}" for word in "abcdef"),
        already=3,
        elsewhere=1,
    )


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    page = _page()
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(Path(os.devnull)),
        entity_page=lambda _id: page,
        entity_trial=lambda _id, _kind, _words: _trial(),
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
    browser, served, *, shown: bool, label: str, folds_open: bool = False, tried: bool = False
) -> tuple[int, int]:
    context = browser.new_context(viewport={"width": PHONE_WIDTH, "height": PHONE_HEIGHT})
    page = context.new_page()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("OBDI_INSTANCE_LABEL", "obdi")
        patch.setenv("OBDI_INSTANCE_ROLE", "production")
        page.goto(f"{served}/entity?id=1")
        if shown:
            with page.expect_navigation():
                page.click("form[action='/entity?id=1'] button")
        if tried:
            page.fill("input[name=words]", "sainsburys")
            with page.expect_navigation():
                page.click("button[formaction='/entity-rule-try']")
    if folds_open:
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("ENTITY_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"entity-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestTwelveNamesOnAPhone:
    @pytest.mark.parametrize("folds_open", [False, True])
    @pytest.mark.parametrize("shown", [False, True])
    def test_EntityPage_WithTwelveNames_FitsItsScreensAndNeverScrollsSideways(
        self, browser, served, shown, folds_open
    ):
        label = f"{'shown' if shown else 'masked'}-{'open' if folds_open else 'closed'}"
        height, overflow = _measure(
            browser, served, shown=shown, label=label, folds_open=folds_open
        )
        print(f"MEASURED {label}: {height}px")

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= (MEASURED_OPEN if folds_open else MEASURED_CLOSED), height
        if not folds_open:
            assert height <= SCREENS * PHONE_HEIGHT, height

    def test_EntityPage_WithADryRunAnswered_NeverScrollsSideways(self, browser, served):
        height, overflow = _measure(
            browser, served, shown=True, label="shown-tried", folds_open=False, tried=True
        )
        print(f"MEASURED shown-tried: {height}px")

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= MEASURED_CLOSED + 300, height


def test_RuleLines_AreCountedFromTheNamesTheEntityHolds():
    # The first rule begins with a word all twelve names begin with; the second wants "supermarkets"
    # and the third "local extra", which no name has.
    page = _page()

    assert [line.matches for line in page.rules] == [12, 0, 0]
    assert all(isinstance(line, RuleLine) for line in page.rules)
