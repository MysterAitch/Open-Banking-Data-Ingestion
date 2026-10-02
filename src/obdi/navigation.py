"""The navigation strip every page carries, and which part of it is current.

The destinations are a fixed, small set that says how the site is organised:
what is up to date, what is held, how it arrives, where it goes, how it is
checked, and the controls that can hurt. Every one is a page of its own except
Accounts, which is the second section of the Overview.

NAVIGATION LINKS ARE NOT BUTTONS. A button is the action a page exists for, and
the page's own buttons are full width so that doing and leaving are told apart
at a glance. A strip of seven full-width buttons would bury every page's action
under its own furniture, so these are a separate element: a wrapping row of
links each at least 44 pixels tall (see `render_page`'s stylesheet), which is
the thumb-reachable size without the weight.
"""

from __future__ import annotations

import html
from contextvars import ContextVar

#: (section, label, destination). The section is what `SECTION_OF_ROUTE` names.
DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("overview", "Overview", "/"),
    ("accounts", "Accounts", "/#accounts"),
    ("connections", "Connections", "/connections"),
    ("actual", "Actual", "/actual"),
    ("reports", "Reports", "/reports"),
    ("evidence", "Evidence", "/evidence"),
    ("admin", "Admin", "/admin"),
)

#: The section a route belongs to, where the route is a page of its own.
#: A route absent from here marks nothing as current, which is correct for a
#: page such as an import result that belongs to no section.
SECTION_OF_ROUTE: dict[str, str] = {
    "/": "overview",
    "/account": "accounts",
    "/accounts": "accounts",
    "/ledger": "accounts",
    "/declare-account": "accounts",
    "/edit-account": "accounts",
    "/coverage": "accounts",
    "/import": "accounts",
    "/review": "accounts",
    "/connect": "connections",
    "/connections": "connections",
    "/actual": "actual",
    "/actual-history": "actual",
    "/admin": "admin",
    "/reports": "reports",
    "/agreements": "reports",
    "/identity-health": "reports",
    "/balance-reconciliation": "reports",
    "/balance-walk": "reports",
    "/date-lag": "reports",
    "/review-report": "reports",
    "/evidence": "evidence",
    "/artefacts": "evidence",
    "/artefact": "evidence",
    "/attempts": "evidence",
    "/fetch-timeline": "evidence",
    "/statement-shape": "evidence",
    "/spaces": "evidence",
}

#: Set by the request handler for the duration of one GET, read by the layout.
#: A context variable rather than an argument because the layout is reached
#: from some forty call sites, and the route is the one fact all of them share.
current_route: ContextVar[str] = ContextVar("current_route", default="")


def navigation_html(route: str | None = None) -> str:
    """The strip, with the section of `route` (default: the request's) marked."""
    section = SECTION_OF_ROUTE.get(current_route.get() if route is None else route, "")
    items = []
    for key, label, href in DESTINATIONS:
        current = ' aria-current="page"' if key == section else ""
        items.append(f'<li><a href="{html.escape(href)}"{current}>{html.escape(label)}</a></li>')
    return f'<nav class="sitenav" aria-label="Sections"><ul>{"".join(items)}</ul></nav>'
