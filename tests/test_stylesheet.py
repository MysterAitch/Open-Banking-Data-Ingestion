"""The shared stylesheet is legible in both colour schemes and says nothing a masked page must not.

Colour is defined once, as custom properties with a light and a dark value, so a
change of direction is a change of tokens. These tests read those tokens and
hold the contrast of every pairing the components use, because an interface
read on a phone in sunlight and again in bed is read in both schemes, and a
pairing that passes in one and fails in the other is the usual way for legibility
to be lost. Contrast is computed here from the WCAG definition of relative
luminance, so the guard does not depend on any tool's idea of a pass.
"""

from __future__ import annotations

import re

import pytest

from stylesheet_support import (
    dark_tokens,
    light_tokens,
    stylesheet,
    without_token_blocks,
)


def _channel(value: int) -> float:
    unit = value / 255
    return unit / 12.92 if unit <= 0.03928 else ((unit + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    assert re.fullmatch(r"#[0-9a-f]{6}", colour), f"a token colour is six hex digits: {colour!r}"
    red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


#: Text on the two grounds a page is made of.
_GROUNDS = ("--paper", "--card")
_TEXT_ON_GROUND = [
    (fg, bg)
    for fg in ("--ink", "--ink-2", "--act", "--ok", "--bad", "--warn")
    for bg in _GROUNDS
]
#: Text on a tinted panel: a notice, an explanation, the instance band.
_TEXT_ON_TINT = [
    ("--ok", "--ok-bg"),
    ("--bad", "--bad-bg"),
    ("--warn", "--warn-bg"),
    ("--ink", "--ok-bg"),
    ("--ink", "--bad-bg"),
    ("--ink", "--warn-bg"),
    ("--ink-2", "--ok-bg"),
    ("--ink-2", "--bad-bg"),
    ("--ink-2", "--warn-bg"),
    ("--act", "--bad-bg"),
    ("--act", "--warn-bg"),
]
#: The filled button: its label on its fill.
_TEXT_ON_FILL = [("--act-ink", "--act")]
#: The edge of a control, a rule that carries meaning, and the focus ring must
#: each be told from the ground at 3:1 (WCAG 1.4.11).
_BOUNDARIES = [
    (fg, bg)
    for fg in ("--edge", "--act", "--bad", "--focus")
    for bg in _GROUNDS
]


def _resolved(scheme: str) -> dict[str, str]:
    css = stylesheet()
    tokens = light_tokens(css)
    if scheme == "dark":
        tokens = {**tokens, **dark_tokens(css)}
    return tokens


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), _TEXT_ON_GROUND + _TEXT_ON_TINT + _TEXT_ON_FILL)
def test_Text_OnItsGround_MeetsAaContrastInBothSchemes(scheme: str, fg: str, bg: str) -> None:
    tokens = _resolved(scheme)
    assert fg in tokens and bg in tokens, f"{fg} or {bg} is not a token"

    ratio = contrast(tokens[fg], tokens[bg])

    assert ratio >= 4.5, f"{fg} {tokens[fg]} on {bg} {tokens[bg]} is {ratio:.2f}:1 in {scheme}"


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg"), _BOUNDARIES)
def test_ControlEdgesAndFocusRing_AgainstTheGround_MeetThreeToOneInBothSchemes(
    scheme: str, fg: str, bg: str
) -> None:
    tokens = _resolved(scheme)
    assert fg in tokens and bg in tokens, f"{fg} or {bg} is not a token"

    ratio = contrast(tokens[fg], tokens[bg])

    assert ratio >= 3.0, f"{fg} {tokens[fg]} on {bg} {tokens[bg]} is {ratio:.2f}:1 in {scheme}"


def test_ContrastFunction_OnKnownPairs_AgreesWithPublishedValues() -> None:
    """Black on white is 21:1 and a mid grey on white is about 4.54:1 by definition."""
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)
    assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_Tokens_EveryColourHasALightAndADarkValue() -> None:
    css = stylesheet()
    light, dark = light_tokens(css), dark_tokens(css)
    colours = {name for name, value in light.items() if value.startswith("#")}

    assert colours, "no colour tokens were found"
    assert colours <= dark.keys(), f"no dark value for {sorted(colours - dark.keys())}"
    assert {n for n, v in dark.items() if v.startswith("#")} <= colours


def test_Stylesheet_DeclaresColourSchemeAndTheThreeTypeRoles() -> None:
    css = stylesheet()
    tokens = light_tokens(css)

    assert "color-scheme: light dark" in css
    for role in ("--serif", "--sans", "--mono"):
        assert role in tokens, f"missing type role {role}"
    assert "--tap" in tokens and "--focus" in tokens and "--radius" in tokens


def test_Stylesheet_ColourLiteralsOutsideTheTokenBlocks_AreNone() -> None:
    rules = re.sub(r"/\*.*?\*/", "", without_token_blocks(stylesheet()), flags=re.S)

    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", rules)
    assert not re.findall(r"\b(?:rgb|rgba|hsl|hsla)\(", rules)
    named = r"\b(?:white|black|red|green|blue|grey|gray|orange|yellow|navy|teal)\b"
    assert not re.findall(rf":[^;{{}}]*{named}", rules)


def test_Stylesheet_MutedTextIsAColourAndNeverOpacity() -> None:
    """Opacity composes when nested: a muted line inside a muted block fell to 3.04:1."""
    assert "opacity" not in stylesheet()


def test_Stylesheet_NeverRemovesTheFocusOutlineWithoutReplacingIt() -> None:
    css = stylesheet()

    assert not re.search(r"outline\s*:\s*(?:none|0)\b", css)
    assert ":focus-visible" in css


#: Every pattern the suite uses to prove that a masked page shows no money, taken
#: from the tests that use them. The stylesheet is part of every page, so a
#: figure in it fails those tests on pages that must show none; this runs them
#: over the stylesheet alone so whoever edits it is told here, with the line.
MONEY_PATTERNS = {
    "figure with a currency sign or two decimals (agreements, periods)": re.compile(
        r"[£€$]\s*[-\d]|\d[\d,]*\.\d\d(?![\d%a-z])"
    ),
    "pound figure (position page)": re.compile(r"£[\d9][\d9,]*\.\d\d"),
    "two decimals at a word boundary (empty Actual)": re.compile(r"\d+\.\d\d\b"),
    "the pound sign": re.compile("£"),
    "the word amount": re.compile("amount", re.I),
    "the phrase a live page must not carry (instance identity)": re.compile(
        "not production", re.I
    ),
}


@pytest.mark.parametrize("name", sorted(MONEY_PATTERNS))
def test_Stylesheet_AgainstEveryMaskedPagePattern_ContainsNoMoneyLikeText(name: str) -> None:
    found = MONEY_PATTERNS[name].search(stylesheet())

    assert found is None, f"{name}: the stylesheet contains {found.group(0)!r}"
