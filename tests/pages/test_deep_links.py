"""A link from a thing's page to a list points at that thing in the list, or at its own page.

Walked over the invented household: every account's page and Today are fetched, and every link
on them is read. A link to the TOP of a list (`/accounts`, `/statements`, `/coverage-timeline`,
`/bring-in`) from a page about one account is a fault wherever an account-scoped form of that page
exists (`navigation.account_address`). Today's rows are held the same way, and so are the
addresses the helper makes.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from obdi.core.addresses import account_address
from obdi.pages.navigation import LIST_TOPS
from page_dom import elements, parse
from page_walk import invented, served, walked_pages  # noqa: F401


def links(page: str, *, inside: str = "") -> list[str]:
    """The links of a page, outside the strip every page carries; with `inside`, only those within
    an element of that class."""
    found = []
    for a in elements(parse(page), "a"):
        if not a.attrs.get("href"):
            continue
        ups = list(a.ancestors())
        if any(up.tag == "nav" for up in ups):
            continue
        if inside and not any(inside in up.classes for up in ups):
            continue
        found.append(a.attrs["href"])
    return found


def bare_list_links(page: str, *, inside: str = "") -> list[str]:
    """The links on a page that go to a list's top with nothing saying which account."""
    found = []
    for href in links(page, inside=inside):
        parsed = urlparse(href)
        if parsed.path in LIST_TOPS and not parsed.query and not parsed.fragment:
            found.append(href)
    return found


class TestTheHelper:
    def test_AccountAddress_EncodesTheReferenceWhereverItIsPlaced(self) -> None:
        assert account_address("edit", "a b&c") == "/edit-account?ref=a%20b%26c"
        assert account_address("list", "a b") == "/accounts#account-a%20b"
        assert account_address("statements", "x") == "/statements?ref=x"
        assert account_address("bring-in-upload", "x") == "/bring-in?account=x"

    @pytest.mark.parametrize(
        "page", ["ledger", "edit", "list", "statements", "timeline", "periods", "bring-in"]
    )
    def test_EveryAddress_IsAccountScopedAndNeverTheTopOfAList(self, page: str) -> None:
        parsed = urlparse(account_address(page, "acct"))

        assert parsed.query or parsed.fragment
        assert "acct" in (parsed.query + parsed.fragment)


class TestTheAccountPages:
    def test_EveryAccountPage_LinksToNoListWithoutSayingWhichAccount(self, walked_pages) -> None:  # noqa: F811
        ledgers = {
            url: page for url, page in walked_pages.items() if url.startswith("/ledger?ref=")
        }
        assert ledgers, "the walk reached no account page"

        offenders = {url: bare_list_links(page) for url, page in ledgers.items()}

        assert {url: found for url, found in offenders.items() if found} == {}

    def test_TheRenameFold_LinksToTheAccountsOwnFormAndItsRowAndNotTheList(
        self,
        walked_pages,  # noqa: F811
    ) -> None:
        url, page = next(
            (u, p)
            for u, p in walked_pages.items()
            if u.startswith("/ledger?ref=") and "Rename or archive" in p
        )
        ref = parse_qs(urlparse(url).query)["ref"][0]
        hrefs = links(page)

        assert account_address("edit", ref) in hrefs
        assert account_address("list", ref) in hrefs
        assert "/accounts" not in hrefs

    def test_TheKeptStatementsLine_LeadsToTheAccountsOwnList(self, walked_pages) -> None:  # noqa: F811
        held = [
            (url, page)
            for url, page in walked_pages.items()
            if url.startswith("/ledger?ref=") and "kept for this account" in page
        ]
        assert held, "no account page said how many statements are kept for it"
        for url, page in held:
            ref = parse_qs(urlparse(url).query)["ref"][0]
            assert account_address("statements", ref) in links(page)


class TestToday:
    def test_TodaysRows_LinkToAnAccountOrItsPageAndNotToAList(self, walked_pages) -> None:  # noqa: F811
        today = walked_pages["/"]

        assert bare_list_links(today, inside="todo") == []
        assert bare_list_links(today, inside="account") == []
