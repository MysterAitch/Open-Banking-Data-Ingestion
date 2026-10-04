# ruff: noqa: E501
# The scripts run in the browser are single expressions that read best unwrapped.
"""The Actual page, laid out in a real browser at the sizes it is read at.

It is read on a phone to answer one question, so what is measured is that the answer and the
one press it names are on the first screen, that the page is short when everything agrees,
that it does not scroll sideways at the narrowest width or at double-size text, that every
control is a thumb-sized target, and that the controls that delete from Actual are inside
the danger zone and nowhere else. Each state is built from invented results through the page's
own entry point (`actual_states`), so nothing here reads the clock.

Skipped where Playwright or its browser is not installed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from actual_states import STATES, render_state

sync_api = pytest.importorskip("playwright.sync_api")

PHONE = {"width": 390, "height": 800}
NARROWEST = {"width": 320, "height": 800}
DESKTOP = {"width": 1280, "height": 900}

#: The press each state's verdict names, by the label on its button; None where none is named.
NAMED_PRESS = {
    "agrees": None,
    "differs_three_orphans": "Bring Actual into line",
    "push_after_audit": "Audit Actual now",
    "request_waiting_applier_silent": None,
    "request_waiting_applier_alive": None,
    "push_failed": "Push to Actual now",
    "nothing_pushed": "Push to Actual now",
    "not_configured": None,
}

#: The verdict each state must open with.
HEADLINES = {
    "agrees": "Actual agrees with obdi",
    "differs_three_orphans": "Actual differs from obdi in 1 account",
    "push_after_audit": "Actual has not been checked since the last push",
    "request_waiting_applier_silent": "The applier has not checked the queue since",
    "request_waiting_applier_alive": "A request is waiting for the applier",
    "push_failed": "The last push failed",
    "nothing_pushed": "Nothing has been pushed yet",
    "not_configured": "Actual is not configured on this instance",
}

#: The page when disclosures are closed, in screens of the phone's height.
MAX_SCREENS_ALL_AGREE = 3


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch()
        except sync_api.Error as exc:
            pytest.skip(f"no browser available: {exc}")
        yield launched
        launched.close()


def _open(browser: object, state: str, size: dict[str, int], zoom: bool = False):
    page = browser.new_page(viewport=size)  # type: ignore[attr-defined]
    page.set_content(render_state(state))
    if zoom:
        page.add_style_tag(content="html { font-size: 200%; }")
    return page


@pytest.mark.parametrize("state", list(STATES))
def test_ActualPage_InEachState_OpensWithTheVerdictAndItsPressOnTheFirstScreen(
    browser: object, state: str
) -> None:
    page = _open(browser, state, PHONE)
    try:
        verdict = page.locator("h2#verdict")
        assert HEADLINES[state] in verdict.inner_text()
        assert verdict.bounding_box()["y"] + verdict.bounding_box()["height"] <= PHONE["height"]
        press = NAMED_PRESS[state]
        if press is not None:
            button = page.get_by_role("button", name=press)
            box = button.bounding_box()
            assert box is not None and box["y"] + box["height"] <= PHONE["height"], press
            assert button.evaluate("b => getComputedStyle(b).backgroundColor") != "rgba(0, 0, 0, 0)"
    finally:
        page.close()


@pytest.mark.parametrize("state", list(STATES))
def test_ActualPage_InEachState_FillsOnlyOneButton(browser: object, state: str) -> None:
    page = _open(browser, state, PHONE)
    try:
        filled = page.evaluate(
            """() => [...document.querySelectorAll('main button.button')]
                .filter(b => !b.closest('.danger-zone') && !b.disabled
                    && getComputedStyle(b).backgroundColor !== 'rgba(0, 0, 0, 0)')
                .map(b => b.innerText)"""
        )
        expected = NAMED_PRESS[state]
        assert filled == ([expected] if expected else []), filled
    finally:
        page.close()


def test_ActualPage_WithSeventeenAgreeingAccounts_IsUnderThreeScreensWithDisclosuresClosed(
    browser: object,
) -> None:
    page = _open(browser, "agrees", PHONE)
    try:
        assert page.locator("details[open]").count() == 0
        height = page.evaluate("document.documentElement.scrollHeight")
        assert height < MAX_SCREENS_ALL_AGREE * PHONE["height"], height
    finally:
        page.close()


def test_ActualPage_WithSeventeenAgreeingAccounts_ListsNoAccountUntilTheFoldIsOpened(
    browser: object,
) -> None:
    page = _open(browser, "agrees", PHONE)
    try:
        listed = page.locator(".agree-list li")
        assert listed.count() == 17
        assert not listed.first.is_visible()
        page.get_by_text("17 accounts agree", exact=True).click()
        assert listed.first.is_visible()
    finally:
        page.close()


def test_ActualPage_WhenOneAccountDiffers_ListsOnlyThatAccountOpen(browser: object) -> None:
    page = _open(browser, "differs_three_orphans", PHONE)
    try:
        assert page.locator(".differ").count() == 1
        assert page.locator(".differ").first.is_visible()
        assert "halifax-current-account" in page.locator(".differ").first.inner_text()
        assert not page.locator(".agree-list li").first.is_visible()
    finally:
        page.close()


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize(("size", "zoom"), [(PHONE, False), (NARROWEST, False), (NARROWEST, True)])
def test_ActualPage_InEachState_DoesNotScrollSideways(
    browser: object, state: str, size: dict[str, int], zoom: bool
) -> None:
    page = _open(browser, state, size, zoom)
    try:
        # Everything open, because a fold that overflows when opened is still a fault.
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        scroll, client = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        assert scroll <= client, (scroll, client)
    finally:
        page.close()


@pytest.mark.parametrize("state", list(STATES))
def test_ActualPage_InEachState_EveryControlIsAtLeastFortyFourPixelsTall(
    browser: object, state: str
) -> None:
    page = _open(browser, state, PHONE)
    try:
        short = page.evaluate(
            """() => [...document.querySelectorAll('main button, main summary, a.button, nav a, main input[type=checkbox], label.tick')]
                .filter(e => e.getClientRects().length && !e.disabled)
                .map(e => [e.innerText.trim().slice(0, 30), e.closest('label') ? e.closest('label').getBoundingClientRect().height : e.getBoundingClientRect().height])
                .filter(([, h]) => h < 44)"""
        )
        assert short == [], short
        # A text link is thumb-sized by the reach of its pseudo-element: probe above and below.
        links = page.evaluate(
            """() => [...document.querySelectorAll('main a')].filter(a => a.getClientRects().length).map(a => {
                a.scrollIntoView({block: 'center'});
                const r = a.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
                const hit = dy => { const e = document.elementFromPoint(x, y + dy); return !!e && e.closest('a') === a; };
                return [a.innerText.trim(), hit(-21) && hit(21)];
            })"""
        )
        assert all(reach for _, reach in links), links
    finally:
        page.close()


@pytest.mark.parametrize("state", [s for s in STATES if s != "not_configured"])
def test_ActualPage_InEachState_DeletingControlsAreInsideTheDangerZoneAndNowhereElse(
    browser: object, state: str
) -> None:
    page = _open(browser, state, PHONE)
    try:
        outside, inside_other = page.evaluate(
            """() => {
                const deleting = ['/prune-actual', '/empty-actual'];
                const outside = [...document.querySelectorAll('form')]
                    .filter(f => deleting.includes(f.getAttribute('action')) && !f.closest('.danger-zone'))
                    .map(f => f.getAttribute('action'));
                const other = [...document.querySelectorAll('.danger-zone form')]
                    .filter(f => !deleting.includes(f.getAttribute('action')))
                    .map(f => f.getAttribute('action'));
                return [outside, other];
            }"""
        )
        assert outside == []
        assert inside_other == []
        assert page.locator(".danger-zone summary", has_text="Empty Actual completely").count() == 1
        assert page.locator(".danger-zone summary", has_text="Remove orphaned imports").count() == 1
    finally:
        page.close()


def test_ActualPage_WhenNotConfigured_OffersNoDeletingControlAtAll(browser: object) -> None:
    page = _open(browser, "not_configured", PHONE)
    try:
        assert page.locator(".danger-zone").count() == 0
        assert (
            page.locator("form[action='/empty-actual'], form[action='/prune-actual']").count() == 0
        )
    finally:
        page.close()


def test_ActualPage_AtDesktopWidth_PutsTheVerdictLeftAndTheResultsRight(browser: object) -> None:
    page = _open(browser, "differs_three_orphans", DESKTOP)
    try:
        verdict = page.locator("h2#verdict").bounding_box()
        results = page.locator(".newest-audit h2").bounding_box()
        assert verdict is not None and results is not None
        assert results["x"] > verdict["x"] + verdict["width"] - 1, (verdict, results)
        assert abs(results["y"] - verdict["y"]) < 80
        zone = page.locator(".danger-zone").bounding_box()
        assert zone is not None and zone["y"] > results["y"]
    finally:
        page.close()


def test_ActualPage_AtPhoneWidth_StacksTheResultsBelowTheVerdict(browser: object) -> None:
    page = _open(browser, "differs_three_orphans", PHONE)
    try:
        verdict = page.locator("h2#verdict").bounding_box()
        results = page.locator(".newest-audit h2").bounding_box()
        assert verdict is not None and results is not None
        assert results["y"] > verdict["y"]
        assert abs(results["x"] - verdict["x"]) < 40
    finally:
        page.close()
