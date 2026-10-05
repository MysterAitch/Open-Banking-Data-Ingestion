"""Coverage by source, at the size of the real household, fits a phone.

The page was ten phone screens tall for 19 accounts and 28 (account, source) pairs. The household
here is `coverage_page_world`, built through the application's doors. A SCREEN is 800 px tall, as
in `test_account_page_scale`. Skipped where Playwright or its browser is not installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import coverage_page_world as world
from section_harness import config, environment, serve_config
from test_phone_layout import _assert_fits, _measure, sync_api

SCREEN = 800
PHONE = 390
REFLOW = 320

#: Every disclosure closed. Measured at 390 px over this household: 2,381 px, which is 2.98
#: screens, 19 px under the bound (the page it replaced was 7,704 px, 9.6 screens, here; 10.2 on
#: the real household). The margin is thin on purpose: the next line added to each of 19 blocks
#: costs about a fifth of a screen, and should have to argue for it.
WHOLE_PAGE_SCREENS = 3.0


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
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("coverage-phone")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    world.write_map(root)
    db = root / "store.sqlite3"
    world.build(db)
    address, stop = serve_config(config(db))
    try:
        yield address
    finally:
        stop()
        mp.undo()


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment(monkeypatch, root)


def test_CoveragePage_ForTheRealSizedHousehold_AtPhoneWidth_FillsUnderThreeScreens(
    browser: object, base: str
) -> None:
    page = browser.new_page(viewport={"width": PHONE, "height": SCREEN})  # type: ignore[attr-defined]
    try:
        page.goto(f"{base}/coverage", wait_until="load")
        total = page.evaluate("() => document.documentElement.scrollHeight")
        assert total < WHOLE_PAGE_SCREENS * SCREEN, f"{total / SCREEN:.2f} screens"
        open_folds = page.evaluate("() => document.querySelectorAll('details[open]').length")
        assert open_folds == 0
    finally:
        page.close()


def test_CoveragePage_AtPhoneWidth_DoesNotScrollSideways(browser: object, base: str) -> None:
    page = browser.new_page(viewport={"width": PHONE, "height": SCREEN})  # type: ignore[attr-defined]
    try:
        page.goto(f"{base}/coverage", wait_until="load")
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_CoveragePage_At320PixelsWithTextAtTwoHundredPercent_DoesNotScrollSideways(
    browser: object, base: str
) -> None:
    page = browser.new_page(viewport={"width": REFLOW, "height": SCREEN})  # type: ignore[attr-defined]
    try:
        page.goto(f"{base}/coverage", wait_until="load")
        page.add_style_tag(content="html { font-size: 200%; }")
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        _assert_fits(_measure(page))
    finally:
        page.close()


def test_CoveragePage_AtPhoneWidth_TheGridShowsEveryKindColumnWithoutSidewaysScroll(
    browser: object, base: str
) -> None:
    page = browser.new_page(viewport={"width": PHONE, "height": SCREEN})  # type: ignore[attr-defined]
    try:
        page.goto(f"{base}/coverage", wait_until="load")
        right = page.evaluate(
            "() => document.querySelector('table.cov-grid').getBoundingClientRect().right"
        )
        assert right <= PHONE
    finally:
        page.close()
