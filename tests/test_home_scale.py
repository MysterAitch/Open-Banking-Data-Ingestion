"""The home page at the size of a real instance, in a real browser, on a phone.

The invented corpus has three accounts, and a page that is fine for three was sixteen screens
tall for twenty. This builds twenty (`home_world`, through the real doors) and measures what a
person meets: the verdict, the four status lines, and the first thing needing attention are on
the first screen; the whole page is under five screens with every disclosure closed; nothing
scrolls sideways at 390 pixels, or at 320 with text at 200%; and every row is a target a thumb
can hit.

Skipped where Playwright or its browser is not installed, as test_phone_layout is.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

import home_world as world
from obdi.cli import build_web_config
from obdi.web import AuthorisationSession, ConnectionHandler
from test_phone_layout import _ENV, _assert_fits, _environment_for, _measure, sync_api

WIDTH, HEIGHT = 390, 800
SCREENS = 5
MINIMUM_TARGET = 44


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def _serve(root: Path, *, trouble: bool) -> Iterator[str]:
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(_environment_for(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    db = root / "store.sqlite3"
    world.build_scale_world(db, trouble=trouble)
    config = build_web_config(db)
    assert config is not None
    handler = type(
        "HomeHandler", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()}
    )
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(scope="module")
def troubled(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _serve(tmp_path_factory.mktemp("home-scale-troubled"), trouble=True)


@pytest.fixture(scope="module")
def clear(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _serve(tmp_path_factory.mktemp("home-scale-clear"), trouble=False)


def _open(browser: object, url: str, *, width: int = WIDTH, height: int = HEIGHT) -> object:
    page = browser.new_page(viewport={"width": width, "height": height})  # type: ignore[attr-defined]
    page.goto(url, wait_until="load")
    return page


def _box(page: object, selector: str) -> dict[str, float]:
    box = page.locator(selector).first.bounding_box()  # type: ignore[attr-defined]
    assert box is not None, f"{selector} is not on the page"
    return box


class TestTheFirstScreen:
    def test_FirstScreen_WithTwentyAccountsAndTroubles_HoldsTheVerdictTheFourLinesAndTheFirstItem(
        self, browser: object, troubled: str
    ) -> None:
        page = _open(browser, f"{troubled}/")
        try:
            verdict = _box(page, "#verdict")
            assert verdict["y"] + verdict["height"] < HEIGHT
            rows = page.locator("a.status-row")
            assert rows.count() == 4
            for index in range(4):
                box = rows.nth(index).bounding_box()
                assert box is not None
                assert box["y"] + box["height"] <= HEIGHT, f"status line {index}"
            first_item = _box(page, ".tier .attention li")
            assert first_item["y"] + 40 <= HEIGHT, "the first item starts on the first screen"
        finally:
            page.close()

    def test_FirstScreen_WithNothingNeedingAttention_HoldsThePositiveVerdictInTeal(
        self, browser: object, clear: str
    ) -> None:
        page = _open(browser, f"{clear}/")
        try:
            verdict = page.locator("#verdict")
            assert "Everything checked is in order." in verdict.inner_text()
            box = _box(page, "#verdict")
            assert box["y"] + box["height"] < HEIGHT
            assert page.locator(".tier").count() == 0
        finally:
            page.close()


class TestTheWholePage:
    @pytest.mark.parametrize("state", ["troubled", "clear"])
    def test_Page_WithTwentyAccountsAndEveryDisclosureClosed_IsUnderFiveScreens(
        self, browser: object, request: pytest.FixtureRequest, state: str
    ) -> None:
        base = request.getfixturevalue(state)
        page = _open(browser, f"{base}/")
        try:
            height = page.evaluate("document.documentElement.scrollHeight")
            assert height < SCREENS * HEIGHT, f"{height}px is {height / HEIGHT:.1f} screens"
        finally:
            page.close()

    @pytest.mark.parametrize("state", ["troubled", "clear"])
    def test_Page_At390Pixels_DoesNotScrollSideways(
        self, browser: object, request: pytest.FixtureRequest, state: str
    ) -> None:
        base = request.getfixturevalue(state)
        page = _open(browser, f"{base}/")
        try:
            _assert_fits(_measure(page))
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
            _assert_fits(_measure(page))
        finally:
            page.close()

    @pytest.mark.parametrize("state", ["troubled", "clear"])
    def test_Page_At320PixelsWithTextAtTwoHundredPercent_DoesNotScrollSideways(
        self, browser: object, request: pytest.FixtureRequest, state: str
    ) -> None:
        base = request.getfixturevalue(state)
        page = _open(browser, f"{base}/", width=320)
        try:
            page.add_style_tag(content="html { font-size: 200%; }")
            _assert_fits(_measure(page))
            page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
            _assert_fits(_measure(page))
        finally:
            page.close()


class TestEveryRowIsATarget:
    def test_Rows_AndStatusLinesAndDisclosureButtons_AreAtLeastFortyFourPixelsEachWay(
        self, browser: object, troubled: str
    ) -> None:
        page = _open(browser, f"{troubled}/")
        try:
            for selector in ("a.acct-row", "a.status-row", ".acct-more > summary"):
                boxes = page.locator(selector).evaluate_all(
                    "els => els.filter(e => e.checkVisibility()).map(e => {"
                    " const r = e.getBoundingClientRect(); return [r.width, r.height]; })"
                )
                assert boxes, f"no {selector} was visible"
                for width, height in boxes:
                    assert width >= MINIMUM_TARGET and height >= MINIMUM_TARGET, (
                        f"{selector} is {width}x{height}"
                    )
        finally:
            page.close()

    def test_Rows_WithTwentyAccounts_ShowSixteenLiveAccountsAndFoldTheArchivedSpaces(
        self, browser: object, troubled: str
    ) -> None:
        page = _open(browser, f"{troubled}/")
        try:
            visible = page.locator("a.acct-row").evaluate_all(
                "els => els.filter(e => e.checkVisibility()).length"
            )
            assert visible == world.TWENTY - len(world.ARCHIVED_SPACES)
            assert page.locator("summary", has_text="4 archived Spaces").count() == 1
        finally:
            page.close()
