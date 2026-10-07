"""Every data table a page carries is named, headed, and kept inside the page's width.

The layout adds this once as a page is assembled, so the cases here are the ways
a table can be written: a label beside its value, a header row over data, a
header-only row, a table that already has a caption or its own scrolling box,
and a heading containing markup or text that looks like markup.
"""

from __future__ import annotations

import re

from obdi.pages.callback import render_page
from obdi.pages.page_structure import structure_tables


def _structured(body: str) -> str:
    return structure_tables(body, fallback_name="Fallback title")


def test_Table_WithALabelBesideItsValue_HeadsEachRowAndNotEachColumn() -> None:
    out = _structured("<h2>Month</h2><table><tr><th>Rows</th><td>8</td></tr></table>")

    assert '<th scope="row">Rows</th><td>8</td>' in out


def test_Table_WithAHeaderRowOverData_HeadsEachColumn() -> None:
    out = _structured(
        "<h2>Rebuilds</h2><table><tr><th>When</th><th>Took</th></tr><tr><td>a</td><td>b</td></tr></table>"
    )

    assert out.count('scope="col"') == 2
    assert 'scope="row"' not in out


def test_Header_WhichAlreadyDeclaresAScope_IsLeftAlone() -> None:
    out = _structured('<table><tr><th scope="colgroup">A</th><td>x</td></tr></table>')

    assert out.count("scope=") == 1


def test_Table_Captioned_IsNamedByTheNearestHeadingAndNotTheFirst() -> None:
    out = _structured(
        "<h2>First</h2><p>x</p><h3>Second <em>one</em></h3><table><tr><td>1</td></tr></table>"
    )

    assert '<caption class="visually-hidden">Second one</caption>' in out


def test_Table_InsideADisclosure_IsNamedByTheSummaryNearerThanTheHeading() -> None:
    out = _structured(
        "<h2>Page</h2><details><summary>Cleared by month</summary>"
        "<table><tr><td>1</td></tr></table></details>"
    )

    assert "<caption class=\"visually-hidden\">Cleared by month</caption>" in out


def test_Table_WithNothingAboveIt_IsNamedByThePageTitle() -> None:
    out = _structured("<p>No heading here.</p><table><tr><td>1</td></tr></table>")

    assert "<caption class=\"visually-hidden\">Fallback title</caption>" in out


def test_Table_WithItsOwnCaption_KeepsItAndGainsNoSecond() -> None:
    out = _structured("<table><caption>Mine</caption><tr><td>1</td></tr></table>")

    assert out.count("<caption") == 1 and "Mine" in out


def test_Table_NotInAScrollingBox_IsWrappedInAFocusableNamedRegion() -> None:
    out = _structured("<h2>Walk</h2><table><tr><td>1</td></tr></table>")

    assert out.startswith(
        '<h2>Walk</h2><div class="scroll" role="region" tabindex="0" data-table-scroll'
    )
    assert 'aria-label="Walk, scrolls sideways"' in out
    assert out.endswith("</table></div>")


def test_Table_AlreadyInAScrollingBox_IsNotWrappedAgain() -> None:
    for opening in ('<div style="overflow-x:auto">', '<div class="scroll">'):
        out = _structured(f"<h2>A</h2>{opening}<table><tr><td>1</td></tr></table></div>")

        assert out.count('class="scroll"') == out.count(opening) - opening.count("overflow")
        assert 'role="region"' not in out


def test_Table_UnderAHeadingThatLooksLikeMarkup_NeverLetsItIntoTheAttributeOrCaption() -> None:
    page = render_page(
        "T",
        "<h2>&lt;script&gt;x&lt;/script&gt; &quot;q&quot;</h2><table><tr><td>1</td></tr></table>",
    ).decode()

    assert "<script>" not in page
    assert 'aria-label="&lt;script&gt;x&lt;/script&gt; &quot;q&quot;, scrolls sideways"' in page


def test_Page_WithNoTable_IsReturnedUnchanged() -> None:
    body = "<h2>A</h2><p>plain <a href='/x'>link</a></p>"

    assert _structured(body) == body


def test_Page_EveryRenderedTable_HasACaptionAndNoBareHeader() -> None:
    page = render_page(
        "Ledger",
        "<h2>Rows</h2><table><tr><th>A</th><th>B</th></tr><tr><th>C</th><td>1</td></tr></table>"
        "<h2>More</h2><table><tr><th>D</th></tr></table>",
    ).decode()

    assert len(re.findall(r"<table>", page)) == len(re.findall(r"<caption", page)) == 2
    assert "<th>" not in page
