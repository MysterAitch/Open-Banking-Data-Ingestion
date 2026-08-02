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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto;
        padding: 0 1rem; line-height: 1.5; }}
 h1 {{ font-size: 1.4rem; }}
 code {{ background: #8883; padding: .1rem .3rem; border-radius: .2rem; }}
 /* Tap targets sized for a thumb: this is used from a phone. */
 a.button, button.button {{ display: block; padding: .9rem 1rem; margin: .5rem 0;
            border-radius: .5rem;
            background: #2563eb; color: #fff; text-decoration: none; text-align: center;
            font-weight: 600; }}
 .row {{ padding: .8rem 0; border-bottom: 1px solid #8884; }}
 table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
 th, td {{ padding: .45rem .5rem; text-align: left; border-bottom: 1px solid #8883;
          vertical-align: top; }}
 th {{ opacity: .7; font-weight: 600; }}
 .scroll {{ overflow-x: auto; }}
 .pill {{ display: inline-block; padding: .1rem .55rem; border-radius: 1rem;
         font-size: .85em; font-weight: 600; white-space: nowrap; }}
 .pill-ok {{ background: #16a34a22; color: #15803d; }}
 .pill-bad {{ background: #dc262622; color: #b91c1c; }}
 .pill-quiet {{ background: #8882; }}
 .muted {{ opacity: .65; }}
 .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         font-size: .85em; word-break: break-all; }}
 .warn {{ color: #b45309; font-weight: 600; }}
 .bad {{ color: #b91c1c; font-weight: 600; }}
 .ok {{ opacity: .75; }}
 input {{ font-size: 1rem; padding: .7rem; width: 100%; box-sizing: border-box;
         border-radius: .4rem; border: 1px solid #8886; }}
</style></head>
<body><h1>{html.escape(title)}</h1>{body}
<footer style="margin-top:2rem;opacity:.6;font-size:.85rem">
obdi {html.escape(describe())}</footer></body></html>
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
