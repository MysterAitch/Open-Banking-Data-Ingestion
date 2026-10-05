"""The pages' own words need no explaining: a newcomer reading a sentence is told what is compared
with what, and nothing is amber that does not need attention.

Faults the owner met are held here as scenarios over invented data, each decided before the first
run:

  1  An account whose one differing known balance is a statement's closing that the page itself
     says "closed before" a transaction dated that day: the folded section's heading counts that
     balance under its own words and not as "differ", and its verb agrees with its count.
  2  Today's known-balances line when no account fails and some have no known balance: the chip is
     the quiet tone and says how many add up; it is amber only where an account does not add up.
  3  The retired phrases of `page_words.RETIRED_ON_PAGES` are absent from every page of the
     invented store, Today included (the walk used to skip it).
"""

from __future__ import annotations

import re

import httpx

from obdi.page_words import RETIRED_ON_PAGES
from obdi.plural import agree
from page_dom import Node, elements, parse
from page_walk import household, household_served, invented, served, walked_pages  # noqa: F401
from test_home_page import clear, page_of, troubled  # noqa: F401
from test_page_wording_vocabulary import text_of
from test_statement_listing_rule_pages import served as rule_served  # noqa: F401


def heading_of(page: str) -> str:
    for node in elements(parse(page), "summary"):
        if node.text().startswith("Known balances and the opening"):
            return node.text()
    raise AssertionError("the page has no known balances section")


def ledger(base: str, ref: str, month: str) -> str:
    return httpx.get(f"{base}/ledger", params={"ref": ref, "month": month}, timeout=60).text


class TestTheKnownBalancesHeading:
    def test_Heading_WhenAStatementIsTakenToHaveClosedBeforeATransaction_DoesNotCountItAsDiffering(
        self, rule_served  # noqa: F811
    ):
        heading = heading_of(ledger(rule_served[0], "sd-explained", "2026-02"))

        assert "differ" not in heading, heading
        assert "closed before a transaction dated that day" in heading, heading

    def test_Heading_WhenNothingExplainsTheDifference_StillCountsItAsDiffering(
        self, rule_served  # noqa: F811
    ):
        heading = heading_of(ledger(rule_served[0], "sd-unexplained", "2026-02"))

        assert "closed before" not in heading, heading
        assert re.search(r"\b1 differs\b", heading), heading

    def test_Verb_AgreesWithItsCount(self):
        assert [agree(n, "differs") for n in (1, 2)] == ["differs", "differ"]


def todays_known_balances_line(page: str) -> tuple[Node, Node]:
    """The status line about known balances and its chip, found by structure."""
    status = next(e for e in elements(parse(page), "ul") if e.attrs.get("id") == "status")
    for item in elements(status, "a"):
        label = next(e for e in elements(item, "span") if "status-label" in e.classes)
        if label.text() == "Known balances":
            chip = next(e for e in elements(item, "span") if "pill" in e.classes)
            return item, chip
    raise AssertionError("Today has no known-balances line")


class TestTodaysKnownBalancesChip:
    def test_Chip_WhenSomeAccountDoesNotAddUp_IsAmberAndSaysSo(self, troubled):  # noqa: F811
        line, chip = todays_known_balances_line(page_of(troubled))

        assert chip.text() == "does not add up"
        assert "pill-warn" in chip.classes
        assert line.attrs["href"] == "/accounts#needs-a-look"

    def test_Chip_WhenNoAccountFailsAndSomeHaveNothingToCheckAgainst_IsQuietAndCountsWhatAddsUp(
        self, clear  # noqa: F811
    ):
        line, chip = todays_known_balances_line(page_of(clear))

        assert chip.text() == "14 add up"
        assert "pill-quiet" in chip.classes
        assert "pill-warn" not in chip.classes
        assert "2 have nothing to check against" in line.text()
        assert line.attrs["href"] == "/accounts#needs-a-look"


class TestNoPageUsesARetiredPhrase:
    def test_Today_IsAmongTheWalkedPages(self, walked_pages):  # noqa: F811
        assert "/" in walked_pages

    def test_Today_OverTheInventedStore_ShowsNoRetiredPhrase(self, walked_pages):  # noqa: F811
        shown = text_of(walked_pages["/"]).casefold()

        assert [phrase for phrase in RETIRED_ON_PAGES if phrase in shown] == []
