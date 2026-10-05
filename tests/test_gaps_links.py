"""Where the What to fetch next page is reached from, and that each place says the same thing.

The Bring in hub leads with the page's own verdict sentence, Today's overdue-statement item sends
its remedy to the page, the Accounts page's list of further pages offers it, and the coverage and
kept-statements pages say where to go for a missing file.
"""

from __future__ import annotations

import html
import re

import pytest

from fetch_gaps_world import TODAY, Loaded, load_household
from obdi.overview import standing_items_from
from obdi.web_destinations import accounts_links_html, render_bring_in
from obdi.web_gaps import FETCH_NEXT_LINE, verdict_sentence
from obdi.web_sections import render_coverage


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return load_household(tmp_path_factory.mktemp("gaps-links"))


def hub(loaded: Loaded | None, **more) -> str:
    report = None if loaded is None else (lambda: loaded.report)
    return render_bring_in(None, fetch_report=report, **more).decode()


class TestTheBringInHub:
    def test_Hub_WhenGapsAreHeld_LeadsWithTheVerdictAndLinksToThePage(self, world):
        page = hub(world)
        rows = page.split('<li class="hub-row')

        assert 'href="/gaps"' in rows[1]
        assert "What to fetch next" in rows[1]
        assert html.escape(verdict_sentence(world.report)) in rows[1]
        assert "to fetch" in rows[1]

    def test_Hub_WhenNothingIsDue_SaysSoWithAGreenChip(self, world):
        from obdi.fetch_gaps import FetchReport

        calm = FetchReport(tuple(o for o in world.report.accounts if not o.gaps), TODAY)
        page = render_bring_in(None, fetch_report=lambda: calm).decode()
        first = page.split('<li class="hub-row')[1]

        assert "nothing due" in first and "pill-ok" in first
        assert "hub-row-warn" not in first

    def test_Hub_WhenTheReportCannotBeRead_SaysSoAndKeepsTheRestOfThePage(self):
        def broken():
            raise RuntimeError("the store is busy")

        page = render_bring_in(None, fetch_report=broken).decode()

        assert "What is still to fetch could not be read just now." in page
        assert "Import an export file" in page

    def test_Hub_WhenNoReportIsWired_HasNoRowForIt(self):
        assert 'href="/gaps"' not in hub(None)


class TestTheOtherWaysIn:
    def test_TodaysOverdueStatementItem_SendsItsRemedyToThePage(self, world):
        items = standing_items_from(world.standings, lambda ref: ref, lambda ref: False, TODAY)
        (due,) = [item for item in items if item.kind == "statement-due"]

        assert due.href == "/gaps"

    def test_AccountsPageList_OffersThePage(self):
        offered = accounts_links_html()

        assert re.search(r'<a class="tap" href="/gaps">What to fetch next</a>', offered)

    def test_CoveragePage_SaysWhereToGoForAMissingFile(self):
        assert FETCH_NEXT_LINE in render_coverage().decode()

    def test_Line_NamesThePageAndWhatItLists(self):
        said = html.unescape(re.sub(r"<[^>]+>", "", FETCH_NEXT_LINE))

        assert said.startswith("Missing a statement or an export?")
        assert "What to fetch next" in said
