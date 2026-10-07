"""The coverage timeline is usable on a phone: the page never scrolls sideways, the chart does.

The household is `coverage_timeline_world`, served by the real handler with today fixed at
2026-10-05. At 14 units a day the default window of 97 days is 1,406 units wide, wider than any
phone, so the chart's own region must scroll and the page must not. The lane labels sit outside
that region, so scrolling the chart leaves them where they are.

Skipped where Playwright or its browser is not installed, as `test_phone_layout` is.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from coverage_timeline_serve import served
from coverage_timeline_world import TODAY, build_household

WIDTHS = [360, 390]

sync_api = pytest.importorskip("playwright.sync_api")


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
def base(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db: Path = build_household(tmp_path_factory.mktemp("timeline-phone"))
    with served(db, TODAY) as address:
        yield address


def _open(browser: object, base: str, width: int, path: str) -> object:
    page = browser.new_page(viewport={"width": width, "height": 780})  # type: ignore[attr-defined]
    page.goto(f"{base}{path}", wait_until="load")
    return page


@pytest.mark.parametrize("width", WIDTHS)
def test_Page_AtPhoneWidth_NeverScrollsSidewaysWhileTheChartRegionDoes(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=main")  # type: ignore[attr-defined]
    measured = page.evaluate(
        "() => ({page: document.documentElement.scrollWidth - window.innerWidth,"
        " chart: document.querySelector('.cov-scroll').scrollWidth,"
        " shown: document.querySelector('.cov-scroll').clientWidth})"
    )
    assert measured["page"] <= 0
    assert measured["chart"] > measured["shown"]
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_Labels_WhenTheChartScrolls_StayWhereTheyAre(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=main")  # type: ignore[attr-defined]
    before = page.evaluate("document.querySelector('.cov-labels').getBoundingClientRect().left")
    page.evaluate("document.querySelector('.cov-scroll').scrollLeft = 600")
    after = page.evaluate("document.querySelector('.cov-labels').getBoundingClientRect().left")
    assert before == after
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_LaneLabels_ShareTheBaselineOfTheirLanes(browser: object, base: str, width: int) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=main")  # type: ignore[attr-defined]
    heights = page.evaluate(
        "() => [...document.querySelectorAll('.cov-label')].map(e => Math.round("
        "e.getBoundingClientRect().height))"
    )
    # Axis, verification, feed, aggregator, export, typed, issues, axis: the same rows the
    # chart is drawn in.
    assert heights == [34, 32, 38, 38, 38, 38, 30, 34]
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_HouseholdPage_AtPhoneWidth_NeverScrollsSideways(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline")  # type: ignore[attr-defined]
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    page.close()
