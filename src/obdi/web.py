"""A small web interface for connecting banks, usable from a phone.

The quarterly re-authorisation is a manual chore by regulation, so the only
thing left to improve is how unpleasant it is. Doing it from a phone means the
bank's own app handles authentication - biometrics rather than typing a
password into a desktop browser and juggling a second factor.

This works because the OAuth redirect is a BROWSER event. The provider never
connects inbound; it sends the browser somewhere. So the whole flow needs only
a page your phone can reach, which a loopback-bound service exposed over
Tailscale provides - real certificate, tailnet-only, no public exposure.

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from urllib.parse import parse_qs, quote, urlparse

from .callback import render_page
from .connections import ConnectionStore, build_connection
from .providers.truelayer import build_auth_link, exchange_code

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
        # Evict on the way in. /connect needs no authentication, so anything on
        # the tailnet could mint states indefinitely - and abandoned
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
    client_secret: str
    redirect_uri: str
    connection_store: ConnectionStore


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


def render_index(store: ConnectionStore) -> bytes:
    body = f"""
{_connection_rows(store)}
<h2>Add a bank</h2>
<form action="/connect" method="get">
  <p><input name="name" placeholder="a name you will recognise, e.g. halifax" required></p>
  <p><button class="button" type="submit" style="border:0;width:100%">Connect</button></p>
</form>
<p style="opacity:.7;font-size:.9rem">Reconnecting keeps the same name on purpose:
a new name would create a second connection to the same bank.</p>
"""
    return render_page("Bank connections", body)


class ConnectionHandler(BaseHTTPRequestHandler):
    config: WebConfig | None = None
    session: AuthorisationSession | None = None

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

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if route == "/":
            self._respond(200, render_index(self.config.connection_store))
        elif route == "/connect":
            self._connect(params)
        elif route == "/callback":
            self._callback(params)
        else:
            self._respond(404, render_page("Not found", "<p>Nothing is served here.</p>"))

    def _connect(self, params: dict[str, list[str]]) -> None:
        name = (params.get("name", [""])[0] or "").strip()
        if not name:
            self._respond(400, render_page("Name required", "<p>Give the connection a name.</p>"))
            return

        state = self.session.begin(name)
        link = build_auth_link(
            client_id=self.config.client_id,
            redirect_uri=self.config.redirect_uri,
            state=state,
        )
        self.send_response(302)
        self.send_header("Location", link)
        self.end_headers()

    def _callback(self, params: dict[str, list[str]]) -> None:
        error = params.get("error", [""])[0]
        if error:
            detail = html.escape(params.get("error_description", [""])[0] or error)
            self._respond(400, render_page("Authorisation failed", f"<p>{detail}</p>"))
            return

        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]
        if not code:
            self._respond(
                400,
                render_page("No code returned", '<p><a href="/">Start again</a></p>'),
            )
            return

        try:
            name = self.session.claim(state)
        except KeyError as exc:
            # Refusing here is the point of the state parameter: without it a
            # code could be redeemed into a connection nobody chose.
            self._respond(
                400,
                render_page("Could not verify this request", f"<p>{html.escape(str(exc))}</p>"),
            )
            return

        try:
            tokens = exchange_code(
                code=code,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                redirect_uri=self.config.redirect_uri,
            )
            connection = build_connection(
                connection_id=name, provider=name, token_response=tokens
            )
            self.config.connection_store.put(connection)
        except Exception as exc:  # noqa: BLE001 - the phone must see the reason
            self._respond(500, render_page("Could not save", f"<p>{html.escape(str(exc))}</p>"))
            return

        days = connection.consent_days_remaining()
        self._respond(
            200,
            render_page(
                f"Connected {name}",
                f"<p>Consent lasts {days} days and cannot be extended by software.</p>"
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

    Loopback by default. Exposure is Tailscale Serve's job - binding to all
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
