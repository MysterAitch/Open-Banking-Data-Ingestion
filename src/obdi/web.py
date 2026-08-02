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
import itertools
import json
import re
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


class UploadSession:
    """Uploaded bytes held between preview and confirm, keyed by token.

    Same shape as AuthorisationSession and for the same reason: the walk
    from preview to confirm crosses two requests, and abandoned uploads
    must not accumulate for as long as the process lives.
    """

    LIFETIME = timedelta(minutes=15)

    def __init__(self) -> None:
        self._pending: dict[str, tuple[bytes, str, datetime]] = {}

    def stash(self, payload: bytes, filename: str) -> str:
        now = datetime.now(UTC)
        expired = [
            token
            for token, (_, _, created) in self._pending.items()
            if now - created > self.LIFETIME
        ]
        for token in expired:
            del self._pending[token]
        token = token_urlsafe(16)
        self._pending[token] = (payload, filename, now)
        return token

    def claim(self, token: str) -> tuple[bytes, str]:
        payload, filename, created = self._pending.pop(token)
        if datetime.now(UTC) - created > self.LIFETIME:
            raise KeyError("upload expired - upload the file again")
        return payload, filename


def _parse_multipart(content_type: str, body: bytes) -> tuple[bytes, str]:
    """The one file out of a multipart form, without the removed cgi module.

    Minimal on purpose: a single file field from our own form, not a general
    parser. The boundary comes from the content type; the first part with a
    filename wins; payload runs from the blank line to the closing boundary.
    """
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("not a multipart upload")
    boundary = content_type.split(marker, 1)[1].split(";")[0].strip().strip('"')
    delimiter = b"--" + boundary.encode()
    for part in body.split(delimiter):
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers = part[:header_end].decode("utf-8", "replace")
        if "filename=" not in headers:
            continue
        filename = headers.split("filename=", 1)[1].split("\r\n")[0].strip().strip('"')
        payload = part[header_end + 4 :]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        return payload, filename or "upload.csv"
    raise ValueError("no file found in the upload")


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
    #: The canonical id this account currently resolves to, and whether that
    #: is still the source-qualified fallback - unbound accounts get a bind
    #: form right in their row, because naming things should not need a shell.
    canonical: str = ""
    unbound: bool = False
    #: The forward edge: how recent the asked coverage runs (latest `to=`
    #: across landed windows) and when the last payload landed. This is what
    #: makes a quietly-stopped scheduler VISIBLE - held transactions age
    #: silently, but "covered to" falling behind today is unambiguous.
    covered_to: date | None = None
    last_landed: str = ""


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
    #: Bind a provider account to a canonical name from the page: the map
    #: entry plus label moves across every layer. Returns a summary line.
    bind_account: Callable[..., str] | None = None
    #: Facts the pulls have LEARNT per connection (accepted windows, SCA
    #: window length, history boundaries) - a stricter bank shows up here as
    #: different numbers, not as a surprise.
    provider_knowledge: Callable[[], list[dict[str, object]]] | None = None
    #: Starling's presence: a first-party token is not an OAuth connection,
    #: so without this the configured-and-pulling Starling account was
    #: entirely invisible on the page that lists banks.
    starling_status: Callable[[], dict[str, object] | None] | None = None
    #: Human names for canonical refs, resolved from layer 0. Pages show
    #: these with the id demoted to small print - the id is the query key,
    #: not the thing a person recognises.
    display_labels: Callable[[], dict[str, str]] | None = None
    #: Per-account timeline marks (boundary/probed/covered, iso dates) for
    #: the holdings strips - the axis and segments render in the page, the
    #: dates come from the store.
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None
    #: File imports from the page: preview parses WITHOUT landing (a wrong
    #: file inspected costs nothing); confirm lands through the same
    #: import_file as the CLI, so the two routes cannot drift.
    preview_upload: Callable[..., dict[str, object]] | None = None
    confirm_upload: Callable[..., str] | None = None
    #: The provider id an existing connection goes through, for pinning the
    #: bank picker on reconnects - the wrong bank should not be one tap away.
    pinned_providers: Callable[[str], str | None] | None = None
    #: Queue a push to Actual (returns a summary line) and read the latest
    #: applier results - the file-queue boundary rendered as a button and a
    #: status list, so the budget sync needs neither a shell nor ansible.
    push_actual: Callable[[], str] | None = None
    actual_status: Callable[[], list[dict[str, object]]] | None = None
    #: Per-account sync fates for the roster: syncing / provision / unnamed.
    actual_roster: Callable[[], list[dict[str, object]]] | None = None

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


def _short_ref(ref: str) -> str:
    """Account references compactly: opaque ids earn eight characters.

    The full id is provenance and lives in the store; on a phone-width page
    it is thirty-two characters of noise between the reader and the answer.
    Bound accounts (human names) pass through untouched.
    """
    bare = ref.removeprefix("truelayer:")
    if bare != ref and len(bare) > 12:
        return f"{bare[:8]}..."
    return ref


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
        '<div class="scroll"><table><tr><th>field</th><th>present</th><th>types</th>'
        f"<th>values / shape</th></tr>{field_rows}</table></div>"
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
            '<div class="scroll"><table><tr><th>field</th><th>value</th><th>positive</th>'
            f"<th>negative</th><th>zero</th></tr>{rows}</table></div>"
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
        bars = "".join(
            '<div style="display:flex;align-items:center;gap:.5rem;margin:.15rem 0">'
            f'<span class="mono" style="flex:0 0 4.6rem">'
            f"{html.escape(str(m.get('month')))}</span>"
            '<div style="flex:1;background:#8882;border-radius:.25rem">'
            f'<div style="width:{max(2, round(int(str(m.get("count"))) * 100 / peak))}%;'
            'background:#2563eb;height:.8rem;border-radius:.25rem"></div></div>'
            f'<span class="muted" style="flex:0 0 2.6rem;text-align:right">'
            f"{m.get('count')}</span></div>"
            for m in by_month
            if isinstance(m, dict) and str(m.get("count")).isdigit()
        )
        parts.append(
            "<h2>Items per month</h2>"
            "<p>A month that should have data and shows no bar is a gap "
            "worth chasing.</p>" + bars
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


#: How each timeline state draws. Style IS meaning here: solid is held
#: history, faint is asked-and-empty (a dormant tail reads as a long pale
#: stretch), dotted amber is truncated-by-provider (the bank likely holds
#: more; the API refuses), dashed grey is simply never-asked.
_TIMELINE_STYLES = {
    "held": "background:#2563eb",
    "empty": "background:#2563eb40",
    "truncated": (
        "background:repeating-linear-gradient(90deg,#b45309aa 0 3px,"
        "transparent 3px 7px)"
    ),
    "unknown": (
        "background:repeating-linear-gradient(90deg,#8888 0 6px,"
        "transparent 6px 12px)"
    ),
    "future": "background:transparent",
}

_TIMELINE_TITLES = {
    "held": "held transactions",
    "empty": "asked, nothing there",
    "truncated": "before the provider's boundary - bank may hold more",
    "unknown": "never asked",
    "future": "future",
}


def timeline_segments(
    axis_start: date,
    axis_end: date,
    *,
    earliest: date | None,
    latest: date | None,
    today: date,
    boundary: date | None = None,
    probed: date | None = None,
    covered: date | None = None,
) -> list[tuple[str, float]]:
    """Classify the axis into (kind, width-percent) segments.

    Pure and interval-based: collect every meaningful date as a breakpoint,
    then classify each interval by its midpoint. Precedence: future beats
    everything right of today; held beats the rest inside the transaction
    span; asked-and-empty covers probed-before-earliest and the
    covered-after-latest dormancy tail; a known boundary marks everything
    before it truncated; what remains was never asked.
    """
    span = (axis_end - axis_start).days
    if span <= 0:
        return []
    points = {axis_start, axis_end}
    for mark in (boundary, probed, earliest, latest, covered, today):
        if mark is not None and axis_start < mark < axis_end:
            points.add(mark)
    ordered = sorted(points)

    def classify(midpoint: date) -> str:
        if midpoint > today:
            return "future"
        if earliest is not None and latest is not None and earliest <= midpoint <= latest:
            return "held"
        if (
            probed is not None
            and midpoint >= probed
            and (earliest is None or midpoint < earliest)
        ):
            return "empty"
        if (
            covered is not None
            and latest is not None
            and latest < midpoint <= covered
        ):
            return "empty"
        if boundary is not None and midpoint < boundary:
            return "truncated"
        return "unknown"

    segments: list[tuple[str, float]] = []
    for left, right in itertools.pairwise(ordered):
        midpoint = left + (right - left) / 2
        kind = classify(midpoint)
        width = (right - left).days * 100 / span
        if segments and segments[-1][0] == kind:
            segments[-1] = (kind, segments[-1][1] + width)
        else:
            segments.append((kind, width))
    return segments


def _timeline_strip(segments: list[tuple[str, float]]) -> str:
    if not segments:
        return ""
    parts = "".join(
        f'<span title="{html.escape(_TIMELINE_TITLES.get(kind, kind))}" '
        f'style="width:{width:.2f}%;{_TIMELINE_STYLES.get(kind, "")}"></span>'
        for kind, width in segments
    )
    return (
        '<div style="display:flex;height:6px;border-radius:3px;'
        f'overflow:hidden;background:#8881;margin:.35rem 0">{parts}</div>'
    )


def _holdings_rows(
    holdings: Callable[[], list[SourceCoverage]] | None,
    display_labels: Callable[[], dict[str, str]] | None = None,
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None,
) -> str:
    """What the store holds, per account and source - or nothing, quietly.

    Failure here must never take down the page that manages connections: the
    store may legitimately be mid-write during a backfill, which is exactly
    when someone is refreshing to see how it is going.

    Names lead and ids demote to small print, and a quiet account SAYS so:
    the date range matters most precisely when it is old, so the old case
    gets a chip instead of hiding in a run of text. Neutral, not red -
    dormancy is a fact about the account, not a fault in the fetching.
    """
    if holdings is None:
        return ""
    try:
        rows = holdings()
    except Exception:
        return ""
    if not rows:
        return ""
    labels: dict[str, str] = {}
    if display_labels is not None:
        try:
            labels = display_labels()
        except Exception:
            labels = {}
    marks: dict[str, dict[str, str]] = {}
    if account_timelines is not None:
        try:
            marks = account_timelines()
        except Exception:
            marks = {}

    def _mark(ref: str, key: str) -> date | None:
        value = marks.get(ref, {}).get(key)
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None

    today = datetime.now(UTC).date()
    # One axis for every account, so the bars are COMPARABLE: the left edge
    # is the oldest date any account knows anything about, the right edge is
    # the end of next month - scheduled payments can colonise that sliver
    # once they are stored; years of future would just be blank tape.
    starts = [
        candidate
        for row in rows
        for candidate in (
            _mark(row.account_id, "boundary"),
            _mark(row.account_id, "probed"),
            row.earliest,
        )
        if candidate is not None
    ]
    axis_start = min(starts) if starts else today
    axis_end = (today.replace(day=1) + timedelta(days=62)).replace(day=1)

    items = []
    # Living accounts lead; the archive sinks. Same information, but the
    # eye finds what changed this week without wading through 2022 first.
    for row in sorted(rows, key=lambda r: r.latest, reverse=True):
        label = labels.get(row.account_id)
        title = html.escape(label) if label else html.escape(row.account_id)
        sub = (
            f'<br><span class="muted mono">{html.escape(row.account_id)}</span>'
            if label
            else ""
        )
        quiet = ""
        if (today - row.latest).days > 365:
            quiet = (
                f' <span class="pill pill-quiet">quiet since '
                f"{row.latest.isoformat()}</span>"
            )
        dormant = (today - row.latest).days > 365
        strip = _timeline_strip(
            timeline_segments(
                axis_start,
                axis_end,
                earliest=row.earliest,
                latest=row.latest,
                today=today,
                boundary=_mark(row.account_id, "boundary"),
                probed=_mark(row.account_id, "probed"),
                covered=_mark(row.account_id, "covered"),
            )
        )
        row_style = ' style="opacity:.62"' if dormant else ""
        bind_form = ""
        if ":" in row.account_id:
            # A source-qualified id is an account nobody has NAMED - and
            # binding must not require the extend section (TrueLayer-only)
            # or a shell. The provider's display label above makes the row
            # recognisable; this form makes the name canonical.
            bind_form = (
                '<form method="post" action="/bind" '
                'style="display:flex;gap:.4rem;margin:.35rem 0">'
                f'<input type="hidden" name="account" '
                f'value="{html.escape(row.account_id)}">'
                '<input name="canonical" placeholder="name this account, '
                'e.g. starling-personal" style="flex:1">'
                '<button class="button" style="display:inline-block;'
                'padding:.5rem .8rem;border:0;cursor:pointer" '
                'type="submit">Bind</button></form>'
            )
        items.append(
            f'<div class="row"{row_style}><strong>'
            f'<a href="/account?ref={quote(row.account_id)}">'
            f"{title}</a></strong>"
            f" via {html.escape(row.source)}{quiet}{sub}<br>"
            f"{row.count:,} transactions, {row.earliest} .. <strong>{row.latest}</strong>"
            f"{bind_form}{strip}</div>"
        )
    # Accounts the store KNOWS about but holds nothing for must not vanish:
    # "this account exists, we asked back to 2020, nothing there" is a
    # finding, and silence would erase it. They render after the live rows,
    # strip and all - the strip is entirely faint/dotted, which is the point.
    held_refs = {row.account_id for row in rows}
    for ref, _entry in sorted(marks.items()):
        if ref in held_refs:
            continue
        label = labels.get(ref)
        title = html.escape(label) if label else html.escape(ref)
        sub = (
            f'<br><span class="muted mono">{html.escape(ref)}</span>'
            if label
            else ""
        )
        probed = _mark(ref, "probed")
        covered = _mark(ref, "covered")
        strip = _timeline_strip(
            timeline_segments(
                axis_start,
                axis_end,
                earliest=None,
                latest=None,
                today=today,
                boundary=_mark(ref, "boundary"),
                probed=probed,
                covered=covered,
            )
        )
        reach = (
            f"asked back to {probed.isoformat()}" if probed else "never asked"
        ) + (f", covered to {covered.isoformat()}" if covered else "")
        items.append(
            f'<div class="row" style="opacity:.62"><strong>{title}</strong>'
            f'{sub}<br><span class="muted">known account, nothing held yet - '
            f"{reach}</span>{strip}</div>"
        )

    legend = (
        '<p class="muted" style="font-size:.85em">timeline: solid = held, '
        "faint = asked and empty, dotted = truncated by the provider, "
        f"dashed = never asked; axis {axis_start.isoformat()} .. "
        f"{axis_end.isoformat()}</p>"
    )
    return "<h2>Held so far</h2>" + legend + "".join(items)


# 1 exists for the endgame: once +7 fails, the boundary is within a week,
# and finding it to the day takes single steps.
EXTEND_CHOICES = (1, 7, 30, 90, 365, 730)


def _freshness_line(account: ExtendableAccount) -> str:
    """One line saying how CURRENT the coverage is, loud when it is not.

    The scheduler covers to today on every six-hour cycle, so covered-to
    lagging more than two days behind means the pulls have quietly stopped -
    the exact failure a week away from the system would otherwise hide.
    """
    if account.covered_to is None:
        return ""
    lag = (datetime.now(UTC).date() - account.covered_to).days
    landed = account.last_landed[:16].replace("T", " ")
    stale = (
        f' <span class="pill pill-bad">stale: {lag} days behind</span>'
        if lag > 2
        else ""
    )
    return (
        f'<br><span class="muted">covered to {account.covered_to.isoformat()}'
        + (f", last landed {landed} UTC" if landed else "")
        + "</span>"
        + stale
    )


def _suggest_slug(label: str, ref: str) -> str:
    """A ready-to-accept canonical name from the provider's display label,
    so naming an account is one tap rather than a typing exercise. Empty
    when the label offers nothing better than the opaque ref."""
    source = ref.split(":", 1)[0] if ":" in ref else ""
    base = label.split("(", 1)[0].strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        return ""
    if source and source not in base:
        base = f"{source}-{base}"
    return base[:40]


def _roster_row(entry: dict[str, object]) -> str:
    label = html.escape(str(entry.get("label", "")))
    ref = str(entry.get("ref", ""))
    state = str(entry.get("state", ""))
    raw_count = entry.get("count", 0)
    count = raw_count if isinstance(raw_count, int) else 0
    held = f"{count:,} transaction(s)" if count else "no transactions yet"
    form = ""
    if state == "syncing":
        badge = '<span class="pill pill-ok">syncing</span>'
        note = held
    elif state == "provision":
        badge = '<span class="pill pill-quiet">creates on next push</span>'
        note = held + (
            "" if count else " - created empty in Actual, fills as data arrives"
        )
    else:
        badge = '<span class="pill pill-bad">not synced - needs a name</span>'
        note = f"{held} - name it and the next push takes it"
        suggestion = _suggest_slug(str(entry.get("label", "")), ref)
        form = (
            '<form method="post" action="/bind" '
            'style="display:flex;gap:.4rem;margin:.35rem 0">'
            f'<input type="hidden" name="account" value="{html.escape(ref)}">'
            f'<input name="canonical" value="{html.escape(suggestion)}" '
            'placeholder="e.g. starling-personal" style="flex:1">'
            '<button class="button" style="display:inline-block;'
            'padding:.5rem .8rem;border:0;cursor:pointer" '
            'type="submit">Bind</button></form>'
        )
    return (
        f'<div class="row"><strong>{label}</strong> {badge}'
        f'<br><span class="muted">{note}</span>{form}</div>'
    )


def _actual_rows(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    push_available: bool,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
) -> str:
    """The budget sync, visible and pressable: the per-account plan first
    (what a push would do and why), then the button, then results newest
    first."""
    if actual_status is None and not push_available:
        return ""
    roster_html = ""
    if actual_roster is not None:
        try:
            roster = actual_roster()
        except Exception:
            roster = []
        if roster:
            roster_html = "".join(_roster_row(entry) for entry in roster)
    results = []
    if actual_status is not None:
        try:
            results = actual_status()
        except Exception:
            results = []
    rows = []
    for result in results:
        ok = bool(result.get("ok"))
        badge = (
            '<span class="pill pill-ok">applied</span>'
            if ok
            else '<span class="pill pill-bad">failed</span>'
        )
        detail = (
            f"{result.get('added', 0)} added, "
            f"{result.get('provisioned', 0)} account(s) provisioned"
            if ok
            else html.escape(str(result.get("error", "")))
        )
        stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
        rows.append(
            f'<div class="row"><strong>{stamp}</strong> {badge}'
            f'<br><span class="muted">{detail}</span></div>'
        )
    button = (
        '<form method="post" action="/push-actual">'
        '<p><button class="button" type="submit" '
        'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
        "Push to Actual now</button></p></form>"
        if push_available
        else ""
    )
    return (
        "<h2>Actual sync</h2>"
        "<p>Pushes run through the applier container: bound accounts import, "
        "named accounts are created in Actual automatically (empty ones "
        "included) and their transactions ride the next push. Scheduled "
        "cycles queue one after every pull.</p>"
        + roster_html
        + button
        + "".join(rows)
    )


def _knowledge_rows(
    provider_knowledge: Callable[[], list[dict[str, object]]] | None,
) -> str:
    """What the pulls have learnt, per connection - shown where decisions
    get made. Fact keys are translated for reading; unknown keys pass
    through raw rather than being hidden."""
    if provider_knowledge is None:
        return ""
    try:
        facts = provider_knowledge()
    except Exception:
        return ""
    if not facts:
        return ""
    lines = []
    for row in facts:
        fact = str(row.get("fact", ""))
        value = html.escape(str(row.get("value", "")))
        connection = html.escape(str(row.get("connection_id", "")))
        if fact == "reconnect_drift":
            lines.append(
                f'<li class="bad"><strong>{connection}</strong> - reconnect '
                f"drift: {value} "
                f'<span class="muted">'
                f'({html.escape(str(row.get("observed_at", ""))[:10])})</span></li>'
            )
            continue
        if fact == "accepted_backfill_days":
            text = f"accepted backfill window: {value} days"
        elif fact == "sca_window_minutes":
            text = f"deep-history window after authentication: {value} min"
        elif fact.startswith("history_boundary:"):
            ref = fact.split(":", 1)[1]
            short = f"{ref.removeprefix('truelayer:')[:8]}..."
            text = f"history boundary ({html.escape(short)}): {value}"
        else:
            text = f"{html.escape(fact)}: {value}"
        lines.append(
            f'<li><strong>{connection}</strong> - {text} '
            f'<span class="muted">'
            f'({html.escape(str(row.get("observed_at", ""))[:10])})</span></li>'
        )
    return (
        "<h2>What the pulls have learnt</h2>"
        "<p>Per connection, from real refusals and grants - a stricter bank "
        "shows up here as different numbers.</p>"
        f"<ul>{''.join(lines)}</ul>"
    )


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
        bind_form = ""
        if account.unbound:
            bind_form = (
                '<form method="post" action="/bind" '
                'style="display:flex;gap:.4rem;margin:.4rem 0">'
                f'<input type="hidden" name="account" '
                f'value="{html.escape(account.provider_ref)}">'
                '<input name="canonical" placeholder="name this account, '
                'e.g. halifax-current" style="flex:1">'
                '<button class="button" style="display:inline-block;'
                'padding:.5rem .8rem;border:0;cursor:pointer" '
                "type=\"submit\">Bind</button></form>"
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
            + _freshness_line(account)
            + f"{note}{bind_form}<br>{controls}</div>"
        )
    return (
        "<h2>Extend history</h2>"
        "<p>Each press fetches one further window, attended - you are the "
        "customer, actively requesting.</p>" + "".join(rows)
    )


def _starling_row(
    starling_status: Callable[[], dict[str, object] | None] | None,
) -> str:
    if starling_status is None:
        return ""
    try:
        status = starling_status()
    except Exception:
        return ""
    if not status:
        return ""
    raw_accounts = status.get("accounts")
    accounts = raw_accounts if isinstance(raw_accounts, list) else []
    listed = "".join(
        f'<br><span class="muted">{html.escape(str(a.get("name", "account")))} '
        f"({html.escape(str(a.get('accountUid', ''))[:8])}...)</span>"
        for a in accounts
        if isinstance(a, dict)
    )
    return (
        '<div class="row"><strong>starling</strong><br>'
        '<span class="ok">first-party token - no consent clock, no expiry '
        "chore</span>" + listed + "</div>"
    )


def render_index(
    store: ConnectionStore,
    holdings: Callable[[], list[SourceCoverage]] | None = None,
    provider_knowledge: Callable[[], list[dict[str, object]]] | None = None,
    extendables: Callable[[], list[ExtendableAccount]] | None = None,
    starling_status: Callable[[], dict[str, object] | None] | None = None,
    display_labels: Callable[[], dict[str, str]] | None = None,
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None,
    push_actual: Callable[[], str] | None = None,
    actual_status: Callable[[], list[dict[str, object]]] | None = None,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
) -> bytes:
    body = f"""
{_credential_banner()}
{_connection_rows(store)}
{_starling_row(starling_status)}
{_holdings_rows(holdings, display_labels, account_timelines)}
{_knowledge_rows(provider_knowledge)}
{_actual_rows(actual_status, push_actual is not None, actual_roster)}
{_extend_rows(extendables)}
<p><a class="button" href="/artefacts">Browse raw artefacts</a></p>
<p><a class="button" href="/attempts">Fetch attempts</a></p>
<h2>Import a file</h2>
<p>Bank CSV or QIF exports - previewed before anything is stored, then
reconciled through the same identity rules as the API pulls.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
  <p><input type="file" name="statement" required></p>
  <p><button class="button" type="submit"
     style="border:0;width:100%;font-size:inherit;cursor:pointer">Preview import</button></p>
</form>
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
                    provider_knowledge=self.bound_config.provider_knowledge,
                    extendables=self.bound_config.extendables,
                    starling_status=self.bound_config.starling_status,
                    display_labels=self.bound_config.display_labels,
                    account_timelines=self.bound_config.account_timelines,
                    push_actual=self.bound_config.push_actual,
                    actual_status=self.bound_config.actual_status,
                    actual_roster=self.bound_config.actual_roster,
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
            f'<td>{html.escape(_short_ref(str(r.get("account_ref", ""))))}</td>'
            f'<td>{r.get("count")}</td></tr>'
            for r in (raw_day if isinstance(raw_day, list) else [])
            if isinstance(r, dict)
        )
        raw_rows = ledger.get("rows")
        rows = "".join(
            f'<div class="row"><strong>'
            f'{html.escape(str(r.get("attempted_at", ""))[:19].replace("T", " "))}'
            "</strong> "
            + (
                f'<span class="pill pill-bad">refused {r.get("http_status")} '
                f'{html.escape(str(r.get("error_code", "")))}</span>'
                if r.get("outcome") == "refused"
                else f'<span class="pill pill-ok">'
                f'{html.escape(str(r.get("outcome", "")))}</span>'
            )
            + f'<br><span class="muted">'
            f'{html.escape(_short_ref(str(r.get("account_ref", ""))))} - '
            f'{html.escape(str(r.get("source", "")).removeprefix("truelayer-"))} - '
            f"{html.escape(_trigger_of(r.get('request_meta')))}</span>"
            f'<br><span class="mono">{html.escape(str(r.get("asked", "")))}</span>'
            + (
                '<details><summary class="muted">provider detail</summary>'
                f'<span class="mono">{html.escape(str(r.get("detail", "")))}</span>'
                "</details>"
                if r.get("outcome") == "refused" and r.get("detail")
                else ""
            )
            + (
                f' <a href="/artefact?id={r.get("artefact_id")}">view artefact</a>'
                if r.get("artefact_id")
                else ""
            )
            + "</div>"
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
            + "<h2>Attempts (UTC)</h2>"
            + (
                rows
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
        label = ""
        if self.bound_config.display_labels is not None:
            try:
                label = self.bound_config.display_labels().get(ref, "")
            except Exception:
                label = ""
        raw_details = shape.get("details")
        details = raw_details if isinstance(raw_details, dict) else {}
        details_html = ""
        if details:
            details_html = (
                f'<br><span class="muted">'
                f'{html.escape(str(details.get("display_name", "")))} '
                f'({html.escape(str(details.get("account_type", "")))}) '
                f'via {html.escape(str(details.get("connection", "")))}</span>'
            )
        heading = html.escape(label) if label else html.escape(ref)
        id_line = (
            f'<br><span class="muted mono">{html.escape(ref)}</span>' if label else ""
        )
        body = (
            f"<p><strong>{heading}</strong>{id_line}{details_html}<br>"
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
        # A reconnect pins the picker to the bank this connection already
        # goes through - the wrong bank must not be one tap away. New
        # connections get the full picker.
        pinned = None
        if self.bound_config.pinned_providers is not None:
            try:
                pinned = self.bound_config.pinned_providers(name)
            except Exception:
                pinned = None
        if pinned:
            link = build_auth_link(
                client_id=self.bound_config.client_id,
                redirect_uri=self.bound_config.redirect_uri,
                state=state,
                providers=pinned,
            )
        else:
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
        if route == "/upload":
            self._upload()
            return
        if route == "/upload-confirm":
            self._upload_confirm(parse_qs(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)
            ).decode("utf-8")))
            return
        if route == "/push-actual":
            self._push_actual()
            return
        if route == "/bind":
            self._bind(parse_qs(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)
            ).decode("utf-8")))
            return
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

    uploads: UploadSession = UploadSession()

    def _upload(self) -> None:
        """Receive a file, preview it, commit NOTHING yet."""
        hook = self.bound_config.preview_upload
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Uploads are not wired.</p>"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 5 * 1024 * 1024:
            self._respond(
                413,
                error_page("Too large", "<p>Bank exports are small; this is not one.</p>"),
            )
            return
        try:
            payload, filename = _parse_multipart(
                self.headers.get("Content-Type") or "", self.rfile.read(length)
            )
            preview = hook(payload, filename)
        except Exception as exc:
            self._respond(
                400, error_page("Could not read the file", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        token = self.uploads.stash(payload, filename)
        labels: dict[str, str] = {}
        if self.bound_config.display_labels is not None:
            try:
                labels = self.bound_config.display_labels()
            except Exception:
                labels = {}
        options = "".join(
            f'<option value="{html.escape(ref)}">{html.escape(name)}</option>'
            for ref, name in sorted(labels.items(), key=lambda kv: kv[1])
        )
        raw_sample = preview.get("sample")
        sample_rows = "".join(
            f'<tr><td>{html.escape(str(r.get("date")))}</td>'
            f'<td style="text-align:right">{html.escape(str(r.get("amount")))}</td>'
            f'<td>{html.escape(str(r.get("description")))}</td></tr>'
            for r in (raw_sample if isinstance(raw_sample, list) else [])
            if isinstance(r, dict)
        )
        warning = (
            '<p class="warn">Every date in this file falls on the 12th or '
            "earlier, so nothing rules out the opposite day/month reading. "
            "Cross-check against another source after importing.</p>"
            if preview.get("date_ambiguous")
            else ""
        )
        body = (
            f"<p><strong>{html.escape(filename)}</strong> parsed as "
            f"{html.escape(str(preview.get('parser')))} "
            f"(dates {html.escape(str(preview.get('date_format')))}): "
            f"{preview.get('rows')} row(s), "
            f"{preview.get('earliest')} .. {preview.get('latest')}</p>"
            + warning
            + '<div class="scroll"><table><tr><th>date</th><th>amount</th>'
            f"<th>description</th></tr>{sample_rows}</table></div>"
            "<p>Nothing has been stored yet. Choose the account these "
            "transactions belong to, then confirm.</p>"
            '<form method="post" action="/upload-confirm">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<p><select name="account" style="width:100%;padding:.6rem" required>'
            f'<option value="">choose an account...</option>{options}</select></p>'
            '<p><input name="account_other" placeholder="or type a canonical '
            'name, e.g. hsbc-old-current"></p>'
            '<p><button class="button" type="submit" '
            'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
            "Import into the chosen account</button></p></form>" + HOME_LINK
        )
        self._respond(200, render_page("Preview import", body))

    def _upload_confirm(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.confirm_upload
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Uploads are not wired.</p>"))
            return
        token = (form.get("token", [""])[0] or "").strip()
        account = (
            (form.get("account_other", [""])[0] or "").strip()
            or (form.get("account", [""])[0] or "").strip()
        )
        if not token or not account:
            self._respond(400, error_page("Bad request", "<p>Token and account required.</p>"))
            return
        try:
            payload, filename = self.uploads.claim(token)
        except KeyError as exc:
            self._respond(410, error_page("Upload expired", f"<p>{html.escape(str(exc))}</p>"))
            return
        try:
            summary = hook(payload, filename, account)
        except Exception as exc:
            self._respond(
                500, error_page("Import failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        print(f"web import: {filename} -> {account}", file=sys.stderr)
        self._respond(
            200,
            render_page("Imported", f"<p>{html.escape(summary)}</p>" + HOME_LINK),
        )

    def _push_actual(self) -> None:
        hook = self.bound_config.push_actual
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Push is not wired.</p>"))
            return
        try:
            summary = hook()
        except Exception as exc:
            self._respond(500, error_page("Could not queue", f"<p>{html.escape(str(exc))}</p>"))
            return
        print(f"actual push queued via page: {summary}", file=sys.stderr)
        self._respond(
            200,
            render_page(
                "Push queued",
                f"<p>{html.escape(summary)}</p>"
                "<p>The applier container picks requests up within its poll "
                "interval; results appear in the Actual sync section on the "
                "home page.</p>" + HOME_LINK,
            ),
        )

    def _bind(self, form: dict[str, list[str]]) -> None:
        """Name an account from the page: the map entry plus label moves.

        Mutating, so POST only - and the result page repeats the extend rows
        so the newly named account is immediately visible under its name.
        """
        hook = self.bound_config.bind_account
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Binding is not wired.</p>"))
            return
        account = (form.get("account", [""])[0] or "").strip()
        canonical = (form.get("canonical", [""])[0] or "").strip()
        if not account or not canonical:
            self._respond(
                400, error_page("Bad request", "<p>Account and name required.</p>")
            )
            return
        try:
            summary = hook(account, canonical)
        except Exception as exc:
            self._respond(
                400,
                error_page("Could not bind", f"<p>{html.escape(str(exc))}</p>"),
            )
            return
        print(f"bound via page: {account} -> {canonical}", file=sys.stderr)
        self._respond(
            200,
            render_page(
                "Account bound",
                f"<p>{html.escape(summary)}</p>"
                + _extend_rows(self.bound_config.extendables)
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
