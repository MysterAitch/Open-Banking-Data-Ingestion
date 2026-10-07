"""A page carries the stylesheet's rules and none of its comments.

The stylesheet is written with a comment beside every rule that needs one. Until 2026-10-05 the
comments were sent too: a fifth of every page, and a hazard, because a word or a number in a
comment is text in every page. Three sentences explaining a layout fix pushed a chart page 574
bytes past its size bound and the build refused the release.

KNOWN ANSWERS, decided before the first run: the served text holds no comment marker; with the
written text's comments removed and both texts' white space collapsed the two are the same, so
no rule is lost or altered; and a page as served holds no comment marker inside its `<style>`.
"""

from __future__ import annotations

import re

from obdi.pages.callback import render_page
from obdi.pages.stylesheet import SERVED_STYLESHEET, STYLESHEET, _as_served


def _collapsed(css: str) -> str:
    return re.sub(r"\s+", " ", css).strip()


def test_ServedStylesheet_HoldsNoComment() -> None:
    assert "/*" not in SERVED_STYLESHEET
    assert "*/" not in SERVED_STYLESHEET


def test_ServedStylesheet_AgainstTheWrittenOneWithoutItsComments_HasTheSameRules() -> None:
    written = re.sub(r"/\*.*?\*/", " ", STYLESHEET, flags=re.DOTALL)

    assert _collapsed(SERVED_STYLESHEET) == _collapsed(written)


def test_ServedStylesheet_IsSmallerThanTheWrittenOneByItsComments() -> None:
    assert len(SERVED_STYLESHEET) < len(STYLESHEET) * 0.85


def test_AsServed_WhenACommentSpansLinesAndSitsBetweenRules_LeavesBothRules() -> None:
    written = (
        " .a { margin: 0; }\n /* why the next rule\n    exists, at length */\n"
        " .b { padding: 0; }\n"
    )

    assert _as_served(written) == ".a { margin: 0; }\n.b { padding: 0; }\n"


def test_Page_AsServed_CarriesTheRulesAndNoComment() -> None:
    page = render_page("A page", "<p>Body</p>").decode()
    style = page[page.index("<style>") : page.index("</style>")]

    assert "/*" not in style
    assert ".pill" in style and "--ok" in style


def test_WrittenStylesheet_PutsNoCommentMarkerInsideAString() -> None:
    """The stripping is a pattern over text; a marker inside a quoted value would break it."""
    assert not re.findall(r"\"[^\"\n]*/\*[^\"\n]*\"", STYLESHEET)
    assert not re.findall(r"'[^'\n]*/\*[^'\n]*'", STYLESHEET)
