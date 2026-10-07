"""The Entities page with thirty names fits a phone: three screens, no sideways scroll.

THE BUDGET: at 390 px by 844 px, thirty names (eight groups of two or three offered, two entities
of three names each, and the rest under no entity) with the longest names a bank prints, the page
is at most three screens tall with the folds closed, and at most six with every fold opened.
Measured with values masked and with them shown, the taller of which is held. The allowance is
recorded beside the measurements in `MEASURED_CLOSED` and `MEASURED_OPEN`.

Set ENTITIES_SHOTS_DIR to keep a picture of each rendering for a person to look at. Skipped
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

from obdi.analysis.entities import Covered, EntitiesView, view_of
from obdi.ingest.connections import ConnectionStore
from obdi.ingest.entity_records import Entity
from obdi.pages.web import AuthorisationSession, ConnectionHandler, WebConfig

sync_api = pytest.importorskip("playwright.sync_api")

PHONE_WIDTH = 390
PHONE_HEIGHT = 844
SCREENS = 3
SCREENS_OPEN = 6

#: The tallest rendering of the thirty, in pixels, as last measured; the budget is SCREENS tall.
#: Measured with the first four of eight groups open and the rest folded: masked 1,102 closed and
#: 1,346 open; shown 2,241 closed and 4,170 open. With six groups open, shown closed was 2,649,
#: over three screens, so two groups moved into the fold. Opened, the fold lists every free name
#: as a tick row of its own (a thumb-sized target each), which is the whole of the extra height;
#: five screens is allowed for an act the owner chooses to make.
#: Re-measured with each name's transactions listed in a fold of its own, the owner entities' fold
#: of rename, fold-into, and make-its-own, and a "newest days" fold on each masked group: masked
#: 1,390 closed and 1,826 open; shown 2,250 closed and 4,179 open (the count of each name is the
#: summary of its fold, so a closed fold adds no line).
#: Re-measured with the "and any name that begins with ..." tick on each proposed group (one more
#: row per group, two lines at this width once a name is long): shown 2,486 closed and 4,651 open,
#: the rest unchanged. Closed stays inside three screens; opened is 431 px over the old five, so
#: the open allowance is six screens - the tick is a control the owner asked for on every group,
#: and the cost of eight of them is all of the difference.
#: The "New entity" form sits inside the fold of names under no entity, so closed is unchanged;
#: opened it adds 174 px (shown 4,825 open), and the open allowance is 4,900.
MEASURED_CLOSED = 2500
MEASURED_OPEN = 4900

_RETAILERS = (
    "fernhollow grocers",
    "marlowe bakery",
    "zephyr stationers",
    "quillon hardware",
    "bramblewick garden centre",
    "tarnside pharmacy",
    "oakmere coffee house",
    "lanternfield books",
)
_TOWNS = ("london", "reading", "leeds")


def _view() -> EntitiesView:
    counts: dict[str, int] = {}
    for retailer in _RETAILERS:
        for town in _TOWNS[: 3 if len(counts) % 2 else 2]:
            counts[f"{retailer} {town}"] = 3 + len(counts)
    gathered = [
        Entity(1, "Sorrel Utilities", None, tuple(f"sorrel utilities {t}" for t in _TOWNS)),
        Entity(2, "Hartwell Cinemas", None, tuple(f"hartwell cinemas {t}" for t in _TOWNS)),
    ]
    for entity in gathered:
        for shape in entity.shapes:
            counts[shape] = 4
    for index in range(30 - len(counts)):
        counts[f"singular{'abcdefghij'[index]} merchant{'klmnopqrst'[index]}"] = 1
    assert len(counts) == 30
    # Each name lists up to three transactions, so the folds the page offers on every name are
    # part of what is measured (they are closed: the count is their summary).
    covers = {
        shape: tuple(
            Covered(
                date(2026, 9, 10 - n), "current-main", "", -1234 - n, "GBP",
                f"{shape.upper()} 1041 LONDON GB", "0123456789ab",
            )
            for n in range(min(3, count))
        )
        for shape, count in counts.items()
    }
    return view_of(counts, gathered, None, covers)


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    view = _view()
    config = WebConfig(
        client_id="c",
        client_secret="tlcs_live_abcdefghij1234567890",
        redirect_uri="https://obdi.example.com/callback",
        connection_store=ConnectionStore(Path(os.devnull)),
        entities_data=lambda: view,
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
        page.goto(f"{served}/entities")
        if shown:
            with page.expect_navigation():
                page.click("form[action='/entities'] button")
    if folds_open:
        page.evaluate("document.querySelectorAll('details.ent-more').forEach(d => d.open = true)")
    height = page.evaluate("document.documentElement.scrollHeight")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    shots = os.environ.get("ENTITIES_SHOTS_DIR")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"entities-{label}.png"), full_page=True)
    context.close()
    return int(height), int(overflow)


class TestThirtyNamesOnAPhone:
    @pytest.mark.parametrize("folds_open", [False, True])
    @pytest.mark.parametrize("shown", [False, True])
    def test_EntitiesPage_WithThirtyNames_FitsItsScreensAndNeverScrollsSideways(
        self, browser, served, shown, folds_open
    ):
        label = f"{'shown' if shown else 'masked'}-{'open' if folds_open else 'closed'}"
        height, overflow = _measure(
            browser, served, shown=shown, label=label, folds_open=folds_open
        )
        print(f"MEASURED {label}: {height}px")

        assert overflow <= 0, "the page scrolls sideways"
        assert height <= (SCREENS_OPEN if folds_open else SCREENS) * PHONE_HEIGHT, height
        assert height <= (MEASURED_OPEN if folds_open else MEASURED_CLOSED), height
