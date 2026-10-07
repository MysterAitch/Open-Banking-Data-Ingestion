"""The strip of lanes on an account's page at phone widths: the page never scrolls sideways with
every fold shut or open, and no lane runs past the strip (skipped where no browser).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import obdi.pages.web_ledger as web_ledger
from coverage_timeline_world import CARD, MAIN, build_card, build_main
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
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
def test_AccountPage_WithTheStripOfLanes_NeverScrollsSideways(
    browser: object, base: str, width: int, ref: str, open_it: bool
) -> None:
    page = browser.new_page(viewport={"width": width, "height": 800})  # type: ignore[attr-defined]
    page.goto(f"{base}/ledger?ref={ref}", wait_until="load")
    if open_it:
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    seen = page.evaluate(
        """() => {
          const strip = document.querySelector('a.strip').getBoundingClientRect();
          const bars = [...document.querySelectorAll('a.strip .bar')]
            .map(e => e.getBoundingClientRect());
          return {page: document.documentElement.scrollWidth - window.innerWidth,
                  stripRight: strip.right, width: window.innerWidth,
                  widest: Math.max(...bars.map(b => b.right)), lanes: bars.length};
        }"""
    )
    assert seen["page"] <= 0
    assert seen["stripRight"] <= seen["width"]
    assert seen["widest"] <= seen["stripRight"] + 1, "no lane runs past the strip"
    assert seen["lanes"] >= 2, "the trust lane and at least one way in"
    page.close()
