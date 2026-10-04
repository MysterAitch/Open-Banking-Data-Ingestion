"""The navigation strip every page carries, which part of it is current, and what pages are called.

The destinations are a fixed, small set that says how the site is organised, in the order
they are used: what is up to date today, the accounts held, the position they add up to,
whether the push to Actual is right, how data comes in, whether it is healthy, and the pages
that exist to explain a fault. Every one is a page of its own.

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
buttons are full width so that doing and leaving are told apart at a glance. A strip of seven
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
#: Seven items wrap to two rows of four at 360 pixels (the stylesheet trims the links' padding to
#: allow it); the browser test in test_phone_layout.py holds it.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("today", "Today", "/"),
    ("accounts", "Accounts", "/accounts"),
    ("position", "Position", "/position"),
    ("actual", "Actual", "/actual"),
    ("bring-in", "Bring in", "/bring-in"),
    ("checks", "Checks", "/checks"),
    ("diagnostics", "Diagnostics", "/diagnostics"),
)

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
    # Today
    "/": "today",
    # Accounts: the list, one account, declaring and editing, Spaces, coverage by source,
    # and the categorising that is done account by account.
    "/accounts": "accounts",
    "/ledger": "accounts",
    "/balance-chart": "accounts",
    "/declare-account": "accounts",
    "/edit-account": "accounts",
    "/coverage": "accounts",
    "/spaces": "accounts",
    "/review": "accounts",
    "/ledger-anchor": "accounts",
    "/ledger-anchor-remove": "accounts",
    "/ledger-typed": "accounts",
    "/ledger-typed-withdraw": "accounts",
    "/protect": "accounts",
    "/protect-withdraw": "accounts",
    "/protect-accept": "accounts",
    "/declare-spaces": "accounts",
    "/declare-known": "accounts",
    "/set-parents": "accounts",
    "/save-account": "accounts",
    "/archive-account": "accounts",
    "/unarchive-account": "accounts",
    "/bind": "accounts",
    "/review-apply": "accounts",
    "/review-defer": "accounts",
    # Position
    "/position": "position",
    # Actual
    "/actual": "actual",
    "/actual-history": "actual",
    "/push-actual": "actual",
    "/audit-actual": "actual",
    "/marker-actual": "actual",
    "/prune-actual": "actual",
    "/align-actual": "actual",
    "/empty-actual": "actual",
    # Bring in: every way data enters.
    "/bring-in": "bring-in",
    "/connect": "bring-in",
    "/callback": "bring-in",
    "/connections": "bring-in",
    "/rename-connection": "bring-in",
    "/fetch-now": "bring-in",
    "/extend": "bring-in",
    "/extend-max": "bring-in",
    "/import": "bring-in",
    "/upload": "bring-in",
    "/upload-preview": "bring-in",
    "/upload-confirm": "bring-in",
    "/upload-result": "bring-in",
    "/statements": "bring-in",
    "/statement-shape": "bring-in",
    "/statement-shape-disclose": "bring-in",
    "/statement-held": "bring-in",
    "/statement-assign": "bring-in",
    "/statements-assign": "bring-in",
    "/statement-section-assign": "bring-in",
    # Checks: the health of the data.
    "/checks": "checks",
    "/reports": "checks",
    "/review-flags": "checks",
    "/review-flags-two": "checks",
    "/review-flags-one": "checks",
    "/review-flags-undo": "checks",
    "/agreements": "checks",
    "/identity-health": "checks",
    "/balance-reconciliation": "checks",
    "/period-reconciliation": "checks",
    "/balance-walk": "checks",
    "/date-lag": "checks",
    "/review-report": "checks",
    # Diagnostics: pages that exist to explain a fault, and the repairs.
    "/diagnostics": "diagnostics",
    "/evidence": "diagnostics",
    "/admin": "diagnostics",
    "/account": "diagnostics",
    "/artefacts": "diagnostics",
    "/artefact": "diagnostics",
    "/attempts": "diagnostics",
    "/fetch-timeline": "diagnostics",
    "/rebuild-derived": "diagnostics",
    "/forget-actual-bindings": "diagnostics",
    "/replay-artefact": "diagnostics",
    "/refile-artefact": "diagnostics",
    "/starling-probe": "diagnostics",
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
    "/agreements": PageName("Do my sources agree?", "Cross-source agreement"),
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
}


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


#: Where an answer page's lead is already a link the page exists for. The way out goes after it.
_ANSWER_LEAD = '<p><a class="button" href="/ledger?ref='


def way_out_html(route: str | None = None) -> str:
    """The link back to the page's destination, and the old name of a renamed page.

    Empty on the Overview, on a destination's own page, and on a route nothing serves.
    """
    where = current_route.get() if route is None else route
    section = section_of(where)
    landing = {href for key, _, href in DESTINATIONS if key == section}
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
    """`body` with the way out under the heading, after an answer's own lead link if it has one."""
    way_out = way_out_html(route)
    if not way_out:
        return body
    if body.startswith(_ANSWER_LEAD):
        end = body.index("</p>") + len("</p>")
        return body[:end] + way_out + body[end:]
    return way_out + body
