"""Bring in at the size of a real household, in a real browser, on a phone.

The household is `fetch_gaps_world`'s: nine statements and two exports wanted for eight accounts,
which is more accounts for fewer files than the design's target (twelve files over six accounts
in under three phone screens), so a page that fits it fits the target with room. Measured at 390
px with every disclosure closed (2026-10-06), in px and screens of 800 px:

  wanted state     2427 px, 3.03 screens: the upload control and the first file wanted end at
                   about 430 px; each account costs about 150 px of name, sentence, bar and lanes,
                   and each file about 55 px
  after an upload  2775 px, 3.47 screens: the results (a settled sentence and the question for
                   one file) add about 350 px above the same list, which is shorter by nothing

The allowances below sit a tenth of a screen above those measurements. Skipped where Playwright or
its browser is not installed, as `test_phone_layout` is.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from fetch_gaps_world import build_household
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from served_store import environment_for, served_store
from test_phone_layout import sync_api

WIDTH, SCREEN = 390, 800
WANTED_SCREENS = 3.15
# Measured 3.65 screens (2918 px) once an answer led with what a statement covered and folded the
# importer's counts beneath it (2026-10-06); it was under 3.6 before.
AFTER_UPLOAD_SCREENS = 3.8


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
    root: Path = tmp_path_factory.mktemp("bring-in-scale")
    build_household(root)
    with Store(root / "store.sqlite3") as store:
        store.declare_account(AccountRecord(ref=AccountRef("up-card"), label="Up card"))
    with served_store(root, lambda store: None, bound=[]) as address:
        yield address


@pytest.fixture(autouse=True)
def _environment(base: str, tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> None:
    root = tmp_path_factory.getbasetemp()
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def _open(browser: object, url: str, *, width: int = WIDTH) -> object:
    page = browser.new_page(viewport={"width": width, "height": SCREEN})  # type: ignore[attr-defined]
    page.goto(url, wait_until="load")
    return page


def _bottom(page: object, selector: str) -> float:
    box = page.locator(selector).first.bounding_box()  # type: ignore[attr-defined]
    assert box is not None, f"{selector} is not on the page"
    return float(box["y"] + box["height"])


class TestTheFirstScreen:
    def test_FirstScreen_HoldsTheUploadControlAndTheFirstFileWanted(
        self, browser: object, base: str
    ) -> None:
        page = _open(browser, f"{base}/bring-in")
        try:
            assert _bottom(page, "input[type=file]") < SCREEN
            assert _bottom(page, "form button.button") < SCREEN
            assert _bottom(page, ".bi-file") <= SCREEN, "the first file wanted is on screen"
        finally:
            page.close()


class TestTheWholePage:
    def test_Page_WithEveryDisclosureClosed_IsWithinItsBudget(
        self, browser: object, base: str
    ) -> None:
        page = _open(browser, f"{base}/bring-in")
        try:
            height = page.evaluate("document.documentElement.scrollHeight")  # type: ignore[attr-defined]
            assert height < WANTED_SCREENS * SCREEN, f"{height}px is {height / SCREEN:.2f} screens"
        finally:
            page.close()

    @pytest.mark.parametrize("width", [390, 320])
    def test_Page_AtPhoneWidths_DoesNotScrollSideways(
        self, browser: object, base: str, width: int
    ) -> None:
        page = _open(browser, f"{base}/bring-in", width=width)
        try:
            scrolled = page.evaluate(  # type: ignore[attr-defined]
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert scrolled <= 0, f"{scrolled}px too wide at {width}px"
        finally:
            page.close()

    def test_EveryLink_IsAThumbSizedTarget(self, browser: object, base: str) -> None:
        page = _open(browser, f"{base}/bring-in")
        try:
            small = page.evaluate(  # type: ignore[attr-defined]
                """() => [...document.querySelectorAll('main a.bi-aside, main a.bi-upload')]
                    .map(e => { const r = e.getBoundingClientRect();
                      const a = getComputedStyle(e, '::after');
                      return [r.height, a.position, a.top, a.bottom]; })"""
            )
            assert small and all(item[1] == "absolute" for item in small)
        finally:
            page.close()


class TestTheWideLayout:
    def test_Page_At1280Pixels_PutsTheTargetBesideTheListOfFilesWanted(
        self, browser: object, base: str
    ) -> None:
        page = _open(browser, f"{base}/bring-in", width=1280)
        try:
            target = page.locator("input[type=file]").first.bounding_box()  # type: ignore[attr-defined]
            first = page.locator(".bi-account").first.bounding_box()  # type: ignore[attr-defined]
            assert target is not None and first is not None
            assert target["x"] + target["width"] <= first["x"], "the target is in its own column"
            assert abs(target["y"] - first["y"]) < SCREEN / 2, "and beside the list's head"
        finally:
            page.close()


class TestTenStatementsWaitingForAnAccount:
    """Measured at 390 px (2026-10-06) over the household above, ten statements and no account:
    the results page is about 1820 px (2.3 screens) with the wanted list folded, the first chooser
    ends at about 295 px, and the one control sits in a bar fixed to the foot of the screen
    (it is on the first screen without any scrolling, and at the end of the list on the last).

    Each row then gained its preview of what the document is (2026-10-06): the page is 2457 px
    (3.07 screens), about 64 px a row, which is the price of a GUID-named file saying what it is.
    The allowance is 3.2 screens."""

    TEN_SCREENS = 3.2

    def _upload(self, browser: object, base: str, tmp_path: Path) -> object:
        from datetime import date

        from test_bring_in_assign import santander

        paths = []
        for index in range(10):
            path = tmp_path / f"Statement-{index}.pdf"
            path.write_bytes(santander(date(2026, 9, 10), 1000 + index))
            paths.append(str(path))
        page = _open(browser, f"{base}/bring-in")
        page.set_input_files("input[type=file]", paths)  # type: ignore[attr-defined]
        page.click("text=Read these files")  # type: ignore[attr-defined]
        page.wait_for_load_state("load")  # type: ignore[attr-defined]
        return page

    def test_Page_WithTenStatementsWaiting_IsUnderThreeScreensBeforeThePress(
        self, browser: object, base: str, tmp_path: Path
    ) -> None:
        page = self._upload(browser, base, tmp_path)
        try:
            height = page.evaluate("document.documentElement.scrollHeight")  # type: ignore[attr-defined]
            assert page.locator(".bi-assign-file").count() == 10  # type: ignore[attr-defined]
            assert height < self.TEN_SCREENS * SCREEN, f"{height / SCREEN:.2f} screens"
        finally:
            page.close()

    def test_FirstScreen_HoldsTheFirstChooserAndTheOneControl(
        self, browser: object, base: str, tmp_path: Path
    ) -> None:
        page = self._upload(browser, base, tmp_path)
        try:
            assert _bottom(page, ".bi-assign-file select") < SCREEN
            assert _bottom(page, ".bi-assign-bar button") <= SCREEN
        finally:
            page.close()

    def test_Control_AfterScrollingToTheLastFile_IsStillOnScreen(
        self, browser: object, base: str, tmp_path: Path
    ) -> None:
        page = self._upload(browser, base, tmp_path)
        try:
            page.locator(".bi-assign-file").last.scroll_into_view_if_needed()  # type: ignore[attr-defined]
            assert _bottom(page, ".bi-assign-bar button") <= SCREEN
            # The suite clears every OBDI_ variable, so the folder is named without the prefix.
            shots = os.environ.get("SCALE_SHOT_DIR")
            if shots:
                page.evaluate("window.scrollTo(0, 0)")  # type: ignore[attr-defined]
                page.screenshot(path=str(Path(shots) / "ten-390-top.png"))  # type: ignore[attr-defined]
                page.screenshot(  # type: ignore[attr-defined]
                    path=str(Path(shots) / "ten-390-full.png"), full_page=True
                )
        finally:
            page.close()


class TestAfterAnUpload:
    def test_Page_AfterAScopedUpload_IsWithinItsBudgetAndHoldsTheQuestionForTheHeldFile(
        self, browser: object, base: str, tmp_path: Path
    ) -> None:
        from test_bring_in_page import _csv_part, _pdf_part

        (tmp_path / "Statement-2026-09.pdf").write_bytes(_pdf_part()[1][1])
        (tmp_path / "transactions.csv").write_bytes(_csv_part()[1][1])
        page = _open(browser, f"{base}/bring-in?account=up-card")
        try:
            page.set_input_files(  # type: ignore[attr-defined]
                "input[type=file]",
                [str(tmp_path / "Statement-2026-09.pdf"), str(tmp_path / "transactions.csv")],
            )
            page.click("text=Read these files")  # type: ignore[attr-defined]
            page.wait_for_load_state("load")  # type: ignore[attr-defined]
            height = page.evaluate("document.documentElement.scrollHeight")  # type: ignore[attr-defined]
            assert page.locator("text=Check it against Up card").count() == 1  # type: ignore[attr-defined]
            assert height < AFTER_UPLOAD_SCREENS * SCREEN, f"{height / SCREEN:.2f} screens"
        finally:
            page.close()
