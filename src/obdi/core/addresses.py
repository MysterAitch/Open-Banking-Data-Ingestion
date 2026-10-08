"""The address of a page for one account.

Pure string building, used by the pages and by the to-do read model that links to them; it sits
in `core` so a read model can name a page's address without importing the pages.
"""

from __future__ import annotations

from urllib.parse import quote

#: The address of each page that has a form for one account, by the name callers use. A page of a
#: thing links to the list it belongs to AT that thing, or to that thing's own page, never to the
#: list's top: "the address of X for account R" is known here and nowhere else, so a new
#: account-scoped form of a page is one entry and a test (`test_deep_links`) holds every link to
#: it. An entry is a template; the reference is encoded where it is placed.
_ACCOUNT_ADDRESS = {
    "ledger": "/ledger?ref={ref}",
    "account": "/account?ref={ref}",
    "edit": "/edit-account?ref={ref}",
    "list": "/accounts#account-{ref}",
    "statements": "/statements?ref={ref}",
    "timeline": "/coverage-timeline?ref={ref}",
    "periods": "/period-reconciliation?ref={ref}",
    "balance-chart": "/balance-chart?ref={ref}",
    "bring-in": "/bring-in#account-{ref}",
    "bring-in-upload": "/bring-in?account={ref}",
}


def account_address(page: str, ref: str) -> str:
    """The address of `page` for the account `ref`: its form of that page, or its row in it."""
    return _ACCOUNT_ADDRESS[page].format(ref=quote(ref, safe=""))
