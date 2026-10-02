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

    frame = 'class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem"'

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


def render_page(title: str, body: str) -> bytes:
    """A plain confirmation page.

    Deliberately styled to be unmistakable at a glance, because the failure
    mode being guarded against is someone assuming an authorisation worked when
    it did not, and only discovering it a quarter later.

    Every page carries the instance identification rather than only the homepage:
    the destructive controls are not all in one place, and a link opens wherever it
    points.
    """
    banner, prefix = instance_identity()
    if prefix:
        title = f"[{prefix}] {title}"
        body = banner + body
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
 /* The floor under every control, including the inline ones whose own padding
    is smaller. */
 a.button, button.button {{ min-height: 44px; box-sizing: border-box; }}
 /* Secondary weight: still thumb-sized, but outlined so the one primary
    control on a page is the heaviest thing on it. */
 a.button.secondary, button.button.secondary {{ background: transparent; color: #2563eb;
            border: 2px solid #2563eb; }}
 /* A bare submit is the ACTION of the page it sits on, and it used to
    render as the browser's default control - a small grey rectangle,
    directly above a full-width navigation link. Missing it meant leaving
    the page instead of doing the thing. Sized like the link below it so
    the two are equally reachable, and outlined rather than filled so the
    doing and the leaving are still told apart at a glance. */
 form button:not(.button) {{ display: block; width: 100%; box-sizing: border-box;
            padding: .9rem 1rem; margin: .5rem 0 1rem; border-radius: .5rem;
            font-size: 1rem; font-weight: 600; cursor: pointer;
            background: transparent; color: #2563eb;
            border: 2px solid #2563eb; }}
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
 /* A date or a sum of money is read whole. The monospace rule above breaks
    anywhere, which is right for a long identifier and split a date across
    three lines in a narrow table cell.
    Worded with care: this text is in every page, and pages that must show
    no money are tested by searching them for a figure and for the word
    itself, so neither appears here. */
 .nowrap {{ white-space: nowrap; word-break: normal; }}
 .warn {{ color: #b45309; font-weight: 600; }}
 .bad {{ color: #b91c1c; font-weight: 600; }}
 .ok {{ opacity: .75; }}
 input {{ font-size: 1rem; padding: .7rem; width: 100%; box-sizing: border-box;
         border-radius: .4rem; border: 1px solid #8886; }}
 /* Navigation: a wrapping row of links, each thumb-tall, none a full-width
    button, so the page's own action stays the heaviest thing on it. */
 .sitenav ul {{ list-style: none; margin: 0 0 1rem; padding: 0; display: flex;
               flex-wrap: wrap; gap: .3rem; }}
 .sitenav a {{ display: flex; align-items: center; min-height: 44px; padding: 0 .75rem;
              box-sizing: border-box; border-radius: .5rem; border: 1px solid #8886;
              color: inherit; text-decoration: none; }}
 .sitenav a[aria-current="page"] {{ background: #2563eb; border-color: #2563eb;
              color: #fff; font-weight: 600; }}
 /* A link that is not a button but is still a thumb-sized target. */
 .linklist {{ list-style: none; margin: 0; padding: 0; }}
 a.tap {{ display: inline-flex; align-items: center; min-height: 44px;
         padding: 0 .5rem; box-sizing: border-box; }}
 .pill-warn {{ background: #b4530922; color: #b45309; }}
 .overview h2 {{ font-size: 1.2rem; margin: 1.6rem 0 .4rem; }}
 /* The Overview: what needs a person is the heaviest thing on the page, and a
    healthy answer is one calm line. */
 .attention {{ list-style: none; margin: .5rem 0; padding: 0; }}
 .attention li {{ margin: .6rem 0; padding: .7rem .9rem; border-radius: .5rem;
                 border: 1px solid #8884; border-left: .4rem solid #b91c1c; }}
 .attention li.soon {{ border-left-color: #b45309; }}
 .attention li.housekeeping {{ border-left-color: #8886; }}
 .attention p {{ margin: .25rem 0; }}
 .allclear {{ margin: .5rem 0; padding: .6rem .9rem; border-radius: .5rem;
             border: 1px solid #16a34a55; }}
 .legend {{ font-size: .85rem; margin: .5rem 0; padding-left: 1.1rem; }}
 /* Accounts: one card each, so the same markup reads on a phone and sits two
    abreast where there is room. A table of eight columns did neither. */
 .accounts {{ list-style: none; margin: .5rem 0; padding: 0; display: grid;
             grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); gap: .6rem; }}
 .account {{ padding: .7rem .9rem; border-radius: .5rem; border: 1px solid #8884; }}
 .account p {{ margin: .2rem 0; }}
 .account-name {{ font-size: 1.05rem; }}
 .facts {{ display: grid; grid-template-columns: 1fr 1fr; gap: .3rem .9rem;
          margin: .5rem 0 .2rem; }}
 .facts dt {{ font-size: .78rem; opacity: .65; }}
 .facts dd {{ margin: 0; }}
 .anchors {{ list-style: none; margin: .5rem 0; padding: 0; }}
 .anchors li {{ padding: .5rem 0; border-bottom: 1px solid #8883; }}
 .anchors p {{ margin: .15rem 0; }}
 /* The headline figure of a position, an account, or an asset. */
 .figure {{ font-size: 1.35rem; font-weight: 700; margin: .3rem 0; }}
 .chart {{ margin: .6rem 0; }}
 /* A step between months: a text link beside the heading it steps, and a
    button only because stepping while values are shown must be a POST. It is
    set after the bare-submit rule above so it wins over it. */
 .monthnav {{ display: flex; flex-wrap: wrap; gap: 0 .9rem; margin: 0 0 .5rem; }}
 .monthnav form {{ margin: 0; }}
 form button.tap {{ display: inline-flex; width: auto; margin: 0; padding: 0 .5rem;
            border: 0; background: none; color: #2563eb; font: inherit;
            font-weight: 400; text-decoration: underline; }}
 /* One transaction per item, so nothing sits in a sideways-scrolling table. */
 .txns {{ list-style: none; margin: .5rem 0; padding: 0; }}
 .txns li {{ padding: .6rem 0; border-bottom: 1px solid #8883; }}
 .txns p {{ margin: .2rem 0; overflow-wrap: anywhere; }}
 .txn-head {{ display: flex; justify-content: space-between; gap: .75rem; }}
 .txns .pill {{ white-space: normal; }}
 details summary {{ cursor: pointer; min-height: 44px; display: flex;
                   align-items: center; opacity: .75; }}
 /* The flex layout above removes the browser's own disclosure marker, so a
    fold would read as plain text without one. */
 details summary::before {{ content: "+"; display: inline-block; width: 1.2rem;
                           font-weight: 700; color: #2563eb; }}
 details[open] summary::before {{ content: "-"; }}
 input[type="checkbox"] {{ width: auto; margin-right: .4rem; }}
 /* What a page is for, in one or two sentences before anything else. */
 .lede {{ margin: .2rem 0 1rem; }}
 /* The newest push and audit, readable at a glance. */
 .leadlines p {{ margin: .35rem 0; }}
 /* The home page's System strip: five facts, each a link to its page. */
 .system {{ list-style: none; margin: .5rem 0; padding: 0; display: grid;
           grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr)); gap: .6rem; }}
 .fact {{ padding: .5rem .9rem; border-radius: .5rem; border: 1px solid #8884; }}
 .fact p {{ margin: .15rem 0; }}
 /* A row of outlined links, thumb-tall, wrapping on a narrow screen. */
 .linkrow {{ list-style: none; margin: .5rem 0; padding: 0; display: flex;
            flex-wrap: wrap; gap: .3rem; }}
 a.tap.outline {{ border: 1px solid #8886; border-radius: .5rem; padding: 0 .75rem; }}
 /* A one-off experiment sits apart from the repairs above it. */
 details.oneoff {{ margin-top: 1.5rem; padding-top: .5rem; border-top: 1px solid #8884; }}
</style></head>
<body>{navigation_html()}<h1>{html.escape(title)}</h1>{body}
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
