"""A callback receiver, so authorisation stops involving copy and paste.

The OAuth redirect happens in the BROWSER, not server to server. The provider
never connects to this service - it sends the browser somewhere, and the
browser follows. That is the whole reason a tailnet-only address works: the
redirect target only has to be reachable by the machine holding the session.

So a service bound to loopback and exposed over Tailscale Serve is a perfectly
valid redirect target, with a real certificate and no public exposure. The
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


class CodeHandler(Protocol):
    """Exchanges an authorisation code and returns a message for the browser."""

    def __call__(self, code: str, state: str) -> str: ...


def render_page(title: str, body: str) -> bytes:
    """A plain confirmation page.

    Deliberately styled to be unmistakable at a glance, because the failure
    mode being guarded against is someone assuming an authorisation worked when
    it did not, and only discovering it a quarter later.
    """
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto;
        padding: 0 1rem; line-height: 1.5; }}
 h1 {{ font-size: 1.4rem; }}
 code {{ background: #f1f1f1; padding: .1rem .3rem; border-radius: .2rem; }}
</style></head>
<body><h1>{html.escape(title)}</h1><p>{body}</p></body></html>
""".encode()


class CallbackHandler(BaseHTTPRequestHandler):
    """Handles the single redirect path. Everything else is a 404."""

    callback_path = "/callback"
    code_handler: CodeHandler | None = None

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
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
        except Exception as exc:  # noqa: BLE001 - the browser must see the reason
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

    Bound to loopback by convention: exposure is Tailscale Serve's job, not
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

    Must match what is registered with the provider byte for byte. For a
    tailnet deployment that is the Serve hostname, e.g.
    https://obdi.tailnet-name.ts.net/callback
    """
    return os.getenv("TRUELAYER_REDIRECT_URI", "https://localhost:8080/callback")
