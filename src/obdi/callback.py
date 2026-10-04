"""A callback receiver, so authorisation stops involving copy and paste.

The OAuth redirect happens in the BROWSER, not server to server. The provider
never connects to this service - it sends the browser somewhere, and the
browser follows. That is the whole reason a private address works: the redirect
target only has to be reachable by the machine holding the session.

So a service bound to loopback, fronted by anything that gives it a hostname and
a certificate the browser trusts, is a valid redirect target with no public
exposure whatsoever. The
human step at the bank remains, because strong customer authentication is the
point of the rule and cannot be automated. Everything after it can be.

Webhooks are a different matter and DO require public ingress - but nothing
here needs them: aggregator transaction data is polled, and their polling runs
on a four-to-six hour cycle anyway, so a webhook would buy promptness within a
window that already exists.

Deliberately built on the standard library. A single-endpoint receiver handling
a handful of requests a quarter does not justify a web framework and two more
dependencies to keep patched.
"""

from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlparse

from .buildinfo import describe
from .navigation import navigation_html
from .page_structure import structure_tables
from .stylesheet import STYLESHEET


class CodeHandler(Protocol):
    """Exchanges an authorisation code and returns a message for the browser."""

    def __call__(self, code: str, state: str) -> str: ...


def instance_identity() -> tuple[str, str]:
    """(banner html, title prefix) identifying which instance is serving this.

    Several instances of this application run side by side - a live one, a restore
    target seeded from its backup, a disposable synthetic one - and they render
    identically. Same layout, same buttons, same destructive controls. On a phone,
    in a tab opened yesterday, the port is the only thing telling them apart, and
    nobody reads the port.

    THE LIVE INSTANCE SHOWS NOTHING, deliberately. It is the one used daily, and a
    banner on every page there would be trained away within a day - taking the
    other instance's banner with it. Absence on production is what gives presence
    its weight.

    AN UNIDENTIFIED INSTANCE SAYS SO, rather than assuming either. Claiming to be
    live invites the mistake this exists to prevent; claiming to be a scratch copy
    invites careless destruction on what might be the real thing. Not knowing is a
    third answer, and it is the honest one.
    """
    label = os.getenv("OBDI_INSTANCE_LABEL", "").strip()
    role = os.getenv("OBDI_INSTANCE_ROLE", "").strip().lower()

    if role == "production":
        return "", ""

    frame = 'class="band"'

    if not label and not role:
        return (
            f"<p {frame}><strong>Instance not identified.</strong> This deployment "
            "does not say whether it holds real data. Set OBDI_INSTANCE_LABEL and "
            "OBDI_INSTANCE_ROLE.</p>",
            "unidentified",
        )

    shown = html.escape(label or "unnamed")
    return (
        f"<p {frame}><strong>{shown} - not production.</strong> This instance does "
        "not hold the real data, and anything done here affects nothing else.</p>",
        shown,
    )


def render_page(title: str, body: str, *, wide: bool = False) -> bytes:
    """A plain confirmation page.

    Deliberately styled to be unmistakable at a glance, because the failure
    mode being guarded against is someone assuming an authorisation worked when
    it did not, and only discovering it a quarter later.

    Every page carries the instance identification rather than only the homepage:
    the destructive controls are not all in one place, and a link opens wherever it
    points.

    `wide` is for a page made of cards, which then sit abreast on a desktop.
    A page of prose and forms stays narrow, where a line is short enough to read.
    """
    banner, prefix = instance_identity()
    if prefix:
        title = f"[{prefix}] {title}"
        body = banner + body
    body = structure_tables(body, fallback_name=html.escape(title))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
{STYLESHEET}</style></head>
<body{' class="wide"' if wide else ""}><a class="skip" href="#main">Skip to content</a>
{navigation_html()}
<main id="main"><h1>{html.escape(title)}</h1>{body}</main>
<footer>obdi {html.escape(describe())}</footer></body></html>
""".encode()


class CallbackHandler(BaseHTTPRequestHandler):
    """Handles the single redirect path. Everything else is a 404."""

    callback_path = "/callback"
    code_handler: CodeHandler | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") != self.callback_path.rstrip("/"):
            self._respond(404, render_page("Not found", "Nothing is served here."))
            return

        params = parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        if error:
            # Surfaced verbatim: a declined or abandoned authorisation is
            # otherwise indistinguishable from a broken integration.
            detail = html.escape(params.get("error_description", [""])[0] or error)
            self._respond(400, render_page("Authorisation failed", detail))
            return

        code = params.get("code", [""])[0]
        if not code:
            self._respond(
                400,
                render_page(
                    "No authorisation code",
                    "The provider redirected here without a code. Start again from "
                    "<code>auth-link</code>.",
                ),
            )
            return

        if self.code_handler is None:
            self._respond(500, render_page("Not configured", "No handler is attached."))
            return

        try:
            message = self.code_handler(code, params.get("state", [""])[0])
        except Exception as exc:
            self._respond(500, render_page("Exchange failed", html.escape(str(exc))))
            return

        self._respond(200, render_page("Connected", html.escape(message)))

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default access log.

        A redirect URL carries the authorisation code in its query string, and
        the default handler would write it straight to stdout and from there
        into container logs.
        """
        return


def serve(code_handler: CodeHandler, *, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the receiver.

    Bound to loopback by convention: exposure is the fronting layer's job, not
    this process's. Binding to all interfaces would put an endpoint that
    accepts authorisation codes on the LAN.
    """
    handler = type("BoundCallbackHandler", (CallbackHandler,), {"code_handler": code_handler})
    server = HTTPServer((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def configured_redirect_uri() -> str:
    """The redirect URI this receiver expects to be reached at.

    Must match what is registered with the provider byte for byte. Whatever
    hostname the browser doing the authorising can reach, e.g.
    https://obdi.example.com/callback
    """
    return os.getenv("TRUELAYER_REDIRECT_URI", "https://localhost:8080/callback")
