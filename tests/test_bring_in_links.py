"""Where Bring in is reached from, and that each place says the same thing.

Today's overdue-statement item sends its remedy to the page, the Accounts page's list of further
pages offers it, and the coverage and kept-statements pages say where to go for a missing file.
The old address of the page, `/gaps`, is only a redirect to it.
"""

from __future__ import annotations

import html
import re

import pytest

from fetch_gaps_world import TODAY, load_household
from obdi.read.overview import standing_items_from
from obdi.web_destinations import accounts_links_html
from obdi.web_marks import FETCH_NEXT_LINE
from obdi.web_sections import render_coverage


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return load_household(tmp_path_factory.mktemp("bring-in-links"))


class TestTheOtherWaysIn:
    def test_TodaysOverdueStatementItem_SendsItsRemedyToBringIn(self, world):
        items = standing_items_from(world.standings, lambda ref: ref, lambda ref: False, TODAY)
        (due,) = [item for item in items if item.kind == "statement-due"]

        assert due.href == "/bring-in"

    def test_AccountsPageList_OffersBringIn(self):
        offered = accounts_links_html()

        assert re.search(r'<a class="tap" href="/bring-in">Bring in</a>', offered)
        assert "/gaps" not in offered

    def test_CoveragePage_SaysWhereToGoForAMissingFile(self):
        assert FETCH_NEXT_LINE in render_coverage().decode()

    def test_Line_NamesThePageAndWhatItLists(self):
        said = html.unescape(re.sub(r"<[^>]+>", "", FETCH_NEXT_LINE))

        assert said.startswith("Missing a statement or an export?")
        assert "Bring in lists them, account by account" in said
