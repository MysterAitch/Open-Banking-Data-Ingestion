"""Showing values for a sitting, rather than pressing "Show values" on every page.

THE DEFAULT IS UNCHANGED: a request with no valid `obdi-values` cookie gets the masked rendering
of every page, exactly as before. The cookie is a deliberate act (a POST to `/values-shown`, never
a link), and while it is valid a page that has an unmasked rendering is answered with that
rendering, marked `no-store`, by the very code the one-page POST uses.

THE EXPIRY IS NOT THE BROWSER'S. The cookie is a session cookie, but its value carries the moment
it was issued and is signed, and a request whose cookie is older than `SITTING_HOURS`, dated in the
future, or not signed by this process is treated as absent. A browser that keeps a session open for
a week, a cookie edited by hand, and a cookie issued by an earlier run of the server all come back
masked; the last is deliberate, since a restart ends every sitting rather than carrying one over.

The pieces other modules read are the context variables: `sitting` (until when values are shown,
or None) and `address` (where a GET was made, for the controls that return to it).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from urllib.parse import urlparse

from .page_times import clock_text

COOKIE = "obdi-values"
SITTING_HOURS = 12
SHOW = "/values-shown"
HIDE = "/values-hidden"
RETURN_FIELD = "return_to"

#: Signs the issue time so an edited cookie is refused. Held in memory on purpose: see the module
#: note on restarts.
_KEY = secrets.token_bytes(32)

#: Until when values are shown for the request being served, or None while they are hidden.
sitting: ContextVar[datetime | None] = ContextVar("values_sitting", default=None)
#: The path and query a GET was made at, so a control can bring the person back to that page.
address: ContextVar[str] = ContextVar("values_address", default="")


def _signature(issued: int) -> str:
    return hmac.new(_KEY, f"shown.{issued}".encode(), hashlib.sha256).hexdigest()[:32]


def issue(now: datetime) -> str:
    """The cookie's value for a sitting that begins at `now`."""
    issued = int(now.timestamp())
    return f"shown.{issued}.{_signature(issued)}"


def sitting_end(cookie_header: str | None, now: datetime) -> datetime | None:
    """When the sitting the request's cookie opened ends, or None where it holds none.

    None for an absent cookie, a malformed or unsigned one, one dated in the future, and one
    older than `SITTING_HOURS`: each is "hidden", never an error, so nothing a browser sends can
    make a page fail or show more.
    """
    if not cookie_header:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(cookie_header)
    except CookieError:
        return None
    morsel = jar.get(COOKIE)
    if morsel is None:
        return None
    parts = morsel.value.split(".")
    if len(parts) != 3 or parts[0] != "shown" or not parts[1].isdigit():
        return None
    issued = int(parts[1])
    if not hmac.compare_digest(parts[2], _signature(issued)):
        return None
    try:
        began = datetime.fromtimestamp(issued, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if began > now:
        return None
    end = began + timedelta(hours=SITTING_HOURS)
    return end if now < end else None


def _attributes(*, secure: bool) -> str:
    return "Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


def shown_cookie(now: datetime, *, secure: bool) -> str:
    """The `Set-Cookie` value that begins a sitting: a session cookie, with no expiry of its own."""
    return f"{COOKIE}={issue(now)}; {_attributes(secure=secure)}"


def hidden_cookie(*, secure: bool) -> str:
    """The `Set-Cookie` value that ends a sitting."""
    return f"{COOKIE}=; Max-Age=0; {_attributes(secure=secure)}"


def is_secure(forwarded_proto: str | None, host: str | None) -> bool:
    """Whether the cookie is marked Secure for a request with these headers.

    The server sits behind a proxy that ends TLS, so the scheme is what the proxy forwards. Where
    it says nothing, the cookie is Secure for every host but this machine's own, because a
    browser given a Secure cookie over plain http to a non-local host drops it, which leaves the
    page masked: the safe way to be wrong.
    """
    proto = (forwarded_proto or "").split(",")[0].strip().casefold()
    if proto:
        return proto == "https"
    name = (host or "").strip().casefold()
    name = name.split("]")[0].lstrip("[") if name.startswith("[") else name.rsplit(":", 1)[0]
    return name not in {"127.0.0.1", "localhost", "::1"}


def local_address(candidate: str | None) -> str:
    """`candidate` where it is a path on this site, otherwise the home page.

    A return address arrives in a form field, so it is whatever a forged page chose to send. Only
    a single-slash path is taken: no scheme, no host, no protocol-relative `//`, no backslash a
    browser would read as a slash, and no control character or space.
    """
    where = (candidate or "").strip()
    if (
        not where.startswith("/")
        or where.startswith("//")
        or len(where) > 2000
        or any(ch == "\\" or ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in where)
    ):
        return "/"
    parsed = urlparse(where)
    if parsed.scheme or parsed.netloc:
        return "/"
    if parsed.path.rstrip("/") in {SHOW, HIDE}:
        return "/"
    return where


def path_only(request_path: str) -> str:
    """The address the controls return to unless the page states its own: the request's path
    with no query. Carried verbatim, a query field nobody reads came straight back in the page's
    own markup, which every page's "never echoes an unknown field" test exists to refuse; and a
    page whose window travels by POST holds nothing in its address at all. A page that does
    address its view (the ledger's month or window) says so with `page_address`."""
    return local_address(urlparse(request_path).path)


def page_address(canonical: str) -> str:
    """Record the page's own canonical address for this request's controls, and return it. The
    page builds it from what it read, never from the request line, so nothing unread is echoed."""
    address.set(local_address(canonical))
    return address.get()


def shown() -> bool:
    """Is the request being served inside a sitting that shows values?"""
    return sitting.get() is not None


def unless_sitting(notice_html: str) -> str:
    """A page's own "values are shown here" notice, left out where the sitting's banner says it."""
    return "" if shown() else notice_html


def _return_field() -> str:
    return f'<input type="hidden" name="{RETURN_FIELD}" value="{html.escape(address.get())}">'


def show_everywhere_press() -> str:
    """The quiet control beside a page's own "Show values", or nothing while values are shown."""
    if shown():
        return ""
    return (
        f'<form class="sitting-press" method="post" action="{SHOW}">{_return_field()}'
        '<button class="tap" type="submit">Show values on every page</button></form>'
    )


def banner_html() -> str:
    """The line every page carries while values are shown, with the control that ends it."""
    until = sitting.get()
    if until is None:
        return ""
    return (
        '<div class="sitting" role="status">'
        "<span>Values are shown on every page for this sitting "
        f"(until {clock_text(until)}).</span>"
        f'<form method="post" action="{HIDE}">{_return_field()}'
        '<button class="tap" type="submit">Hide values</button></form></div>'
    )


def more_line_html() -> str:
    """What More says of the sitting: whether values are shown, and the opposite control."""
    until = sitting.get()
    if until is None:
        return (
            '<h2>Values</h2><p>Values are hidden on every page unless one is pressed for.</p>'
            f'<form method="post" action="{SHOW}">{_return_field()}'
            '<button class="tap" type="submit">Show values on every page</button></form>'
        )
    return (
        f"<h2>Values</h2><p>Values are shown this sitting, until {clock_text(until)}.</p>"
        f'<form method="post" action="{HIDE}">{_return_field()}'
        '<button class="tap" type="submit">Hide values</button></form>'
    )
