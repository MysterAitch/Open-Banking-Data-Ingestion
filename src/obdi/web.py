"""A small web interface for connecting banks, usable from a phone.

The quarterly re-authorisation is a manual chore by regulation, so the only
thing left to improve is how unpleasant it is. Doing it from a phone means the
bank's own app handles authentication - biometrics rather than typing a
password into a desktop browser and juggling a second factor.

This works because the OAuth redirect is a BROWSER event. The provider never
connects inbound; it sends the browser somewhere. So the whole flow needs only
a page your phone can reach: a loopback-bound service fronted by anything that
gives it a trusted certificate on a privately reachable hostname, with nothing
published to the internet.

Three routes:

    GET /           connections and their consent clocks, and a way to add one
    GET /connect    mint a state, send the browser to the bank
    GET /callback   verify the state, exchange the code, save the connection

The `state` parameter is not decoration. It carries which connection is being
authorised through a redirect this service does not control, and it is verified
on return so that a code cannot be replayed into an unexpected connection.
"""

from __future__ import annotations

import html
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from urllib.parse import parse_qs, quote, urlparse

from .callback import render_page
from .connections import ConnectionStore, build_connection
from .coverage import SourceCoverage
from .doctor import shape_problems
from .providers.truelayer import build_auth_link, exchange_code
from .secrets import SecretError, read_secret

# A person walking to another room mid-authorisation is normal; a state hanging
# around for hours is not.
STATE_LIFETIME = timedelta(minutes=30)


@dataclass
class PendingAuthorisation:
    connection_name: str
    created_at: datetime


class AuthorisationSession:
    """Tracks in-flight authorisations, keyed by their state parameter."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingAuthorisation] = {}

    def begin(self, connection_name: str, *, now: datetime | None = None) -> str:
        moment = now or datetime.now(UTC)
        # Evict on the way in. /connect needs no authentication, so anything
        # that can reach it could mint states indefinitely - and abandoned
        # authorisations accumulate in ordinary use too, since starting one and
        # not finishing it is common. Without this the process grows for as
        # long as it runs.
        self._evict_expired(moment)
        state = token_urlsafe(24)
        self._pending[state] = PendingAuthorisation(
            connection_name=connection_name, created_at=moment
        )
        return state

    def _evict_expired(self, now: datetime) -> None:
        expired = [
            state
            for state, pending in self._pending.items()
            if now - pending.created_at > STATE_LIFETIME
        ]
        for state in expired:
            del self._pending[state]

    def claim(self, state: str, *, now: datetime | None = None) -> str:
        """Consume a state and return its connection name.

        Single use: a state that has been redeemed cannot be redeemed again,
        so a code replayed from a browser history cannot rebind a connection.
        """
        pending = self._pending.pop(state, None)
        if pending is None:
            raise KeyError("unknown or already-used state")
        if (now or datetime.now(UTC)) - pending.created_at > STATE_LIFETIME:
            raise KeyError("this authorisation took too long and has expired")
        return pending.connection_name

    def __len__(self) -> int:
        return len(self._pending)


@dataclass
class WebConfig:
    client_id: str
    #: Either the value itself, or a callable that fetches it. The callable
    #: form is what production uses, and it is the fix for a failure observed
    #: live: the secret is used only at code exchange, a handful of times a
    #: quarter, so caching it at startup buys nothing - and it made a rotation
    #: invisible, with the new value on disk approved by every fresh process
    #: while the serving one kept the old value in memory. Reading at USE makes
    #: rotation atomic with the write, and no restart choreography exists to
    #: get wrong.
    client_secret: str | Callable[[], str]
    redirect_uri: str
    connection_store: ConnectionStore
    #: Called with the connection name and the AUTHORISER'S address the moment
    #: fetch deep history while the strong customer authentication is still
    #: fresh. Injected rather than imported so this module keeps knowing nothing
    #: about stores and providers - and so a test can prove the callback fires
    #: without a database or a bank. Returning False makes the page say plainly
    #: that no backfill ran, rather than implying one did.
    start_backfill: Callable[[str, str | None], bool] | None = None

    #: Ran between the connect click and the redirect to the bank, returning
    #: human-readable concerns; empty means clear. Injected like the backfill
    #: hook, so this module stays ignorant of doctors and providers and a test
    #: can exercise the fork without a network. The expensive failure this
    #: prevents is a completed bank login whose single-use code burns against a
    #: credential that could never exchange it.
    preflight: Callable[[], list[str]] | None = None
    #: Returns per-account coverage rows for the homepage, so "did the backfill
    #: work, and how far back?" is answered where the person already is instead
    #: of in a shell. Injected like the others; None hides the section.
    holdings: Callable[[], list[SourceCoverage]] | None = None

    def current_client_secret(self) -> str:
        value = self.client_secret
        return value() if callable(value) else value


def _connection_rows(store: ConnectionStore) -> str:
    connections = sorted(store, key=lambda c: c.connection_id)
    if not connections:
        return "<p>No banks connected yet.</p>"

    rows = []
    for connection in connections:
        days = connection.consent_days_remaining()
        if connection.consent_expired():
            state = '<span class="bad">expired - reconnect now</span>'
        elif connection.consent_needs_attention():
            state = f'<span class="warn">expires in {days} days</span>'
        else:
            state = f'<span class="ok">{days} days left</span>'
        display = html.escape(connection.connection_id)
        # Escaping protects the page but not the query string. An unencoded
        # ampersand or hash truncates the name in the link, so the reconnect
        # would target a DIFFERENT connection - and using a name that does not
        # already exist silently creates a second connection to one bank.
        target = quote(connection.connection_id, safe="")
        rows.append(
            f'<div class="row"><strong>{display}</strong><br>{state}'
            f'<br><a class="button" href="/connect?name={target}">Reconnect {display}</a></div>'
        )
    return "".join(rows)


HOME_LINK = '<p><a class="button" href="/">Back to connections</a></p>'


def error_page(title: str, message_html: str) -> bytes:
    """An error page that always offers the way back.

    Read on a phone mid-flow, a dead-end error page forces editing the address
    bar to recover - the exact friction this interface exists to remove, at the
    moment the reader is most likely to retry.
    """
    return render_page(title, f"{message_html}{HOME_LINK}")


def _credential_banner() -> str:
    """A prominent warning when the secret on disk cannot work, or empty.

    On the homepage rather than at startup, and a banner rather than a refusal:
    the page's local duties - consent clocks, reconnect links - owe nothing to
    an online-only credential, so a malformed secret must not take them down.
    But it must also not wait silently for the next authorisation to fail with
    a burnt single-use code. Checked at render, so it reflects the file as it
    is NOW - a fixed secret clears the banner on refresh, no restart.
    """
    try:
        value = read_secret("TRUELAYER_CLIENT_SECRET", required=False)
    except SecretError as exc:
        return (
            f'<p class="bad"><strong>TrueLayer secret unreadable:</strong> '
            f"{html.escape(str(exc))} Bank authorisation will fail until this is fixed.</p>"
        )
    if not value:
        return ""
    problems = shape_problems("TRUELAYER_CLIENT_SECRET", value)
    if not problems:
        return ""
    detail = html.escape("; ".join(problems))
    return (
        f'<p class="bad"><strong>TrueLayer secret looks malformed:</strong> {detail}. '
        "Bank authorisation will fail until this is fixed - the value itself is "
        "never shown here.</p>"
    )


def _holdings_rows(holdings: Callable[[], list[SourceCoverage]] | None) -> str:
    """What the store holds, per account and source - or nothing, quietly.

    Failure here must never take down the page that manages connections: the
    store may legitimately be mid-write during a backfill, which is exactly
    when someone is refreshing to see how it is going.
    """
    if holdings is None:
        return ""
    try:
        rows = holdings()
    except Exception:
        return ""
    if not rows:
        return ""
    items = []
    for row in rows:
        items.append(
            f'<div class="row"><strong>{html.escape(row.account_id)}</strong>'
            f" via {html.escape(row.source)}<br>"
            f"{row.count:,} transactions, {row.earliest} .. {row.latest}</div>"
        )
    return "<h2>Held so far</h2>" + "".join(items)


def render_index(
    store: ConnectionStore, holdings: Callable[[], list[SourceCoverage]] | None = None
) -> bytes:
    body = f"""
{_credential_banner()}
{_connection_rows(store)}
{_holdings_rows(holdings)}
<h2>Add a bank</h2>
<form action="/connect" method="get">
  <p><input name="name" placeholder="a name you will recognise, e.g. halifax" required></p>
  <p><button class="button" type="submit"
     style="border:0;width:100%;font-size:inherit;cursor:pointer">Connect</button></p>
</form>
<p style="opacity:.7;font-size:.9rem">Reconnecting keeps the same name on purpose:
a new name would create a second connection to the same bank.</p>
"""
    return render_page("Bank connections", body)


class ConnectionHandler(BaseHTTPRequestHandler):
    config: WebConfig | None = None
    session: AuthorisationSession | None = None

    @property
    def bound_config(self) -> WebConfig:
        """Configuration, or a clear failure if the handler was never bound.

        The class attributes exist because the server instantiates handlers
        itself, so they cannot be constructor arguments. Narrowing here means
        an unbound handler fails once, saying so, rather than raising an
        attribute error on None somewhere inside request handling.
        """
        if self.config is None:
            raise RuntimeError("handler was not bound to a configuration")
        return self.config

    @property
    def bound_session(self) -> AuthorisationSession:
        if self.session is None:
            raise RuntimeError("handler was not bound to an authorisation session")
        return self.session

    # A socket that opens and then says nothing must not hold the handler
    # forever. Without this a single idle connection wedges the whole service,
    # including the OAuth callback - so an authorisation already in flight
    # could never complete, and the failure would look like the bank's fault.
    timeout = 30

    @staticmethod
    def make_server(address: tuple[str, int], handler: type) -> ThreadingHTTPServer:
        """Build the server this handler expects.

        Threading rather than the single-threaded default: the callback must
        stay answerable while anything else is connected, and a phone that
        backgrounds mid-authorisation leaves exactly that kind of half-open
        connection behind.
        """
        return ThreadingHTTPServer(address, handler)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except TimeoutError:
            self.close_connection = True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if route == "/":
            self._respond(
                200,
                render_index(
                    self.bound_config.connection_store,
                    holdings=self.bound_config.holdings,
                ),
            )
        elif route == "/connect":
            self._connect(params)
        elif route == "/callback":
            self._callback(params)
        else:
            self._respond(404, error_page("Not found", "<p>Nothing is served here.</p>"))

    def _connect(self, params: dict[str, list[str]]) -> None:
        name = (params.get("name", [""])[0] or "").strip()
        if not name:
            self._respond(400, error_page("Name required", "<p>Give the connection a name.</p>"))
            return

        forced = params.get("force", ["0"])[0] == "1"
        check = self.bound_config.preflight
        if check is not None and not forced:
            concerns = check()
            if concerns:
                # Stop HERE, before any state is minted: no journey has
                # started, so nothing should be waiting for one to return. The
                # override exists because a preflight that cannot be overridden
                # becomes a gate whenever the check itself is wrong.
                listed = "".join(f"<li>{html.escape(c)}</li>" for c in concerns)
                target = quote(name, safe="")
                self._respond(
                    200,
                    error_page(
                        "Before you go to the bank",
                        f"<p>These would make the authorisation fail at the last "
                        f"step, after the bank login:</p><ul>{listed}</ul>"
                        f'<p><a class="button" href="/connect?name={target}&force=1">'
                        "Try anyway</a></p>",
                    ),
                )
                return

        state = self.bound_session.begin(name)
        link = build_auth_link(
            client_id=self.bound_config.client_id,
            redirect_uri=self.bound_config.redirect_uri,
            state=state,
        )
        self.send_response(302)
        self.send_header("Location", link)
        self.end_headers()

    def _callback(self, params: dict[str, list[str]]) -> None:
        error = params.get("error", [""])[0]
        if error:
            detail = html.escape(params.get("error_description", [""])[0] or error)
            self._respond(400, error_page("Authorisation failed", f"<p>{detail}</p>"))
            return

        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]
        if not code:
            self._respond(
                400,
                error_page(
                    "No code returned",
                    "<p>The provider sent no authorisation code back.</p>",
                ),
            )
            return

        try:
            name = self.bound_session.claim(state)
        except KeyError as exc:
            # Refusing here is the point of the state parameter: without it a
            # code could be redeemed into a connection nobody chose.
            self._respond(
                400,
                error_page("Could not verify this request", f"<p>{html.escape(str(exc))}</p>"),
            )
            return

        try:
            tokens = exchange_code(
                code=code,
                client_id=self.bound_config.client_id,
                client_secret=self.bound_config.current_client_secret(),
                redirect_uri=self.bound_config.redirect_uri,
            )
            connection = build_connection(
                connection_id=name, provider=name, token_response=tokens
            )
            self.bound_config.connection_store.put(connection)
        except Exception as exc:
            # To the container log as well as the page: the page vanishes with
            # the phone browser, and this is the one failure that has to be
            # debuggable after the fact.
            print(f"authorisation for {name!r} failed: {exc}", file=sys.stderr)
            detail = f"<p>{html.escape(str(exc))}</p>"
            if "invalid_client" in str(exc):
                # Say what the secret file looks like RIGHT NOW, so a stale
                # process and a bad file are distinguishable from the error
                # page itself instead of needing a shell.
                verdict = _credential_banner() or (
                    '<p>The secret file currently on disk looks well-formed, so if '
                    "this happened just after a rotation the provider may not have "
                    "propagated it yet (up to 15 minutes) - or the value, though "
                    "shaped correctly, is not the one the provider issued.</p>"
                )
                detail += verdict
            self._respond(500, error_page("Could not save", detail))
            return

        # Start the backfill NOW, before the page is even rendered. Anything
        # older than ninety days needs strong customer authentication, and the
        # only moment one has just happened is this one. A scheduler running
        # hours later gets the ninety-day cap and the rest is unrecoverable -
        # not harder to fetch, gone. Leaving it to the operator makes the
        # irreplaceable part of the job depend on remembering to act within
        # minutes, on a phone, having just finished a bank login.
        starter = self.bound_config.start_backfill
        # The device on THIS request just completed the bank's strong customer
        # authentication - presence proven, not assumed - so the backfill may
        # honestly declare its address. But behind the TLS-terminating proxy
        # the socket peer is loopback, not the person: the true client address
        # arrives in X-Forwarded-For. Prefer it, and when the only candidate is
        # loopback, declare NOTHING - asserting 127.0.0.1 as a customer's
        # address to a regulated counterparty is worse than silence.
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        peer = self.client_address[0]
        psu_ip = forwarded or (peer if not peer.startswith("127.") else None)
        started = starter(name, psu_ip) if starter is not None else False

        days = connection.consent_days_remaining()
        note = (
            "<p>Fetching your history now - this is the one moment deep history is "
            "reachable, so it starts automatically. It runs in the background; "
            "check back shortly.</p>"
            if started
            else "<p><strong>No backfill was started.</strong> Run "
            f"<code>obdi pull {html.escape(name)}</code> <strong>now</strong> - "
            "anything beyond ninety days needs the authentication you just "
            "completed, and that window closes within minutes.</p>"
        )
        self._respond(
            200,
            render_page(
                f"Connected {name}",
                f"<p>Consent lasts {days} days and cannot be extended by software.</p>"
                f"{note}"
                '<p><a class="button" href="/">Back to connections</a></p>',
            ),
        )

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the access log: the callback URL carries the code."""
        return


def serve(config: WebConfig, *, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the interface.

    Loopback by default. Exposure is the fronting layer's job - binding to all
    interfaces would put a page that can initiate bank authorisations onto the
    LAN, reachable by anything on the network.
    """
    handler = type(
        "BoundConnectionHandler",
        (ConnectionHandler,),
        {"config": config, "session": AuthorisationSession()},
    )
    server = ConnectionHandler.make_server((host, port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
