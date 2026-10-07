"""The collapsed timeline in a real browser at phone widths (skipped where none is installed).

The long account (`coverage_timeline_world.build_long`) is served with today fixed at 2026-10-05.
Its twelve-month window has one quiet stretch of 323 days between its margins, so the chart is
drawn in 42 days and one cut. Where a chart is still wider than the screen it opens at its newest
end: the first thing in view is today, with the lane labels still on the left.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from coverage_timeline_serve import served
from coverage_timeline_world import TODAY, build_long

WIDTHS = [360, 390]

sync_api = pytest.importorskip("playwright.sync_api")

OVERLAP = """
(a, b) => !(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top)
"""


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
    db: Path = build_long(tmp_path_factory.mktemp("long-browser"))
    with served(db, TODAY) as address:
        yield address


def _open(browser: object, base: str, width: int, path: str) -> object:
    page = browser.new_page(viewport={"width": width, "height": 780})  # type: ignore[attr-defined]
    page.goto(f"{base}{path}", wait_until="load")
    return page


@pytest.mark.parametrize("width", WIDTHS)
def test_Chart_WhenWiderThanTheScreen_OpensShowingTodayWithTheLabelsStillOnTheLeft(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=long&days=all")  # type: ignore[attr-defined]
    seen = page.evaluate(
        """() => {
          const box = document.querySelector('.cov-scroll').getBoundingClientRect();
          const today = document.querySelector('line.cov-today').getBoundingClientRect();
          const labels = document.querySelector('.cov-labels').getBoundingClientRect();
          const scroller = document.querySelector('.cov-scroll');
          return {today: [today.left, today.right], box: [box.left, box.right],
                  labels: [labels.left, labels.right],
                  wide: scroller.scrollWidth > scroller.clientWidth,
                  page: document.documentElement.scrollWidth - window.innerWidth};
        }"""
    )
    assert seen["wide"], "every day is drawn, so this chart must be wider than a phone"
    assert seen["box"][0] <= seen["today"][0] and seen["today"][1] <= seen["box"][1]
    assert seen["labels"][1] <= seen["box"][0] + 1
    assert seen["page"] <= 0
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_Chart_WhenCollapsed_ShowsItsBreakAndItsOwnNewestEndAtPhoneWidth(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=long")  # type: ignore[attr-defined]
    seen = page.evaluate(
        """() => {
          const box = document.querySelector('.cov-scroll').getBoundingClientRect();
          const today = document.querySelector('line.cov-today').getBoundingClientRect();
          return {breaks: document.querySelectorAll('.cov-break-hit').length,
                  inView: box.left <= today.left && today.right <= box.right,
                  page: document.documentElement.scrollWidth - window.innerWidth};
        }"""
    )
    assert seen["breaks"] == 1
    assert seen["inView"]
    assert seen["page"] <= 0
    page.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_BreakLabels_AtPhoneWidth_NeverOverlapATickLabelOrTheOtherCut(
    browser: object, base: str, width: int
) -> None:
    page = _open(browser, base, width, "/coverage-timeline?ref=long")  # type: ignore[attr-defined]
    page.evaluate("document.querySelector('.cov-scroll').scrollLeft = -100000")
    clashes = page.evaluate(
        f"""() => {{
          const hit = {OVERLAP};
          const labels = [...document.querySelectorAll('.cov-break-label')]
            .map(e => e.getBoundingClientRect());
          const ticks = [...document.querySelectorAll('.cov-svg text[font-size]')]
            .map(e => [e.textContent, e.getBoundingClientRect()]);
          const out = [];
          for (const l of labels) for (const [t, r] of ticks) if (hit(l, r)) out.push(t);
          return {{count: labels.length, out}};
        }}"""
    )
    assert clashes["count"] >= 2
    assert clashes["out"] == []
    page.close()
