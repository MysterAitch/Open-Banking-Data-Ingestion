"""The compact timeline on an account's page at phone widths: the page never scrolls sideways
with it folded or open, and the drawing is fitted to the page's width (skipped where no browser).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import obdi.web_ledger as web_ledger
from coverage_timeline_world import CARD, MAIN, build_card, build_main
from obdi.statement_terms import keep_statement_readings
from obdi.store import Store
from served_store import environment_for, served_store
from test_account_page_timeline import _Fixed

sync_api = pytest.importorskip("playwright.sync_api")

WIDTHS = [360, 390]


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
    return tmp_path_factory.mktemp("account-timeline-browser")


@pytest.fixture(scope="module")
def base(root: Path) -> Iterator[str]:
    patch = pytest.MonkeyPatch()
    patch.setattr(web_ledger, "datetime", _Fixed)

    def build(store: Store) -> None:
        build_main(root, store)
        build_card(root, store)
        keep_statement_readings(store)

    try:
        with served_store(root, build, bound=[MAIN, CARD]) as address:
            yield address
    finally:
        patch.undo()


@pytest.fixture(autouse=True)
def _environment(base: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("ref", [MAIN, CARD])
@pytest.mark.parametrize("open_it", [False, True])
def test_AccountPage_WithTheCompactTimeline_NeverScrollsSideways(
    browser: object, base: str, width: int, ref: str, open_it: bool
) -> None:
    page = browser.new_page(viewport={"width": width, "height": 800})  # type: ignore[attr-defined]
    page.goto(f"{base}/ledger?ref={ref}", wait_until="load")
    if open_it:
        page.evaluate("document.querySelector('.cov-compact > details').open = true")
    seen = page.evaluate(
        """() => {
          const svg = document.querySelector('.cov-compact-svg').getBoundingClientRect();
          const box = document.querySelector('.cov-compact').getBoundingClientRect();
          return {page: document.documentElement.scrollWidth - window.innerWidth,
                  svgRight: svg.right, boxRight: box.right};
        }"""
    )
    assert seen["page"] <= 0
    assert seen["svgRight"] <= seen["boxRight"] + 1
    page.close()
