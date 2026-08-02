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
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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


@dataclass(frozen=True)
class ExtendableAccount:
    """One provider account whose history window can be pushed further back."""

    connection: str
    provider_ref: str
    display: str
    earliest: date | None
    #: How far back a window has ALREADY been asked for, held data or not.
    #: This is what lets repeated presses on an empty account keep walking
    #: back instead of re-asking the same span forever.
    probed_back_to: date | None = None
    #: One advisory line about the authentication window ("likely OPEN, 3 min
    #: left" / "closed - re-authorise"). Never a gate: the provider is the
    #: authority, this just saves spending a call to learn the obvious.
    auth_note: str = ""
    #: Where the provider stopped granting, once found to the day. When set,
    #: probing is DE-EMPHASISED (folded away), not prohibited - the boundary
    #: was observed, and observations can be re-tested.
    boundary: date | None = None


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
    #: Accounts whose history can be extended from the page, and the hook that
    #: performs one extension. Injected like everything else. Each extension is
    #: a real person pressing a real button - the forwarded address rides along
    #: as the attended declaration, which is what makes button-driven probing
    #: legitimately exempt from the unattended cap rather than a workaround.
    extendables: Callable[[], list[ExtendableAccount]] | None = None
    extend_window: Callable[..., str] | None = None
    #: The raw-evidence browser: a listing of landed artefacts, and per-id
    #: detail carrying static circumstances beside computed shape. Injected
    #: like every other hook; the page renders whatever dictionaries arrive,
    #: so the analysis lives beside the store, not in the HTML.
    artefact_index: Callable[[], list[dict[str, object]]] | None = None
    artefact_detail: Callable[..., dict[str, object] | None] | None = None
    #: The fetch-attempt ledger: every ask made of a provider, refused or
    #: landed, plus per-account call counts over the last day. The probing
    #: workflow is press, read, decide - and deciding needs this without a
    #: shell.
    attempts_index: Callable[[], dict[str, object]] | None = None
    #: One press, walked as far as the provider allows: attended because the
    #: person is present and waiting, honest because it stops the moment the
    #: provider says stop and never replays unattended.
    extend_max: Callable[..., str] | None = None
    #: The merged layer summarised per account - the same computed-shape
    #: analysis as an artefact, over what the store believes after matching.
    account_shape: Callable[..., dict[str, object] | None] | None = None

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


def _trigger_of(request_meta: object) -> str:
    try:
        meta = json.loads(str(request_meta or ""))
    except ValueError:
        return ""
    return str(meta.get("trigger", "")) if isinstance(meta, dict) else ""


def _shape_detail(field: dict[str, object]) -> str:
    """One cell saying what a field CONTAINS, chosen by what it is.

    Categories get their values tallied, identifiers get their shape
    (cardinality, length, prefix, format), ordered things keep their range.
    Escaped here because the values are provider data, not our markup.
    """
    values = field.get("values")
    if isinstance(values, list) and values:
        listed = ", ".join(
            f"{html.escape(str(v.get('value')))} x{v.get('count')}"
            for v in values
            if isinstance(v, dict)
        )
        return f"{field.get('distinct')} distinct: {listed}"
    length = field.get("length")
    if isinstance(length, dict):
        bits = [
            f"{field.get('distinct')} distinct",
            f"length {length.get('min')}-{length.get('max')}",
        ]
        prefix = field.get("prefix")
        if prefix:
            bits.append(f"prefix {html.escape(str(prefix))}")
        fmt = field.get("format")
        if fmt:
            bits.append(html.escape(str(fmt)))
        return ", ".join(bits)
    if field.get("min") is not None:
        return (
            f"{html.escape(str(field.get('min')))} .. "
            f"{html.escape(str(field.get('max')))}"
        )
    return "-"


def _shape_html(summary: dict[str, object]) -> str:
    """The computed-shape block, shared by the artefact and account pages."""
    raw_fields = summary.get("fields")
    fields = raw_fields if isinstance(raw_fields, list) else []
    field_rows = "".join(
        f'<tr><td>{html.escape(str(field.get("path")))}</td>'
        f'<td>{field.get("present")}</td>'
        f'<td>{html.escape(", ".join(field.get("types", [])))}</td>'
        f"<td>{_shape_detail(field)}</td></tr>"
        for field in fields
        if isinstance(field, dict)
    )
    return (
        f'<p>{summary.get("items", 0)} item(s), {summary.get("bytes", 0):,} bytes</p>'
        "<table><tr><th>field</th><th>present</th><th>types</th>"
        f"<th>values / shape</th></tr>{field_rows}</table>"
        f"{_insight_sections(summary)}"
    )


def _insight_sections(summary: dict[str, object]) -> str:
    """The cross-field evidence: sign agreement, presence patterns, months.

    Payload forensics, deliberately not finance analytics - the boundary is
    "evidence about the payload" (parsing decisions, gaps, provider
    semantics) versus "insight about the money" (the budgeting app's job).
    """
    parts: list[str] = []
    sign_by = summary.get("sign_by")
    if isinstance(sign_by, list) and sign_by:
        rows = "".join(
            f'<tr><td>{html.escape(str(r.get("field")))}</td>'
            f'<td>{html.escape(str(r.get("value")))}</td>'
            f'<td>{r.get("positive")}</td><td>{r.get("negative")}</td>'
            f'<td>{r.get("zero")}</td></tr>'
            for r in sign_by
            if isinstance(r, dict)
        )
        parts.append(
            "<h2>Amount sign by category</h2>"
            "<p>The sign-convention check: a category whose row mixes "
            "positive and negative is either genuinely mixed or a parsing "
            "problem.</p>"
            "<table><tr><th>field</th><th>value</th><th>positive</th>"
            f"<th>negative</th><th>zero</th></tr>{rows}</table>"
        )
    links = summary.get("presence_links")
    if isinstance(links, list) and links:
        items = "".join(
            f"<li><code>{html.escape(str(link.get('field')))}</code> is present in "
            f"{link.get('present')} of {link.get('total')} items where "
            f"<code>{html.escape(str(link.get('by')))}</code> = "
            f"{html.escape(str(link.get('value')))} "
            f"({link.get('overall_present')} of {summary.get('items')} overall)</li>"
            for link in links
            if isinstance(link, dict)
        )
        parts.append(
            "<h2>Presence patterns</h2>"
            "<p>Fields that appear or vanish exactly with one category value "
            "- provider semantics nothing documents.</p>"
            f"<ul>{items}</ul>"
        )
    by_month = summary.get("by_month")
    if isinstance(by_month, list) and by_month:
        counts = [
            int(str(m.get("count")))
            for m in by_month
            if isinstance(m, dict) and str(m.get("count")).isdigit()
        ]
        peak = max(counts, default=1) or 1
        bars = "\n".join(
            f"{html.escape(str(m.get('month')))} "
            f"{'#' * max(1, round(int(str(m.get('count'))) * 40 / peak))} "
            f"{m.get('count')}"
            for m in by_month
            if isinstance(m, dict)
        )
        parts.append(
            "<h2>Items per month</h2>"
            "<p>A month that should have data and shows no bar is a gap "
            "worth chasing.</p>"
            f'<pre style="overflow-x:auto">{bars}</pre>'
        )
    return "".join(parts)


def refusal_html(exc: Exception) -> str:
    """A provider refusal in parts, not one blob.

    The machine code, the provider's prose, and the upstream detail have
    different audiences; rendered together they read as noise and the actual
    fault hides inside the generic wrapper. Errors without parts fall back to
    their plain text, and the one refusal with a known remedy states it.
    """
    code = str(getattr(exc, "code", "") or "")
    description = str(getattr(exc, "description", "") or "")
    if not code and not description:
        return f"<p>{html.escape(str(exc))}</p>"
    status = getattr(exc, "status", None)
    details = str(getattr(exc, "provider_details", "") or "")
    parts = [
        '<p class="bad">'
        + html.escape(f"HTTP {status}" if status else "Refused")
        + (f" - <code>{html.escape(code)}</code>" if code else "")
        + "</p>"
    ]
    asked = str(getattr(exc, "asked_window", "") or "")
    if asked:
        # What was asked is half the diagnosis - a refusal of "since
        # 2011-04-12 until 2013-04-12" locates the boundary; a bare refusal
        # locates nothing.
        parts.append(f"<p>This press asked for {html.escape(asked)}.</p>")
    if description:
        parts.append(f"<p>{html.escape(description)}</p>")
    if details:
        parts.append(
            f'<p style="opacity:.7">Provider detail: {html.escape(details)}</p>'
        )
    remedy = _REMEDIES.get(code)
    if remedy:
        parts.append(f"<p>{html.escape(remedy)}</p>")
    jargon = [
        (term, meaning)
        for term, meaning in _JARGON.items()
        if term in f"{code} {description} {details}"
    ]
    if jargon:
        parts.append(
            '<p style="opacity:.7;font-size:.9em">'
            + "<br>".join(
                f"<strong>{html.escape(term)}</strong>: {html.escape(meaning)}"
                for term, meaning in jargon
            )
            + "</p>"
        )
    other = [(c, r) for c, r in _REMEDIES.items() if c != code]
    if other:
        parts.append(
            "<details><summary>Other refusal codes and their remedies</summary><ul>"
            + "".join(
                f"<li><code>{html.escape(c)}</code> - {html.escape(r)}</li>"
                for c, r in other
            )
            + "</ul></details>"
        )
    raw = str(getattr(exc, "raw", "") or "")
    if raw:
        try:
            pretty = json.dumps(json.loads(raw), indent=2)
        except ValueError:
            pretty = raw
        parts.append(
            "<details><summary>Full provider response</summary>"
            f'<pre style="overflow-x:auto;white-space:pre-wrap">{html.escape(pretty)}</pre>'
            "</details>"
        )
    return "".join(parts)


# Refusal codes met in practice, each with its one next step. The provider's
# prose says what happened; this says what to do about it - kept apart so
# neither reads as the other. The matching remedy is shown prominently; the
# rest stay a fold away for when the page is being read as documentation.
_JARGON = {
    "SCA": "Strong Customer Authentication - the bank's own login-and-approve step",
    "PSU": "Payment Services User - the account holder themselves; you",
}

_REMEDIES = {
    "sca_exceeded": (
        "History beyond the routine window is only reachable shortly after "
        "authenticating with the bank. Re-authorise this connection from the "
        "home page, then press extend again straight away."
    ),
    "invalid_date_range": (
        "The provider rejected the window itself. When this happens while "
        "walking history back, the refusal boundary is a fixed DATE, not a "
        "span - step down (+90, +7, +1) to find it to the day."
    ),
    "invalid_grant": (
        "The authorisation code was already used or has expired. Start a "
        "fresh authorisation from the home page."
    ),
    "invalid_client": (
        "The provider rejected the client secret."
    ),
    "invalid_redirect_uri": (
        "The redirect URI registered with the provider differs from the one "
        "this deployment used - they must match byte for byte."
    ),
}


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
            f'<div class="row"><strong>'
            f'<a href="/account?ref={quote(row.account_id)}">'
            f"{html.escape(row.account_id)}</a></strong>"
            f" via {html.escape(row.source)}<br>"
            f"{row.count:,} transactions, {row.earliest} .. {row.latest}</div>"
        )
    return "<h2>Held so far</h2>" + "".join(items)


# 1 exists for the endgame: once +7 fails, the boundary is within a week,
# and finding it to the day takes single steps.
EXTEND_CHOICES = (1, 7, 30, 90, 365, 730)


def _extend_rows(
    extendables: Callable[[], list[ExtendableAccount]] | None,
    only_ref: str | None = None,
) -> str:
    """The extend controls - all accounts on the homepage, ONE on a result.

    A result page repeats only the account that was just pressed: mid-probe,
    a wall of every account's buttons is where the wrong account gets
    pressed, and it stops scaling past a handful of accounts at all.
    """
    if extendables is None:
        return ""
    try:
        accounts = extendables()
    except Exception:
        return ""
    if only_ref is not None:
        accounts = [a for a in accounts if a.provider_ref == only_ref]
    if not accounts:
        return ""
    rows = []
    for account in accounts:
        reach = account.earliest.isoformat() if account.earliest else "nothing held yet"
        buttons = "".join(
            f'<form action="/extend" method="post" style="display:inline">'
            f'<input type="hidden" name="connection" value="{html.escape(account.connection)}">'
            f'<input type="hidden" name="account" value="{html.escape(account.provider_ref)}">'
            f'<button class="button" style="display:inline-block;width:auto;'
            f'padding:.5rem .8rem;border:0;cursor:pointer" '
            f'name="days" value="{days}">+{days}d</button></form> '
            for days in EXTEND_CHOICES
        )
        max_button = (
            f'<form method="post" action="/extend-max" style="display:inline">'
            f'<input type="hidden" name="connection" value="{html.escape(account.connection)}">'
            f'<input type="hidden" name="account" value="{html.escape(account.provider_ref)}">'
            f'<button class="button" style="display:inline-block;'
            f'padding:.5rem .8rem;border:0;cursor:pointer" type="submit">'
            f"Extend as far as possible</button></form>"
        )
        controls = f"{buttons}{max_button}"
        if account.boundary:
            controls = (
                f'<p class="ok">Boundary reached: the provider refuses anything '
                f"earlier than {account.boundary.isoformat()} - probing is "
                f"de-emphasised, not forbidden.</p>"
                f"<details><summary>Probe anyway</summary>{controls}</details>"
            )
        note = (
            f'<br><span style="opacity:.75">{html.escape(account.auth_note)}</span>'
            if account.auth_note
            else ""
        )
        rows.append(
            f'<div class="row"><strong>{html.escape(account.display)}</strong> '
            f"({html.escape(account.connection)})<br>history reaches {reach}"
            + (
                f" - probed back to {account.probed_back_to.isoformat()}"
                if account.probed_back_to
                and (account.earliest is None or account.probed_back_to < account.earliest)
                else ""
            )
            + f"{note}<br>{controls}</div>"
        )
    return (
        "<h2>Extend history</h2>"
        "<p>Each press fetches one further window, attended - you are the "
        "customer, actively requesting.</p>" + "".join(rows)
    )


def render_index(
    store: ConnectionStore,
    holdings: Callable[[], list[SourceCoverage]] | None = None,
    extendables: Callable[[], list[ExtendableAccount]] | None = None,
) -> bytes:
    body = f"""
{_credential_banner()}
{_connection_rows(store)}
{_holdings_rows(holdings)}
{_extend_rows(extendables)}
<p><a class="button" href="/artefacts">Browse raw artefacts</a></p>
<p><a class="button" href="/attempts">Fetch attempts</a></p>
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

        if route == "/account":
            self._account(params)
            return
        if route == "/attempts":
            self._attempts()
            return
        if route == "/artefacts":
            self._artefacts()
            return
        if route == "/artefact":
            self._artefact(params)
            return
        if route == "/":
            self._respond(
                200,
                render_index(
                    self.bound_config.connection_store,
                    holdings=self.bound_config.holdings,
                    extendables=self.bound_config.extendables,
                ),
            )
        elif route == "/connect":
            self._connect(params)
        elif route == "/callback":
            self._callback(params)
        else:
            self._respond(404, error_page("Not found", "<p>Nothing is served here.</p>"))

    def _attempts(self) -> None:
        hook = self.bound_config.attempts_index
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No ledger wired.</p>"))
            return
        ledger = hook()
        raw_day = ledger.get("last_day")
        day_rows = "".join(
            f'<tr><td>{html.escape(str(r.get("connection_id")))}</td>'
            f'<td>{html.escape(str(r.get("account_ref")))}</td>'
            f'<td>{r.get("count")}</td></tr>'
            for r in (raw_day if isinstance(raw_day, list) else [])
            if isinstance(r, dict)
        )
        raw_rows = ledger.get("rows")
        rows = "".join(
            f'<tr><td>{html.escape(str(r.get("attempted_at", ""))[:19])}</td>'
            f'<td>{html.escape(str(r.get("connection_id")))} / '
            f'{html.escape(str(r.get("account_ref")))}</td>'
            f'<td>{html.escape(str(r.get("source", "")).removeprefix("truelayer-"))}</td>'
            f'<td style="word-break:break-all">{html.escape(str(r.get("asked", "")))}</td>'
            f'<td>{html.escape(_trigger_of(r.get("request_meta")))}</td>'
            + (
                f'<td class="bad">{r.get("http_status")} '
                f'<code>{html.escape(str(r.get("error_code", "")))}</code></td>'
                if r.get("outcome") == "refused"
                else f'<td class="ok">{html.escape(str(r.get("outcome", "")))}</td>'
            )
            + "</tr>"
            for r in (raw_rows if isinstance(raw_rows, list) else [])
            if isinstance(r, dict)
        )
        body = (
            "<p>Every ask made of a provider, newest first - refused or "
            "landed. Refusals are the valuable rows: what was asked and what "
            "the provider answered is the raw material of the quota model "
            "and the ceiling probes.</p>"
            "<p>A deep-ladder row may cover several provider calls, so deep "
            "rows are a known under-count of quota spend.</p>"
            + (
                "<h2>Calls in the last 24 hours</h2>"
                "<table><tr><th>connection</th><th>account</th><th>calls</th>"
                f"</tr>{day_rows}</table>"
                if day_rows
                else ""
            )
            + "<h2>Attempts</h2>"
            + (
                "<table><tr><th>when (UTC)</th><th>account</th><th>kind</th>"
                f"<th>asked</th><th>trigger</th><th>answer</th></tr>{rows}</table>"
                if rows
                else "<p>No attempts recorded yet - the ledger began at 0.4.5, "
                "so only fetches after that deployment appear.</p>"
            )
            + HOME_LINK
        )
        self._respond(200, render_page("Fetch attempts", body))

    def _artefacts(self) -> None:
        hook = self.bound_config.artefact_index
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No browser wired.</p>"))
            return
        rows = []
        for item in hook():
            origin = html.escape(str(item.get("origin", "")))
            rows.append(
                f'<div class="row"><strong>{html.escape(str(item.get("source", "")))}</strong> '
                f'- {html.escape(str(item.get("account_ref", "")))}<br>'
                f'{html.escape(str(item.get("fetched_at", "")))} - '
                f'{item.get("bytes", 0):,} bytes - '
                f'trigger: {html.escape(str(item.get("trigger", "unrecorded")))}<br>'
                f'<span style="opacity:.7;word-break:break-all">{origin}</span><br>'
                f'<a class="button" href="/artefact?id={item.get("id")}">Inspect</a></div>'
            )
        body = (
            "<p>Every payload landed, newest first: the evidence everything else "
            "derives from.</p>" + ("".join(rows) or "<p>Nothing landed yet.</p>") + HOME_LINK
        )
        self._respond(200, render_page("Raw artefacts", body))

    def _artefact(self, params: dict[str, list[str]]) -> None:
        hook = self.bound_config.artefact_detail
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No browser wired.</p>"))
            return
        try:
            artefact_id = int(params.get("id", ["0"])[0])
        except ValueError:
            artefact_id = 0
        want_payload = params.get("view", [""])[0] == "payload"
        detail = hook(artefact_id, with_payload=want_payload)
        if detail is None:
            self._respond(404, error_page("Not found", "<p>No such artefact.</p>"))
            return

        if want_payload:
            pretty = html.escape(str(detail.get("payload_pretty", "")))
            body = (
                f'<p><a class="button" href="/artefact?id={artefact_id}">'
                "Back to the analysis</a></p>"
                f'<pre style="overflow-x:auto;white-space:pre-wrap">{pretty}</pre>'
                + HOME_LINK
            )
            self._respond(200, render_page("Payload", body))
            return

        raw_meta = detail.get("request_meta")
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        meta_rows = "".join(
            f"<tr><td>{html.escape(str(key))}</td>"
            f"<td>{html.escape(str(value))}</td></tr>"
            for key, value in meta.items()
        )
        raw_summary = detail.get("summary")
        summary: dict[str, object] = raw_summary if isinstance(raw_summary, dict) else {}
        body = (
            f'<p><strong>{html.escape(str(detail.get("source", "")))}</strong> - '
            f'{html.escape(str(detail.get("account_ref", "")))}<br>'
            f'fetched {html.escape(str(detail.get("fetched_at", "")))}<br>'
            f'<span style="opacity:.7;word-break:break-all">'
            f'{html.escape(str(detail.get("origin", "")))}</span></p>'
            "<h2>Request circumstances</h2>"
            f'<table><tr><th>key</th><th>value</th></tr>{meta_rows or ""}</table>'
            "<h2>Computed shape</h2>"
            + _shape_html(summary)
            + f'<p><a class="button" href="/artefact?id={artefact_id}&view=payload">'
            "View payload</a></p>" + HOME_LINK
        )
        self._respond(200, render_page("Artefact", body))

    def _account(self, params: dict[str, list[str]]) -> None:
        hook = self.bound_config.account_shape
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No shape wired.</p>"))
            return
        ref = (params.get("ref", [""])[0] or "").strip()
        shape = hook(ref) if ref else None
        if shape is None:
            self._respond(
                404,
                error_page(
                    "Nothing held",
                    "<p>No merged transactions for this account yet.</p>",
                ),
            )
            return
        raw_summary = shape.get("summary")
        summary: dict[str, object] = raw_summary if isinstance(raw_summary, dict) else {}
        sources = shape.get("sources")
        source_list = ", ".join(
            html.escape(str(s)) for s in (sources if isinstance(sources, list) else [])
        )
        body = (
            f"<p><strong>{html.escape(ref)}</strong><br>"
            f"{shape.get('count', 0):,} merged transaction(s) "
            f"from {source_list or 'unknown sources'}</p>"
            "<p>This is the MERGED layer - what the store believes after "
            "matching - not one payload. The raw artefacts remain the "
            "evidence underneath.</p>"
            "<h2>Computed shape</h2>" + _shape_html(summary) + HOME_LINK
        )
        self._respond(200, render_page("Account", body))

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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if route not in ("/extend", "/extend-max"):
            self._respond(404, error_page("Not found", "<p>Nothing is served here.</p>"))
            return
        walk = route == "/extend-max"
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8"))

        hook = self.bound_config.extend_max if walk else self.bound_config.extend_window
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Extension is not wired.</p>"))
            return

        connection = (form.get("connection", [""])[0] or "").strip()
        account = (form.get("account", [""])[0] or "").strip()
        try:
            days = int(form.get("days", ["0"])[0])
        except ValueError:
            days = 0
        if not connection or not account or (not walk and days <= 0):
            self._respond(
                400,
                error_page("Bad request", "<p>Connection, account and days required.</p>"),
            )
            return

        psu_ip = self._requester_address()
        # The audit line: who pressed, from where, for what - to the container
        # log now, and the landed artefact carries the same declaration
        # permanently in layer 0.
        print(
            f"attended extend{'-max' if walk else ''}: connection={connection} "
            f"account={account} days={days if not walk else 'walk'} "
            f"requested_by={psu_ip or 'unknown'}",
            file=sys.stderr,
        )
        try:
            if walk:
                summary = hook(
                    connection=connection, provider_ref=account, psu_ip=psu_ip
                )
            else:
                summary = hook(
                    connection=connection, provider_ref=account, days=days, psu_ip=psu_ip
                )
        except Exception as exc:
            print(f"extend failed: {exc}", file=sys.stderr)
            # The buttons stay on the refusal page: probing is press, read,
            # press again - the five-minute window after authentication is
            # too short to spend round-tripping through the home page.
            self._respond(
                502,
                error_page(
                    "Could not extend",
                    refusal_html(exc)
                    + _extend_rows(self.bound_config.extendables, only_ref=account),
                ),
            )
            return
        # A walk returns a multi-line transcript; line breaks are meaning.
        rendered = html.escape(summary).replace(chr(10), "<br>")
        self._respond(
            200,
            render_page(
                "Window extended",
                f"<p>{rendered}</p>"
                + _extend_rows(self.bound_config.extendables, only_ref=account)
                + HOME_LINK,
            ),
        )

    def _requester_address(self) -> str | None:
        """The pressing device's address: forwarded first, never loopback."""
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        peer = self.client_address[0]
        return forwarded or (peer if not peer.startswith("127.") else None)

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
            detail = refusal_html(exc)
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
        psu_ip = self._requester_address()
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
