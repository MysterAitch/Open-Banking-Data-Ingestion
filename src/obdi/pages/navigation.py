"""The navigation strip every page carries, which part of it is current, and what pages are called.

The destinations are a fixed, small set that says how the site is organised, in the order
they are used: what needs doing today, how data is brought in from files, the position the
accounts add up to, the places data is fetched from and sent to, and everything else. Every one
is a page of its own.

EVERY ROUTE HAS A HOME. `SECTION_OF_ROUTE` names the destination of every route the
dispatcher serves, GET or POST, so the current section is marked on an answer page as well
as on the page a person navigated to. A test reads the dispatcher and fails for a route this
table does not know, and another walks the links from the destinations and fails for a page
nothing links to.

THE NAME OF A PAGE IS DECLARED ONCE, in `PAGE_NAMES`. The page's heading, the strip's link, the
index rows, and the "formerly" note under a renamed page's heading all read it, so a rename is
one edit and the old name is said once, on the page it led to, where somebody who learnt it
will look.

NAVIGATION LINKS ARE NOT BUTTONS. A button is the action a page exists for, and the page's own
buttons are full width so that doing and leaving are told apart at a glance. A strip of five
full-width buttons would bury every page's action under its own furniture, so these are a
separate element: a wrapping row of links each at least 44 pixels tall (see `render_page`'s
stylesheet), which is the thumb-reachable size without the weight.
"""

from __future__ import annotations

import html
from contextvars import ContextVar
from dataclasses import dataclass

#: (section, label, destination). The section is what `SECTION_OF_ROUTE` names.
#: "Today" rather than "Overview": the page opens with whether anything needs a person now, and
#: a word that says when it is read says more than one that says only that it is broad.
#: Five items sit in one row on a phone; the browser test in test_phone_layout.py holds it.
#: "Connections" is every external place data moves to or from: the banks and the aggregator it is
#: fetched from, and the budgeting tool it is sent to. "More" lists everything else: the accounts
#: page, the checks, and the diagnostics, which are read when something is wrong and are not a
#: daily destination. An account's own page marks Today as current, because Today is the list it
#: is opened from.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("today", "Today", "/"),
    ("bring-in", "Bring in", "/bring-in"),
    ("position", "Position", "/position"),
    ("connections", "Connections", "/connections"),
    ("more", "More", "/more"),
)

#: The pages More lists that are the hub of a group of pages of their own. Like a destination's
#: own page, a hub offers no way out and says its old name itself: More is one tap away in the
#: strip, and a second "Back to More" beside the page's own note would say the name twice.
HUBS = frozenset({"/checks", "/diagnostics"})

#: Addresses that answer with a destination's own page, kept so that bookmarks and the links
#: other pages hold go on working. Each is the landing page of the section it maps to.
ALIASES: dict[str, str] = {
    "/reports": "/checks",
    "/evidence": "/diagnostics",
    "/admin": "/diagnostics",
}

#: The section a route belongs to. A route absent from here marks nothing as current, which is
#: right only for an address no page serves.
SECTION_OF_ROUTE: dict[str, str] = {
    # Today, and the pages opened from its list of accounts: one account, declaring and editing,
    # Spaces, and what is done to an account with its transactions in view.
    "/": "today",
    "/accounts": "today",
    "/ledger": "today",
    "/balance-chart": "today",
    "/declare-account": "today",
    "/edit-account": "today",
    "/spaces": "today",
    "/ledger-anchor": "today",
    "/ledger-anchor-remove": "today",
    "/ledger-balance-disregard": "today",
    "/ledger-balance-use-again": "today",
    "/ledger-window-default": "today",
    "/ledger-typed": "today",
    "/ledger-typed-withdraw": "today",
    "/protect": "today",
    "/protect-withdraw": "today",
    "/protect-accept": "today",
    "/declare-spaces": "today",
    "/declare-known": "today",
    "/set-parents": "today",
    "/save-account": "today",
    "/archive-account": "today",
    "/unarchive-account": "today",
    "/bind": "today",
    "/review-apply": "more",
    "/review-defer": "more",
    # Position
    "/position": "position",
    # Connections: the places data is fetched from (banks, the aggregator) and the place it is
    # sent to (the budgeting tool, Actual).
    "/connections": "connections",
    "/connect": "connections",
    "/callback": "connections",
    "/rename-connection": "connections",
    "/fetch-now": "connections",
    "/extend": "connections",
    "/extend-max": "connections",
    "/actual": "connections",
    "/actual-history": "connections",
    "/push-actual": "connections",
    "/audit-actual": "connections",
    "/marker-actual": "connections",
    "/prune-actual": "connections",
    "/align-actual": "connections",
    "/empty-actual": "connections",
    # Bring in: every way data enters from a file.
    "/bring-in": "bring-in",
    "/gaps": "bring-in",
    "/gaps-mark": "bring-in",
    "/gaps-mark-undo": "bring-in",
    "/gaps-scope": "bring-in",
    "/coverage-timeline": "bring-in",
    "/import": "bring-in",
    "/upload": "bring-in",
    "/upload-preview": "bring-in",
    "/upload-confirm": "bring-in",
    "/upload-result": "bring-in",
    "/statements": "bring-in",
    "/statement-shape": "bring-in",
    "/statement-held": "bring-in",
    "/statement-assign": "bring-in",
    "/statements-assign": "bring-in",
    "/statement-dry-run": "bring-in",
    "/statement-section-assign": "bring-in",
    "/statement-section-move": "bring-in",
    # More: everything else, in the plain groups its page lists them in. The accounts' own
    # housekeeping, the checks of the data's health, and the pages that exist to explain a fault
    # and the repairs.
    "/more": "more",
    "/values-shown": "more",
    "/values-hidden": "more",
    "/coverage": "more",
    "/review": "more",
    "/checks": "more",
    "/reports": "more",
    "/review-flags": "more",
    "/recurring": "more",
    "/entities": "more",
    "/entities-merge": "more",
    "/entities-split": "more",
    "/entities-rename": "more",
    "/entities-fold": "more",
    "/entities-child": "more",
    "/entities-new": "more",
    "/entity": "more",
    "/entity-rule": "more",
    "/entity-rule-try": "more",
    "/entity-rule-remove": "more",
    "/entity-rename": "more",
    "/entity-split": "more",
    "/entity-parent": "more",
    "/entity-link-keep": "more",
    "/entity-link-refuse": "more",
    "/review-flags-two": "more",
    "/review-flags-one": "more",
    "/review-flags-undo": "more",
    "/agreements": "more",
    "/identity-health": "more",
    "/balance-reconciliation": "more",
    "/period-reconciliation": "more",
    "/balance-walk": "more",
    "/date-lag": "more",
    "/review-report": "more",
    "/diagnostics": "more",
    "/evidence": "more",
    "/admin": "more",
    "/account": "more",
    "/artefacts": "more",
    "/artefact": "more",
    "/attempts": "more",
    "/fetch-timeline": "more",
    "/rebuild-derived": "more",
    "/forget-actual-bindings": "more",
    "/replay-artefact": "more",
    "/refile-artefact": "more",
    "/starling-probe": "more",
}


@dataclass(frozen=True)
class PageName:
    """What a page is called now, and what it was called before, if it was renamed."""

    name: str
    formerly: str = ""


#: Pages whose name is not simply what their code first called them. A check is named by the
#: question it answers; the account's field statistics were "Shape". A page absent from here
#: keeps the title its handler gives it.
PAGE_NAMES: dict[str, PageName] = {
    # "match", not "agree": rows are in agreement with known balances, and two sources match
    # each other (`page_words`).
    "/agreements": PageName("Do my sources match?", "Cross-source agreement"),
    "/recurring": PageName("Recurring payments"),
    "/entities": PageName("Entities"),
    "/entity": PageName("Entity"),
    "/identity-health": PageName("Is any payment counted twice?", "Identity health"),
    "/balance-reconciliation": PageName("Do the days add up?", "Balance reconciliation"),
    "/period-reconciliation": PageName("Do the statements add up?", "Statement periods"),
    "/balance-walk": PageName("Is any money unexplained?", "Balance walk"),
    "/date-lag": PageName("Do payment dates drift?", "Settlement lag"),
    "/review-report": PageName("Could any payment be doubled?", "Review queue report"),
    "/account": PageName("Field statistics", "Shape"),
    "/checks": PageName("Checks", "Reports"),
    "/diagnostics": PageName("Diagnostics", "Evidence and Admin"),
    "/bring-in": PageName("Bring in"),
    "/more": PageName("More"),
    "/connections": PageName("Connections"),
    "/gaps": PageName("What to fetch next"),
    "/gaps-mark": PageName("Set a period aside"),
    "/coverage-timeline": PageName("Coverage timeline"),
}


#: Where on the Accounts page the accounts that need a look are named. Today's Verification
#: line links there when any does, so a tap from "1 held back" lands on the one held back and
#: not on the top of a list in which it looks like every other account.
NEEDS_A_LOOK = "needs-a-look"


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

#: The list each account-scoped address is the account's place in, by its top address.
LIST_TOPS = frozenset({"/accounts", "/statements", "/coverage-timeline", "/bring-in"})


def account_address(page: str, ref: str) -> str:
    """The address of `page` for the account `ref`: its form of that page, or its row in it."""
    from urllib.parse import quote

    return _ACCOUNT_ADDRESS[page].format(ref=quote(ref, safe=""))


def page_name(route: str) -> str:
    """The page's name, as its heading, its index row, and every link to it say it."""
    return PAGE_NAMES[route].name


#: Set by the request handler for the duration of one request, read by the layout.
#: A context variable rather than an argument because the layout is reached
#: from some forty call sites, and the route is the one fact all of them share.
current_route: ContextVar[str] = ContextVar("current_route", default="")


def section_of(route: str) -> str:
    """The destination a route belongs to, or "" for an address nothing serves."""
    return SECTION_OF_ROUTE.get(route, "")


def navigation_html(route: str | None = None) -> str:
    """The strip, with the section of `route` (default: the request's) marked."""
    section = section_of(current_route.get() if route is None else route)
    items = []
    for key, label, href in DESTINATIONS:
        current = ' aria-current="page"' if key == section else ""
        items.append(f'<li><a href="{html.escape(href)}"{current}>{html.escape(label)}</a></li>')
    return f'<nav class="sitenav" aria-label="Sections"><ul>{"".join(items)}</ul></nav>'


#: Set by the request handler for the duration of one POST. An answer leads with what happened
#: (or with a link to the account it happened to), so the way out follows that first paragraph
#: instead of pushing it down the screen.
answering: ContextVar[bool] = ContextVar("answering", default=False)


def way_out_html(route: str | None = None) -> str:
    """The link back to the page's destination, and the old name of a renamed page.

    Empty on the Overview, on a destination's own page, and on a route nothing serves.
    """
    where = current_route.get() if route is None else route
    section = section_of(where)
    landing = {href for key, _, href in DESTINATIONS if key == section}
    landing |= HUBS if section == "more" else set()
    landing |= {alias for alias, target in ALIASES.items() if target in landing}
    if not section or section == "today" or where in landing:
        return ""
    label, href = next((label, href) for key, label, href in DESTINATIONS if key == section)
    parts = [f'<p class="wayout"><a class="tap" href="{html.escape(href)}">Back to {label}</a></p>']
    renamed = PAGE_NAMES.get(where)
    if renamed is not None and renamed.formerly:
        former = html.escape(renamed.formerly)
        parts.append(f'<p class="muted formerly">Formerly called {former}.</p>')
    return "".join(parts)


def with_way_out(body: str, route: str | None = None) -> str:
    """`body` with the way out under the heading; in an answer, after its first paragraph."""
    way_out = way_out_html(route)
    if not way_out:
        return body
    if answering.get() and body.startswith("<p") and "</p>" in body:
        end = body.index("</p>") + len("</p>")
        return body[:end] + way_out + body[end:]
    return way_out + body
