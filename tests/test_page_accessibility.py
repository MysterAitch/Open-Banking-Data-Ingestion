"""Structural accessibility of the rendered pages, measured in a real browser.

The owner reads this on a phone, and a screen reader or a keyboard meets the same
markup. What is checked here is structure and never wording: a landmark to jump
to, a first stop that skips the navigation, a name on every control, headers on
every table, one top heading, and a target a thumb can hit. Each failed page is
named with the element, so the fix is an attribute and not a rewrite.

Pages come from the generated demo corpus (invented data, served by
`scripts/dev_corpus_ui.py`). Skipped where Playwright or its browser is missing.
"""

from __future__ import annotations

import pytest

from test_phone_layout import browser, corpus_base  # noqa: F401 - fixtures

ROUTES = [
    "/",
    "/position",
    "/accounts",
    "/actual",
    "/connections",
    "/reports",
    "/evidence",
    "/admin",
    "/coverage",
    "/import",
    "/review",
    "/statements",
    "/identity-health",
    "/period-reconciliation",
    "/actual-history",
    "/fetch-timeline",
    "/artefacts",
    "/artefact?id=1",
    "/statement-shape",
    "/declare-account",
    "/ledger?ref=synthetic-current",
    "/ledger?ref=synthetic-card",
    "/balance-chart?ref=synthetic-current",
]

#: A control with no visible label still needs a name; an element is named if any
#: of these reach it. Hidden, submit, button, reset, and image inputs name
#: themselves.
_UNNAMED_CONTROLS = """() => [...document.querySelectorAll('input, select, textarea')]
  .filter(e => !['hidden', 'submit', 'button', 'reset', 'image'].includes(e.type))
  .filter(e => {
    if (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby')) return false;
    if (e.labels && e.labels.length) return false;
    return true;
  })
  .map(e => e.tagName.toLowerCase() + '[name=' + (e.name || '') + '] in '
            + (e.form ? e.form.getAttribute('action') : 'no form'))"""

_UNHEADED_TABLES = """() => [...document.querySelectorAll('table')]
  .flatMap(t => {
    const bad = [];
    if (!t.querySelector('caption')) bad.push('no caption');
    const heads = [...t.querySelectorAll('th')];
    if (heads.some(th => !th.hasAttribute('scope'))) bad.push('th without scope');
    return bad.map(b => b + ': ' + (t.textContent || '').trim().slice(0, 40));
  })"""

#: Controls the review measured short. A text input or select is a thumb target in
#: its own right, so it is measured; inline links carry their hit area in a
#: pseudo-element and are covered by test_navigation.py.
_SHORT_CONTROLS = """() => [...document.querySelectorAll('input, select, textarea, summary')]
  .filter(e => !['hidden'].includes(e.type))
  .filter(e => e.getBoundingClientRect().height > 0)
  .map(e => {
    const box = e.type === 'checkbox' || e.type === 'radio'
      ? (e.closest('label') || e).getBoundingClientRect() : e.getBoundingClientRect();
    return [e.tagName.toLowerCase() + '[' + (e.name || e.type) + ']', Math.round(box.height)];
  })
  .filter(([, h]) => h < 44)"""

_SHORT_LINKS = """() => [...document.querySelectorAll('main a')]
  .filter(a => a.closest('table, .scroll') === null)
  .filter(a => a.getBoundingClientRect().height > 0)
  .filter(a => {
    a.scrollIntoView({block: 'center'});
    const r = a.getBoundingClientRect();
    const above = document.elementFromPoint(r.left + r.width / 2, Math.max(0, r.top - 8));
    const below = document.elementFromPoint(r.left + r.width / 2, r.bottom + 8);
    // Where two links sit on adjacent lines they share the gap between them, so
    // a point there may belong to the neighbour; it must never be bare text.
    const link = e => e !== null && e.closest('a') !== null;
    return r.height < 44 && !(link(above) && link(below));
  })
  .map(a => (a.textContent || '').trim().slice(0, 30) + ' '
            + Math.round(a.getBoundingClientRect().height))"""


def _open(browser: object, base: str, route: str) -> object:  # noqa: F811
    page = browser.new_page(viewport={"width": 390, "height": 800})  # type: ignore[attr-defined]
    page.goto(f"{base}{route}", wait_until="load")
    return page


@pytest.mark.parametrize("route", ROUTES)
def test_Page_Structure_HasOneMainOneTopHeadingAndASkipLinkAsTheFirstStop(
    browser: object, corpus_base: str, route: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, route)
    try:
        facts = page.evaluate(
            """() => ({
                mains: document.querySelectorAll('main#main').length,
                h1: document.querySelectorAll('h1').length,
                lang: document.documentElement.lang,
                first: (document.querySelector('a, button, input, select, summary') || {})
                    .className,
                skip: (document.querySelector('a.skip') || {}).hash,
                h1InMain: !!document.querySelector('main#main h1'),
            })"""
        )
    finally:
        page.close()
    assert facts["mains"] == 1, facts
    assert facts["h1"] == 1 and facts["h1InMain"], facts
    assert facts["lang"] == "en", facts
    assert facts["first"] == "skip" and facts["skip"] == "#main", facts


@pytest.mark.parametrize("route", ROUTES)
def test_Page_Controls_EachHaveAnAccessibleName(
    browser: object, corpus_base: str, route: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, route)
    try:
        unnamed = page.evaluate(_UNNAMED_CONTROLS)
    finally:
        page.close()
    assert unnamed == [], unnamed


@pytest.mark.parametrize("route", ROUTES)
def test_Page_Tables_HaveACaptionAndScopedHeaders(
    browser: object, corpus_base: str, route: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, route)
    try:
        unheaded = page.evaluate(_UNHEADED_TABLES)
    finally:
        page.close()
    assert unheaded == [], unheaded


@pytest.mark.parametrize("route", ROUTES)
def test_Page_Controls_AreAtLeastFortyFourPixelsTall(
    browser: object, corpus_base: str, route: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, route)
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        short = page.evaluate(_SHORT_CONTROLS)
        links = page.evaluate(_SHORT_LINKS)
    finally:
        page.close()
    assert short == [], short
    assert links == [], links


def test_SkipLink_WhenFocusedByKeyboard_IsVisibleAndLandsOnTheContent(
    browser: object, corpus_base: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, "/")
    try:
        before = page.evaluate(
            "() => document.querySelector('a.skip').getBoundingClientRect().width"
        )
        page.keyboard.press("Tab")
        after = page.evaluate(
            """() => {
                const a = document.activeElement;
                const r = a.getBoundingClientRect();
                return [a.className, r.width, r.height, r.top >= 0 && r.left >= 0];
            }"""
        )
        page.keyboard.press("Enter")
        target = page.evaluate("() => location.hash")
    finally:
        page.close()
    assert before <= 2, "the skip link is hidden until it is focused"
    assert after[0] == "skip" and after[1] > 40 and after[2] > 20 and after[3], after
    assert target == "#main"


@pytest.mark.parametrize("route", ["/", "/review", "/admin", "/ledger?ref=synthetic-current"])
def test_Focus_OnEveryKindOfControl_ShowsARing(
    browser: object, corpus_base: str, route: str  # noqa: F811
) -> None:
    page = _open(browser, corpus_base, route)
    try:
        page.evaluate("() => document.querySelectorAll('details').forEach(d => d.open = true)")
        missing = []
        for _stop in range(120):
            page.keyboard.press("Tab")
            state = page.evaluate(
                """() => {
                    const e = document.activeElement;
                    if (!e || e === document.body) return null;
                    // A date field's calendar button is a browser-drawn stop whose
                    // host is not :focus-visible; the field's own stops are checked.
                    if (e.type === 'date' && !e.matches(':focus-visible')) {
                        return ['skip', 'solid', 3];
                    }
                    const s = getComputedStyle(e);
                    return [e.tagName.toLowerCase() + '.' + e.className + ' ' + (e.name || ''),
                            s.outlineStyle, parseFloat(s.outlineWidth)];
                }"""
            )
            if state is None:
                break
            if state[1] == "none" or state[2] < 2:
                missing.append(state)
    finally:
        page.close()
    assert missing == [], missing
