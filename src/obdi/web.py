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

import contextlib
import html
import itertools
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from typing import ParamSpec, TypeVar
from urllib.parse import ParseResult, parse_qs, quote, urlparse

from .callback import render_page
from .classification import redact_summary
from .connections import ConnectionStore, build_connection
from .coverage import SourceCoverage
from .doctor import shape_problems
from .namespaces import validate_connection_name
from .providers.truelayer import build_auth_link, exchange_code
from .secrets import SecretError, read_secret
from .statement_shape import ShapeReport


def _report_slow_route(method: str, route: str, seconds: float) -> None:
    """Any route slower than the threshold names itself in the log.

    Route-level for EVERY page, hook-level only on the index: the 40s
    index taught that a slow page invisible outside its own render is
    diagnosed by guesswork, but blanket fine-grained instrumentation is
    noise - one line per slow request is the proportionate net. The
    index additionally prints its per-hook breakdown, so a slow / gets
    two lines that agree.
    """
    threshold = float(os.environ.get("OBDI_WEB_SLOW_RENDER_SECS", "2.0"))
    if seconds >= threshold:
        print(f"web timing: {method} {route} took {seconds:.2f}s", flush=True)


_HookParams = ParamSpec("_HookParams")
_HookReturn = TypeVar("_HookReturn")

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

    def peek(self, state: str) -> str:
        """The connection name behind a state, WITHOUT consuming it.

        A refused authorisation should still be attributable to the
        connection it was for, but reading that name must not spend the
        state: the person may retry the same link, and a consumed state
        would then be refused as forgery.
        """
        pending = self._pending.get(state)
        return pending.connection_name if pending is not None else ""

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
        self._pending: dict[str, tuple[bytes, str, datetime, bool]] = {}

    def stash(self, payload: bytes, filename: str, *, doubted: bool = False) -> str:
        """`doubted` rides the stash so the CONFIRM can enforce the
        wrong-destination override server-side - a checkbox the browser
        merely requires is decoration, and the misfile this guards against
        happened on a phone at 22:51."""
        now = datetime.now(UTC)
        expired = [
            token
            for token, (_, _, created, _) in self._pending.items()
            if now - created > self.LIFETIME
        ]
        for token in expired:
            del self._pending[token]
        token = token_urlsafe(16)
        self._pending[token] = (payload, filename, now, doubted)
        return token

    def claim(self, token: str) -> tuple[bytes, str, bool]:
        payload, filename, created, doubted = self._pending.pop(token)
        if datetime.now(UTC) - created > self.LIFETIME:
            raise KeyError("upload expired - upload the file again")
        return payload, filename, doubted


def _parse_multipart_files(
    content_type: str, body: bytes
) -> tuple[list[tuple[bytes, str]], dict[str, str]]:
    """EVERY file in a multipart form, plus the text fields.

    Minimal on purpose: our own form, not a general parser. Parts carrying
    a filename are files, in the order the browser sent them; parts with a
    plain name are fields.

    All of them rather than the first, because keeping statements is a PURE
    upload step - no destination to choose, nothing imported, no preview to
    verify against - so a dozen documents at once carries none of the risk
    that keeps the import flow to one file at a time.
    """
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("not a multipart upload")
    boundary = content_type.split(marker, 1)[1].split(";")[0].strip().strip('"')
    delimiter = b"--" + boundary.encode()
    files: list[tuple[bytes, str]] = []
    fields: dict[str, str] = {}
    for part in body.split(delimiter):
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers = part[:header_end].decode("utf-8", "replace")
        content = part[header_end + 4 :]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        if "filename=" in headers:
            filename = (
                headers.split("filename=", 1)[1].split("\r\n")[0].strip().strip('"')
            )
            # A file input with nothing chosen still sends an empty part.
            if filename or content:
                files.append((content, filename))
        elif 'name="' in headers:
            name = headers.split('name="', 1)[1].split('"')[0]
            fields[name] = content.decode("utf-8", "replace").strip()
    return files, fields


def _parse_multipart(
    content_type: str, body: bytes
) -> tuple[bytes, str, dict[str, str]]:
    """The FIRST file and the text fields.

    The shape the import flow wants: it takes one file at a time so the
    preview can verify it against the destination chosen beforehand.
    """
    files, fields = _parse_multipart_files(content_type, body)
    if not files:
        raise ValueError("no file found in the upload")
    payload, filename = files[0]
    return payload, filename or "upload.csv", fields


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
    #: Cross-witness staleness warnings (already described, evidence and all):
    #: a scheduled feed proven behind by another witness's newer rows for the
    #: same account. Rendered above the holdings so a stuck feed cannot hide
    #: behind a quiet-looking coverage bar.
    feed_warnings: Callable[[], list[str]] | None = None
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
    #: Keep an uploaded statement as evidence before an account is
    #: chosen - the exports worth keeping most are the ones that
    #: cannot be fetched twice.
    keep_statement: Callable[[bytes, str], int] | None = None
    #: A kept statement's filename and bytes, by artefact id.
    statement_payload: Callable[[int], tuple[str, bytes] | None] | None = None
    artefact_detail: Callable[..., dict[str, object] | None] | None = None
    #: Correct one artefact's landed account (the mis-tapped destination
    #: remedy). Takes (artefact_id, new_account_ref); returns the old ref,
    #: or None for an unknown artefact.
    refile_artefact: Callable[[int, str], str | None] | None = None
    #: The fetch-attempt ledger: every ask made of a provider, refused or
    #: landed, plus per-account call counts over the last day. The probing
    #: workflow is press, read, decide - and deciding needs this without a
    #: shell.
    attempts_index: Callable[[], dict[str, object]] | None = None
    #: The standing cross-source review: per-account agreement outlines,
    #: contradicted missing months, and transposition alarms - the
    #: import-page verdicts, browsable after a bulk import session.
    agreement_report: Callable[[], dict[str, object]] | None = None
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
    #: Returns either a plain summary string, or a dict with "summary" and
    #: structured "agreements" (Agreement.outline() entries) - the page
    #: renders whichever shape it is handed.
    confirm_upload: Callable[..., str | dict[str, object]] | None = None
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
    #: Envelopes queued but not yet picked up by the applier.
    actual_queue: Callable[[], list[dict[str, object]]] | None = None
    #: Queue a read-only audit: the applier reads Actual back and reports.
    audit_actual: Callable[[], str] | None = None
    #: The full sync history - every result, not just the newest handful.
    actual_history: Callable[[], list[dict[str, object]]] | None = None
    #: The audit's action arm: delete provably-ours orphaned imports.
    prune_actual: Callable[[], str] | None = None
    #: The review queue decomposed: reasons, clusters, declaration matches.
    review_report_text: Callable[[], str] | None = None
    #: The uncategorised worklist as data: coverage, then groups with the
    #: evidence needed to judge them (a real example, how many distinct
    #: strings, whether it is a reference code rather than a payee).
    categorise_overview: Callable[[], dict[str, object]] | None = None
    #: Answer a whole group at human rank. Separate from the overview so a
    #: read cannot write, and a test can exercise either alone.
    categorise_apply: Callable[[str, str, str], int] | None = None
    #: Record that a group was looked at and left unanswered - an outcome in
    #: its own right, since a queue emptied only by answering is a queue
    #: emptied by guessing.
    categorise_defer: Callable[[str], int] | None = None
    #: Settlement-lag measurement from the starling truth set.
    date_lag_text: Callable[[], str] | None = None
    #: Balance-walk integrity: bank running balances vs held transactions.
    balance_walk_text: Callable[[], str] | None = None
    #: Move a connection's name everywhere it was recorded.
    rename_connection: Callable[[str, str], str] | None = None
    #: Land a refused authorisation in the attempt ledger.
    record_auth_failure: Callable[[str, str, str], None] | None = None
    #: Additively replay one landed artefact into the store (see the cli).
    replay_artefact: Callable[[int], str] | None = None
    #: When the applier last checked the queue (ISO stamp, empty if never).
    actual_heartbeat: Callable[[], str] | None = None
    #: Danger zone: wipe and replay the derived layers from raw artefacts.
    #: Returns immediately - the work runs in the background and its state
    #: is read back via rebuild_status.
    rebuild_derived: Callable[[], str] | None = None
    rebuild_status: Callable[[], dict[str, object]] | None = None
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None = None
    #: (account, source) -> connection names, for the roster's via-labels.
    source_connections: Callable[[], dict[tuple[str, str], list[str]]] | None = None
    #: Raw attempt rows for the fetch timeline - more history than the
    #: ledger page shows, projected into bars by obdi.timeline.
    recent_attempts: Callable[[], list[dict[str, object]]] | None = None
    #: Run the Starling changesSince probe at a cutoff; None when the web
    #: process has no Starling token. Returns the rendered-ready report.
    starling_probe: Callable[[str], object] | None = None
    #: Cutoff suggestions derived from the store's known amendments.
    probe_suggestions: Callable[[], list[object]] | None = None
    #: Danger zone: drop the canonical-to-Actual links (source names kept).
    forget_actual: Callable[[], int] | None = None
    #: True while a stack update holds its lease - new bank authorisations
    #: are deferred rather than risked mid-SCA (that window is five minutes
    #: and does not come back).
    update_in_progress: Callable[[], bool] | None = None
    #: Taken while an authorisation is in flight, released on callback -
    #: tells the updater a person is mid-SCA.
    auth_lease_take: Callable[[], None] | None = None
    auth_lease_release: Callable[[], None] | None = None
    #: The scheduler's cycle heartbeat: {"at": iso, "interval_seconds": n}.
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None
    #: The post-auth backfill-and-ladder thread's progress.
    backfill_status: Callable[[], dict[str, object]] | None = None
    #: canonical -> provider refs bound to it; the map's edges, readable.
    account_feeders: Callable[[], dict[str, list[str]]] | None = None

    def current_client_secret(self) -> str:
        value = self.client_secret
        return value() if callable(value) else value


def _connection_rows(store: ConnectionStore, rename_available: bool = False) -> str:
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
        rename = (
            '<form method="post" action="/rename-connection" '
            'style="margin:.4rem 0 0">'
            f'<input type="hidden" name="old_name" value="{display}">'
            '<label style="display:block;font-size:.85rem" class="muted">'
            "Rename this connection (the name is obdi's label, not the "
            "bank's - it moves everywhere at once)"
            f'<input name="new_name" value="{display}" '
            'style="width:100%;font-size:1rem;min-height:44px;box-sizing:border-box">'
            "</label>"
            '<button class="button" type="submit" style="border:0;width:100%;'
            'min-height:44px;font-size:inherit;cursor:pointer;'
            'background:#8882;color:inherit">Rename</button></form>'
            if rename_available
            else ""
        )
        rows.append(
            f'<div class="row"><strong>{display}</strong><br>{state}'
            f'<br><a class="button" href="/connect?name={target}">Reconnect {display}</a>'
            f"{rename}</div>"
        )
    return "".join(rows)


HOME_LINK = '<p><a class="button" href="/">Back to connections</a></p>'

#: What the provider's own OAuth codes mean, in words. Deliberately
#: describes the CLASS of cause rather than asserting which one occurred -
#: the provider sent a code, not a diagnosis, and inventing certainty here
#: would be the same fault as a bare code, dressed better.
_AUTH_ERROR_READINGS = {
    "access_denied": (
        "The request was declined at the bank - either by you, or by the bank "
        "on your behalf. Nothing was created."
    ),
    "provider_error": (
        "The bank refused the request itself. The usual causes are a bank "
        "relationship with nothing it is willing to share through an "
        "aggregator (a mortgage-only or product-only login is the common "
        "one), or a fault on the bank's side. Nothing was created."
    ),
    "temporarily_unavailable": (
        "The bank or the aggregator was temporarily unavailable. Trying again "
        "later is reasonable; nothing was created."
    ),
    "server_error": (
        "The aggregator reported an internal error. Nothing was created."
    ),
    "invalid_request": (
        "The request obdi built was rejected as malformed. That points at "
        "obdi's configuration rather than at the bank."
    ),
    "invalid_scope": (
        "The permissions obdi asked for were rejected. That points at obdi's "
        "configuration rather than at the bank."
    ),
    "unauthorized_client": (
        "The aggregator did not accept obdi's client credentials. Check the "
        "client id and secret before retrying."
    ),
}


def _auth_failure_body(name: str, code: str, described: str) -> str:
    """The refusal in parts: who it was for, what the provider said, what
    that class of code means, and what state now exists (none)."""
    lines = []
    if name:
        lines.append(
            f"<p>The authorisation for <strong>{html.escape(name)}</strong> "
            "did not complete.</p>"
        )
    else:
        lines.append("<p>An authorisation did not complete.</p>")
    lines.append(
        f'<p class="muted">provider code: <code>{html.escape(code)}</code></p>'
    )
    if described:
        lines.append(
            f'<p class="muted">provider said: {html.escape(described)}</p>'
        )
    else:
        lines.append(
            '<p class="muted">The provider sent no description with it.</p>'
        )
    reading = _AUTH_ERROR_READINGS.get(code.strip().lower())
    if reading:
        lines.append(f"<p>{reading}</p>")
    lines.append(
        "<p>No connection was created and no data was fetched - the refusal "
        "came before any account list was seen. The attempt is recorded in "
        'the <a href="/attempts">fetch attempts</a> ledger, so this answer '
        "survives the page.</p>"
    )
    return "".join(lines) + HOME_LINK


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
    caveat = field.get("caveat")
    warning = (
        f'<br><span class="warn">the provider documents this field as: '
        f"{html.escape(str(caveat))}</span>"
        if caveat
        else ""
    )
    note = field.get("note")
    values = field.get("values")
    if note and not values:
        # "no value" and "withheld" are different facts about a payload,
        # and a reader deserves to be told which one they are looking at.
        shape = []
        length = field.get("length")
        if isinstance(length, dict):
            shape.append(f"length {length.get('min')}-{length.get('max')}")
        if field.get("format"):
            shape.append(html.escape(str(field.get("format"))))
        if field.get("distinct"):
            shape.append(f"{field.get('distinct')} distinct")
        rendered = ", ".join(shape)
        return (
            f'<span class="muted">{html.escape(str(note))}</span>'
            + (f"<br>{rendered}" if rendered else "")
            + warning
        )
    if isinstance(values, list) and values:
        listed = ", ".join(
            f"{html.escape(str(v.get('value')))} x{v.get('count')}"
            for v in values
            if isinstance(v, dict)
        )
        return f"{field.get('distinct')} distinct: {listed}{warning}"
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


def _breakdown_html(breakdown: dict[str, object]) -> str:
    """Which feeders made this account, and how much each contributed.

    Transactions and sightings are stated separately and always: they are
    equal until a payment is seen twice, and the moment they diverge is
    exactly the moment the difference matters. Corroboration is the point
    of running more than one pipe, so it gets its own line rather than
    being inferred from the arithmetic.
    """
    feeders = breakdown.get("by_feeder")
    rows = [f for f in feeders if isinstance(f, dict)] if isinstance(feeders, list) else []
    if not rows:
        return ""
    transactions = int(str(breakdown.get("transactions", 0) or 0))
    sightings = int(str(breakdown.get("sightings", 0) or 0))
    corroborated = int(str(breakdown.get("corroborated", 0) or 0))
    single = int(str(breakdown.get("single_source", 0) or 0))
    sources = breakdown.get("sources")
    source_count = len(sources) if isinstance(sources, list) else 0

    lines = [
        "<h2>Where these rows came from</h2>",
        f"<p>{transactions:,} transaction(s), {sightings:,} sighting(s), "
        f"{source_count} source(s). A transaction seen by two pipes is one "
        "transaction and two sightings.</p>",
        '<div class="row">',
    ]
    for entry in sorted(
        rows, key=lambda e: -int(str(e.get("transactions", 0) or 0))
    ):
        label = html.escape(str(entry.get("label") or entry.get("feeder") or ""))
        source = html.escape(str(entry.get("source", "")))
        count = int(str(entry.get("transactions", 0) or 0))
        raw_connections = entry.get("connections")
        connections = (
            [str(c) for c in raw_connections]
            if isinstance(raw_connections, list) and raw_connections
            else []
        )
        via = (
            f' <span class="muted">via {html.escape(", ".join(connections))}</span>'
            if connections
            else ""
        )
        lines.append(
            f'<span class="muted">{source}</span> {label}: '
            f"<strong>{count:,}</strong> transaction(s){via}<br>"
        )
    lines.append("</div>")
    if source_count > 1:
        lines.append(
            f'<p><span class="ok">{corroborated:,} transaction(s) corroborated '
            f"by two or more sources</span>; {single:,} seen by one source only. "
            "A row only one pipe has seen is either a gap in the others or a "
            "disagreement worth reading.</p>"
        )
    else:
        lines.append(
            '<p class="muted">One source, so nothing is corroborated yet - '
            "every row here rests on a single pipe's word.</p>"
        )
    return "".join(lines)


def _shape_html(summary: dict[str, object]) -> str:
    """The computed-shape block, shared by the artefact and account pages.

    Every field passes the disclosure allowlist on the way out. The shape
    - nesting, types, presence, formats, cardinality - is untouched,
    because that is what this block is FOR; only example values are
    gated, and a field nobody has classified is withheld rather than
    shown.
    """
    summary = redact_summary(summary)
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
    withheld = int(str(summary.get("withheld_fields", 0) or 0))
    unclassified = int(str(summary.get("unclassified_fields", 0) or 0))
    disclosure = ""
    if withheld:
        disclosure = (
            f'<p class="muted">{withheld} field(s) show shape without values'
            + (
                f", {unclassified} because nothing has classified them yet"
                if unclassified
                else ""
            )
            + ". The payload itself is unchanged - this is what the page "
            "chooses to render, not what was stored.</p>"
        )
    return (
        f'<p>{summary.get("items", 0)} item(s), {summary.get("bytes", 0):,} bytes</p>'
        f"{disclosure}"
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


def _agreements_html(entries: object) -> str:
    """Render agreement outlines as per-source ledgers.

    Accepts Agreement.outline() dicts, and falls back to a plain paragraph
    for string entries (the no-other-source message, older callers). Each
    side renders as its own heading with one bucket per line, so the counts
    visibly sum to the side's total and every line names whose rows it
    counts - the two questions a flat prose line made the reader
    reconstruct forensically.
    """
    if not isinstance(entries, list):
        return ""
    parts: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            parts.append(f"<p>{html.escape(entry)}</p>")
            continue
        if not isinstance(entry, dict):
            continue
        warn = ' class="warn"' if entry.get("warn") else ""
        parts.append(
            f"<p><strong>{html.escape(str(entry.get('sources')))}</strong> "
            f"[{html.escape(str(entry.get('window')))}]: "
            f"<strong{warn}>{html.escape(str(entry.get('verdict')))}</strong><br>"
            f'<span class="muted">{html.escape(str(entry.get("figures")))}</span></p>'
        )
        raw_sides = entry.get("sides")
        if isinstance(raw_sides, list) and raw_sides:
            side_html = ""
            for side in raw_sides:
                if not isinstance(side, dict):
                    continue
                bucket_html = ""
                raw_buckets = side.get("buckets")
                for bucket in raw_buckets if isinstance(raw_buckets, list) else []:
                    if not isinstance(bucket, dict):
                        continue
                    label = html.escape(str(bucket.get("label")))
                    raw_items = bucket.get("items")
                    inner = ""
                    if isinstance(raw_items, list) and raw_items:
                        inner = (
                            "<ul>"
                            + "".join(
                                f"<li>{html.escape(str(item))}</li>"
                                for item in raw_items
                            )
                            + "</ul>"
                        )
                    bucket_html += f"<li>{label}{inner}</li>"
                side_html += (
                    f"<li><strong>{html.escape(str(side.get('heading')))}</strong>"
                    f"<ul>{bucket_html}</ul></li>"
                )
            parts.append(f"<ul>{side_html}</ul>")
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


def _feeder_line(
    account_id: str, feeders: dict[str, list[str]]
) -> str:
    """Which provider refs the map binds to this canonical account.

    Shown so a mis-binding is READABLE: three refs feeding one Space was
    invisible config, and its consequences kept reading as code bugs.
    More than one feeder can be legitimate (CSV plus API of one real
    account) - many usually is not, so several feeders render as a
    warning."""
    refs = feeders.get(account_id, [])
    if not refs:
        return ""
    shown = ", ".join(html.escape(_short_ref(ref)) for ref in refs)
    css = "warn" if len(refs) > 1 else "muted"
    note = " - several sources feed this one account" if len(refs) > 1 else ""
    return f'<br><span class="{css} mono">bound from: {shown}{note}</span>'


def _holdings_rows(
    holdings: Callable[[], list[SourceCoverage]] | None,
    display_labels: Callable[[], dict[str, str]] | None = None,
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None,
    account_feeders: Callable[[], dict[str, list[str]]] | None = None,
    source_connections: dict[tuple[str, str], list[str]] | None = None,
    feed_warnings: Callable[[], list[str]] | None = None,
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
    feeders_map: dict[str, list[str]] = {}
    if account_feeders is not None:
        try:
            feeders_map = account_feeders()
        except Exception:
            feeders_map = {}
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
        feeder_note = ""
        if ":" not in row.account_id and feeders_map:
            feeder_note = _feeder_line(row.account_id, feeders_map)
        bind_form = ""
        if ":" in row.account_id:
            # A source-qualified id is an account nobody has NAMED - and
            # binding must not require the extend section (TrueLayer-only)
            # or a shell. The provider's display label above makes the row
            # recognisable; this form makes the name canonical.
            held_suggestion = _suggest_slug(
                labels.get(row.account_id, ""), row.account_id
            )
            bind_form = (
                '<form method="post" action="/bind" '
                'style="display:flex;gap:.4rem;margin:.35rem 0">'
                f'<input type="hidden" name="account" '
                f'value="{html.escape(row.account_id)}">'
                f'<input name="canonical" value="{html.escape(held_suggestion)}" '
                'placeholder="name this account, '
                'e.g. starling-personal" style="flex:1">'
                '<button class="button" style="display:inline-block;'
                'padding:.5rem .8rem;border:0;cursor:pointer" '
                'type="submit">Bind</button></form>'
            )
        items.append(
            f'<div class="row"{row_style}><strong>'
            f'<a href="/account?ref={quote(row.account_id)}">'
            f"{title}</a></strong>"
            " via "
            + html.escape(
                _via_label(
                    row.source,
                    (source_connections or {}).get((row.account_id, row.source)),
                )
            )
            + f"{quiet}{sub}<br>"
            f"{row.count:,} transactions, {row.earliest} .. <strong>{row.latest}</strong>"
            f"{feeder_note}{bind_form}{strip}</div>"
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
        # A known-but-empty source-qualified ref still needs a way to be
        # named - after a consolidating rebuild these rows hold nothing
        # (their rows live under the map's canonical), and renaming the
        # map edge is exactly the repair they exist to receive. The bind
        # moves no rows; the next rebuild applies the new edge.
        empty_bind = ""
        if ":" in ref:
            empty_suggestion = _suggest_slug(labels.get(ref, ""), ref)
            empty_bind = (
                '<form method="post" action="/bind" '
                'style="display:flex;gap:.4rem;margin:.35rem 0">'
                f'<input type="hidden" name="account" value="{html.escape(ref)}">'
                f'<input name="canonical" value="{html.escape(empty_suggestion)}" '
                'placeholder="name this account, '
                'e.g. starling-personal" style="flex:1">'
                '<button class="button" style="display:inline-block;'
                'padding:.5rem .8rem;border:0;cursor:pointer" '
                'type="submit">Bind</button></form>'
            )
        feeder_note = _feeder_line(ref, feeders_map) if feeders_map else ""
        items.append(
            f'<div class="row" style="opacity:.62"><strong>{title}</strong>'
            f'{sub}<br><span class="muted">known account, nothing held yet - '
            f"{reach}</span>{feeder_note}{empty_bind}{strip}</div>"
        )

    legend = (
        '<p class="muted" style="font-size:.85em">timeline: solid = held, '
        "faint = asked and empty, dotted = truncated by the provider, "
        f"dashed = never asked; axis {axis_start.isoformat()} .. "
        f"{axis_end.isoformat()}</p>"
    )
    warnings_html = ""
    if feed_warnings is not None:
        try:
            warnings = feed_warnings()
        except Exception:
            warnings = []
        warnings_html = "".join(
            f'<p class="warn">{html.escape(str(line))}</p>' for line in warnings
        )
    return "<h2>Held so far</h2>" + warnings_html + legend + "".join(items)


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
    if label == ref:
        return ""
    source = ref.split(":", 1)[0] if ":" in ref else ""
    head, _, parenthetical = label.partition("(")
    base = re.sub(r"[^a-z0-9]+", "-", head.strip().lower()).strip("-")
    if not base:
        return ""
    # "Bills (starling space)" is a Space and the house convention names
    # it starling-space-bills; "Personal (starling)" is the main account.
    if "space" in parenthetical.lower() and source:
        return f"{source}-space-{base}"[:64].rstrip("-")
    if source and source not in base:
        base = f"{source}-{base}"
    return base[:64].rstrip("-")


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


def _applier_liveness(heartbeat: str, queued_count: int, now: datetime) -> str:
    """The queue's counterparty, made visible.

    Quiet fact when the applier is checking in; a warning naming the
    container when work is queued and nobody has looked at it - which
    otherwise reads as a silent, minutes-long mystery."""
    stamp = ""
    age_seconds: float | None = None
    if heartbeat:
        with contextlib.suppress(ValueError):
            seen = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            stamp = seen.strftime("%H:%M:%S")
            age_seconds = (now - seen).total_seconds()
    if not stamp:
        if queued_count:
            return (
                '<p class="warn">the applier has never checked this queue - '
                "look at the obdi-applier container</p>"
            )
        return ""
    if queued_count and age_seconds is not None and age_seconds > 120:
        minutes = int(age_seconds // 60)
        return (
            f'<p class="warn">work is queued but the applier last checked '
            f"the queue at {stamp}Z ({minutes} min ago) - look at the "
            "obdi-applier container</p>"
        )
    return f'<p class="muted">applier last checked the queue at {stamp}Z</p>'


def _scheduler_row(
    scheduler_heartbeat: Callable[[], dict[str, object]] | None,
    now: datetime | None = None,
) -> str:
    """The pull loop's pulse: "container running" does not prove "loop
    looping", so the cycle stamp gets the same treatment as the applier's
    heartbeat - a quiet fact when fresh, a warning naming the container
    when overdue."""
    if scheduler_heartbeat is None:
        return ""
    beat: dict[str, object] = {}
    try:
        beat = scheduler_heartbeat() or {}
    except Exception:
        return ""
    raw_at = str(beat.get("at", ""))
    if not raw_at:
        return ""
    try:
        seen = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    try:
        interval = int(str(beat.get("interval_seconds", 0)))
    except ValueError:
        interval = 0
    now = now or datetime.now(UTC)
    age = (now - seen).total_seconds()
    stamp = seen.strftime("%Y-%m-%d %H:%M")
    if interval > 0 and age > interval * 1.5:
        hours = age / 3600
        return (
            f'<p class="warn">the scheduler last completed a cycle at {stamp}Z '
            f"({hours:.1f} h ago, interval {interval // 3600} h) - look at "
            "the obdi-pull container</p>"
        )
    due = ""
    if interval > 0:
        due_at = datetime.fromtimestamp(seen.timestamp() + interval, tz=UTC)
        due = f" - next due by ~{due_at.strftime('%H:%M')}Z"
    return (
        f'<p class="muted">scheduler last completed a cycle at {stamp}Z{due}</p>'
    )


def _backfill_running_banner(
    backfill_status: Callable[[], dict[str, object]] | None,
    now: datetime | None = None,
) -> str:
    """The post-auth ladder announces itself: it races a five-minute
    window in a background thread, and its silence read as not-started -
    which sent a human off to race it manually, in parallel."""
    if backfill_status is None:
        return ""
    status: dict[str, object] = {}
    try:
        status = backfill_status() or {}
    except Exception:
        return ""
    if str(status.get("state", "")) != "running":
        return ""
    updated_raw = str(status.get("updated_at", ""))
    with contextlib.suppress(ValueError):
        updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        # A crashed thread must not banner forever: the ladder updates its
        # status every step, so a stale stamp means it is gone.
        if ((now or datetime.now(UTC)) - updated).total_seconds() > 900:
            return ""
    connection = html.escape(str(status.get("connection", "")))
    stage = str(status.get("stage", ""))
    detail = "fetching deep history"
    if stage == "ladder":
        target = status.get("target")
        targets = status.get("targets")
        if isinstance(target, int) and isinstance(targets, int):
            detail = (
                f"walking history to each wall - account {target} of {targets}"
            )
        else:
            detail = "walking history to each wall"
    return (
        f'<p class="warn">post-authorisation backfill running for '
        f"{connection}: {detail} - it races the five-minute window in the "
        "background; no need to press anything</p>"
    )


def _rebuild_running_banner(
    rebuild_status: Callable[[], dict[str, object]] | None,
) -> str:
    """Shown at the TOP of the page while a rebuild runs: the account
    listing below is a store mid-replay, and without a banner it
    masquerades as the truth."""
    if rebuild_status is None:
        return ""
    status: dict[str, object] = {}
    try:
        status = rebuild_status() or {}
    except Exception:
        return ""
    if str(status.get("state", "")) != "running":
        return ""
    return _rebuild_status_line(lambda: status)


@dataclass(frozen=True)
class _RebuildProgress:
    """What is known about a running rebuild, derived once.

    Deriving and rendering in one pass is what turned this display into a
    seven-clause sentence: each new fact was appended to whatever string
    existed, so the reading order followed the computation order and
    every addition needed another conditional fragment. Facts are worked
    out here and given a fixed home by the renderer, so adding one is a
    new row rather than another clause.

    Every field is optional because a status file may be written by an
    older build, and a missing fact must read as unknown rather than as
    zero.
    """

    started: str = ""
    artefacts: tuple[int, int] | None = None
    #: How big the artefact in flight is, and how far into it the replay
    #: has reached. Separate fields because the size is worth stating on
    #: its own - it explains a pause even when nothing reports position.
    artefact_size: int | None = None
    artefact_position: int | None = None
    records: tuple[int, int] | None = None
    transactions: int | None = None
    per_minute: float | None = None
    eta_minutes: float | None = None
    still_for: float | None = None
    #: Whether this status came from a build that reports within an
    #: artefact. It decides what a gap in updates MEANS: builds that tick
    #: per record should never go quiet, while older ones fall silent for
    #: the length of a big artefact as a matter of course.
    ticks_per_record: bool = False

    @property
    def fraction(self) -> float | None:
        if self.records is None or self.records[1] <= 0:
            return None
        return min(self.records[0] / self.records[1], 1.0)


def _parse_stamp(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pair(status: dict[str, object], first: str, second: str) -> tuple[int, int] | None:
    """Two counts that only mean anything together."""
    left = status.get(first)
    right = status.get(second)
    if isinstance(left, int) and isinstance(right, int) and right > 0:
        return (left, right)
    return None


def _read_progress(status: dict[str, object], now: datetime) -> _RebuildProgress:
    started_raw = str(status.get("started_at", ""))
    began = _parse_stamp(started_raw)
    records = _pair(status, "records_done", "records_total")
    transactions = status.get("transactions")

    per_minute: float | None = None
    eta_minutes: float | None = None
    if began is not None and records is not None:
        elapsed = (now - began).total_seconds()
        # Below half a minute the rate says more about start-up than
        # about throughput, and an ETA drawn from it would be fiction.
        if elapsed > 30 and records[0] > 0:
            per_minute = records[0] / (elapsed / 60)
            remaining = max(records[1] - records[0], 0)
            if per_minute > 0 and remaining > 0:
                eta_minutes = max(remaining / per_minute, 1)

    still_for: float | None = None
    updated = _parse_stamp(str(status.get("updated_at", "")))
    if updated is not None:
        still_for = max((now - updated).total_seconds(), 0)

    size = status.get("current_records")
    position = status.get("records_in_flight")
    return _RebuildProgress(
        started=started_raw,
        artefacts=_pair(status, "done", "total"),
        artefact_size=size if isinstance(size, int) and size > 0 else None,
        artefact_position=position if isinstance(position, int) and position > 0 else None,
        records=records,
        transactions=transactions if isinstance(transactions, int) else None,
        per_minute=per_minute,
        eta_minutes=eta_minutes,
        still_for=still_for,
        ticks_per_record="records_in_flight" in status,
    )


def _stillness_note(progress: _RebuildProgress) -> str:
    """How long since the numbers moved, and whether that is worrying.

    The same silence means opposite things depending on what is
    reporting. A build that ticks per record should refresh every second,
    so a minute of quiet is a symptom; one that reports only at artefact
    boundaries goes quiet for the length of a large file as a matter of
    course. Saying "stuck" in the second case would be wrong, and saying
    "this is normal" in the first would hide a fault.
    """
    if progress.still_for is None:
        return ""
    seconds = progress.still_for
    if seconds < 10:
        return ""
    minutes = int(seconds // 60)
    if progress.ticks_per_record:
        if minutes < 1:
            return f"Last update {int(seconds)}s ago."
        return (
            f'<strong>No update for {minutes} min</strong> - this build '
            "reports every record, so it may be stuck."
        )
    if minutes < 1:
        return f"Last update {int(seconds)}s ago."
    return (
        f"Counts last moved {minutes} min ago; this build reports only at "
        "artefact boundaries, so a large one holds them still."
    )


def _rebuild_running_html(progress: _RebuildProgress) -> str:
    """One row per fact, each with a fixed home.

    Ordered by what a reader needs and in what order they need it: how
    far along, what has been banked, what is happening right now, how
    long it will take, whether it is healthy, and what it stops them
    doing. Anything unknown omits its row rather than collapsing the
    others together.
    """
    rows: list[str] = []

    heading = "Rebuilding derived data"
    records = progress.records
    fraction = progress.fraction
    if records is not None and fraction is not None:
        done, expected = records
        rows.append(
            f'<p><strong>{heading}</strong> <span class="muted">'
            f"{fraction * 100:.0f}%</span></p>"
            f'<progress value="{done}" max="{expected}" '
            'style="width:100%"></progress>'
        )
    else:
        rows.append(f"<p><strong>{heading}</strong></p>")

    if progress.records is not None:
        banked = f"{progress.records[0]:,} of {progress.records[1]:,} records"
        if progress.transactions is not None:
            banked += f" replayed into {progress.transactions:,} transaction(s)"
        rows.append(f"<p>{banked}.</p>")
    elif progress.transactions is not None:
        rows.append(f"<p>{progress.transactions:,} transaction(s) so far.</p>")

    if progress.artefacts is not None:
        where = f"Artefact {progress.artefacts[0]:,} of {progress.artefacts[1]:,}"
        if progress.artefact_size is not None:
            if progress.artefact_position is not None:
                # Position within the batch is stated as resolved-not-banked
                # on purpose: a batch commits once, at its end, so this
                # much is real work that a crash would still take back.
                where += (
                    f", {progress.artefact_position:,} of "
                    f"{progress.artefact_size:,} records into it "
                    "(not yet committed)"
                )
            else:
                where += f", which holds {progress.artefact_size:,} records"
        rows.append(f'<p class="muted">{where}.</p>')

    pace: list[str] = []
    if progress.per_minute is not None:
        pace.append(f"~{progress.per_minute:,.0f} records/min")
    if progress.eta_minutes is not None:
        pace.append(f"about {progress.eta_minutes:.0f} min remaining")
    note = _stillness_note(progress)
    if pace or note:
        # Only the first character is raised. str.capitalize() would
        # lower-case everything after it, quietly mangling any term that
        # is capitalised for a reason.
        sentence = ", ".join(pace)
        sentence = sentence[:1].upper() + sentence[1:]
        tail = ". ".join(part for part in [sentence, note] if part)
        rows.append(f'<p class="muted">{tail}</p>')

    rows.append(
        '<p class="muted">Started '
        f"{html.escape(progress.started)}. Refresh to follow it; deploys "
        "defer while it holds its lease.</p>"
    )
    return '<div class="warn">' + "".join(rows) + "</div>"


def _rebuild_status_line(
    rebuild_status: Callable[[], dict[str, object]] | None,
    now: datetime | None = None,
) -> str:
    if rebuild_status is None:
        return ""
    status: dict[str, object] = {}
    try:
        status = rebuild_status() or {}
    except Exception:
        return ""
    state = str(status.get("state", ""))
    if state == "running":
        return _rebuild_running_html(_read_progress(status, now or datetime.now(UTC)))
    if state == "done":
        badge = "ok" if status.get("ok") else "bad"
        finished = html.escape(str(status.get("finished_at", "")))
        summary = html.escape(str(status.get("summary", ""))).replace(
            chr(10), "<br>"
        )
        return (
            f'<p><span class="pill pill-{badge}">last rebuild</span> '
            f'<span class="muted">{finished}: {summary}</span></p>'
        )
    return ""


def _via_label(source: str, connections: list[str] | None) -> str:
    """What an account row was fed BY: the witness, as specifically as known.

    The connection name when attribution knows it ("via halifax"), the
    bare pipe when it does not ("via truelayer") - never a guess dressed
    as a name. Several connections on one pipe all get named, because
    that is exactly the situation the label exists to disambiguate.
    """
    if connections:
        return ", ".join(connections)
    return source


def _rebuild_history_html(
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None,
) -> str:
    """The cost record over time, from rows rather than log-greps.

    Each run stores what the timings flag prints, so "is it getting
    slower as the corpus grows" is answered by this table - which is
    exactly the question the journal decision is parked on.
    """
    if recent_rebuilds is None:
        return ""
    try:
        runs = recent_rebuilds()
    except Exception:
        return ""
    if not runs:
        return ""

    rows = []
    for run in runs:
        ok = bool(run.get("ok"))
        badge = "ok" if ok else "bad"
        started = str(run.get("started_at", ""))
        finished = str(run.get("finished_at", ""))
        duration = ""
        with contextlib.suppress(ValueError):
            span = datetime.fromisoformat(
                finished.replace("Z", "+00:00")
            ) - datetime.fromisoformat(started.replace("Z", "+00:00"))
            duration = f"{span.total_seconds():,.0f}s"
        records = run.get("records_total")
        transactions = run.get("transactions")
        volume = ""
        if isinstance(records, int):
            volume = f"{records:,} records"
            if isinstance(transactions, int):
                volume += f" -> {transactions:,}"
        timings = run.get("timings")
        slowest = ""
        if isinstance(timings, dict) and timings:
            name, figures = next(iter(timings.items()))
            if isinstance(figures, dict):
                slowest = f"{html.escape(str(name))} {figures.get('seconds')}s"
        rows.append(
            "<tr>"
            f'<td><span class="pill pill-{badge}">{"ok" if ok else "failed"}</span></td>'
            f"<td>{html.escape(finished)}</td>"
            f"<td>{duration}</td>"
            f"<td>{volume}</td>"
            f'<td class="muted">{slowest}</td>'
            f'<td class="muted">{html.escape(str(run.get("build", "")))}</td>'
            "</tr>"
        )
    return (
        "<h3>Recent rebuilds</h3>"
        '<div style="overflow-x:auto"><table>'
        "<tr><th></th><th>finished</th><th>took</th><th>volume</th>"
        "<th>slowest phase</th><th>build</th></tr>"
        + "".join(rows)
        + "</table></div>"
    )


def _probe_result_html(report: object) -> str:
    """The verdict first, then exactly the numbers behind it.

    The wording comes from the report itself, which states no more than
    the single response proved - a probe page that editorialised beyond
    its evidence would poison the decision it exists to inform.
    """
    verdict = ""
    with contextlib.suppress(Exception):
        verdict = str(report.verdict())  # type: ignore[attr-defined]
    cutoff = html.escape(str(getattr(report, "cutoff", "")))
    decisive = bool(getattr(report, "before_cutoff", 0))

    rows_html = ""
    for account in getattr(report, "accounts", []) or []:
        rows_html += (
            "<tr>"
            f"<td>{html.escape(str(getattr(account, 'label', '')))}</td>"
            f"<td>{getattr(account, 'items', 0):,}</td>"
            f"<td>{getattr(account, 'before_cutoff', 0):,}</td>"
            "<td class=muted>"
            + html.escape(str(getattr(account, "oldest_transaction_time", ""))[:19])
            + "</td><td class=muted>"
            + html.escape(str(getattr(account, "newest_transaction_time", ""))[:19])
            + "</td>"
            "</tr>"
        )
    problems = "".join(
        f"<p class=warn>{html.escape(str(problem))}</p>"
        for problem in getattr(report, "problems", []) or []
    )

    pill = "ok" if decisive else "warn"
    return (
        f'<h1>changesSince probe</h1>'
        f'<p class="muted">cutoff {cutoff}</p>'
        f'<p><span class="pill pill-{pill}">'
        f'{"decisive" if decisive else "not decisive"}</span> '
        f"{html.escape(verdict)}</p>"
        '<div style="overflow-x:auto"><table>'
        "<tr><th>account</th><th>items</th><th>transactionTime before cutoff</th>"
        "<th>oldest txn</th><th>newest txn</th></tr>"
        f"{rows_html}</table></div>"
        f"{problems}"
        '<p class="muted">Every response above was landed in layer 0 with its '
        "asked-for cutoff recorded, so this experiment is replayable "
        "evidence, not a screenshot.</p>"
        + HOME_LINK
    )


def _probe_section_html(
    available: bool,
    probe_suggestions: Callable[[], list[object]] | None,
) -> str:
    """The changesSince experiment as buttons.

    The naive probe is ambiguous - an empty answer is what BOTH possible
    semantics produce on a quiet account - so the suggestions row is the
    heart of this section: cutoffs derived from amendments the store has
    already witnessed, each placed between a transaction's own time and
    the moment its record changed. Only update-time filtering can return
    that item, so one tap gives a decisive answer.
    """
    if not available:
        return ""
    suggestions = []
    if probe_suggestions is not None:
        with contextlib.suppress(Exception):
            suggestions = probe_suggestions()

    def form(cutoff: str, label: str, note: str = "") -> str:
        return (
            '<form method="post" action="/starling-probe" '
            'style="display:inline-block;margin:.2rem .3rem .2rem 0">'
            f'<input type="hidden" name="cutoff" value="{html.escape(cutoff)}">'
            '<button class="button" type="submit" '
            'style="border:0;cursor:pointer;padding:.4rem .7rem">'
            f"{html.escape(label)}</button>"
            f'{f"<span class=muted> {html.escape(note)}</span>" if note else ""}'
            "</form>"
        )

    now = datetime.now(UTC)
    presets = "".join(
        form((now - timedelta(days=days)).isoformat().replace("+00:00", "Z"), label)
        for days, label in ((7, "7 days ago"), (28, "28 days ago"), (90, "90 days ago"))
    )
    straddles = "".join(
        form(
            str(getattr(s, "cutoff", "")),
            f"straddle amendment {getattr(s, 'item_hint', '')}",
            f"txn {str(getattr(s, 'transaction_time', ''))[:10]}, "
            f"changed {str(getattr(s, 'changed_at', ''))[:10]}",
        )
        + "<br>"
        for s in suggestions
    )
    if not straddles:
        straddles = (
            '<p class="muted">No amendment straddles available yet - the store '
            "has not witnessed a feed item change. Presets still answer the "
            '"anything new since" question; the semantics question needs an '
            "amendment to straddle.</p>"
        )

    return (
        "<h3>Starling changesSince probe</h3>"
        '<p class="muted">One read-only ask that decides the sync design: '
        "does changesSince return items by when they HAPPENED or by when "
        "their record last CHANGED? A cutoff placed between a known "
        "amendment's two timestamps distinguishes the two - only "
        "update-time filtering can return that item. Every response lands "
        "in layer 0 as evidence.</p>"
        f"<p>{presets}</p>"
        f"<p>Decisive cutoffs, from amendments the store has seen:<br>{straddles}</p>"
        '<form method="post" action="/starling-probe" '
        'style="display:flex;gap:.4rem;margin:.35rem 0">'
        '<input name="cutoff" placeholder="or any cutoff: 2026-08-03T09:00:00Z" '
        'style="flex:1">'
        '<button class="button" type="submit" '
        'style="border:0;cursor:pointer">Probe</button></form>'
    )


def _danger_zone(
    rebuild_available: bool,
    forget_available: bool,
    rebuild_status: Callable[[], dict[str, object]] | None = None,
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None = None,
) -> str:
    if not (rebuild_available or forget_available):
        return ""
    parts = [
        "<h2>Danger zone</h2>",
        '<p class="muted">Administrative repairs. Each asks for '
        "confirmation; none touches the raw artefacts in layer 0.</p>",
    ]
    checkbox = (
        '<label style="display:block;margin:.35rem 0">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand</label>"
    )
    button_style = (
        'style="border:0;width:100%;font-size:inherit;cursor:pointer;'
        'background:#dc262622;color:#b91c1c"'
    )
    if rebuild_available:
        parts.append(_rebuild_status_line(rebuild_status))
        parts.append(_rebuild_history_html(recent_rebuilds))
        parts.append(
            '<form method="post" action="/rebuild-derived">'
            "<p>Wipe the derived transaction layer and replay every raw "
            "artefact through the current account map and rules - binds "
            "made since the artefacts landed are applied, so rows and "
            "coverage consolidate under one name per account. Fixes "
            "duplicated or misfiled rows; identities are content-keyed, so "
            "downstream imports dedupe cleanly. Runs in the background - "
            "the result appears here.</p>"
            + checkbox
            + f'<p><button class="button" type="submit" {button_style}>'
            "Rebuild from raw</button></p></form>"
        )
    if forget_available:
        parts.append(
            '<form method="post" action="/forget-actual-bindings">'
            "<p>Forget which Actual account each canonical account maps to "
            "(names are kept). Use after deleting accounts on the Actual "
            "side: the next push re-provisions by name, reusing any "
            "same-named accounts that still exist.</p>"
            + checkbox
            + f'<p><button class="button" type="submit" {button_style}>'
            "Forget Actual account links</button></p></form>"
        )
    return "".join(parts)


def _result_row(result: dict[str, object]) -> str:
    """One sync outcome, whatever its kind - shared by the homepage
    section (newest handful) and the full history page."""
    if str(result.get("kind", "")) == "audit":
        return _audit_result_row(result)
    if str(result.get("kind", "")) == "prune":
        return _prune_result_row(result)
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
    return (
        f'<div class="row"><strong>{stamp}Z</strong> {badge}'
        f'<br><span class="muted">{detail}</span></div>'
    )


def _prune_result_row(result: dict[str, object]) -> str:
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    if not result.get("ok"):
        return (
            f'<div class="row"><strong>{stamp}Z</strong> '
            '<span class="pill pill-bad">prune failed</span>'
            f'<br><span class="muted">{html.escape(str(result.get("error", "")))}'
            "</span></div>"
        )
    raw = result.get("accounts")
    accounts = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []
    removed_total = sum(
        int(a.get("removed", 0)) for a in accounts if "removed" in a
    )
    lines = []
    for account in accounts:
        name = html.escape(str(account.get("name") or account.get("account_id", "")))
        if account.get("skipped"):
            lines.append(
                f'<span class="warn">{name}: '
                f"{html.escape(str(account.get('skipped')))}</span>"
            )
        elif account.get("removed"):
            lines.append(
                f'<span class="muted">{name}: removed '
                f"{account.get('removed')} orphaned import(s)</span>"
            )
    return (
        f'<div class="row"><strong>{stamp}Z</strong> '
        f'<span class="pill pill-ok">pruned ({removed_total} removed)</span><br>'
        + "<br>".join(lines)
        + "</div>"
    )


def _audit_result_row(result: dict[str, object]) -> str:
    """One audit outcome: a verdict pill, then a line per account.

    "yours" is the count of rows without an imported id - the person's own
    entries, counted to show they were seen and deliberately not compared.
    """
    stamp = html.escape(str(result.get("finished_at", ""))[:16].replace("T", " "))
    if not result.get("ok"):
        return (
            f'<div class="row"><strong>{stamp}Z</strong> '
            '<span class="pill pill-bad">audit failed</span>'
            f'<br><span class="muted">{html.escape(str(result.get("error", "")))}'
            "</span></div>"
        )
    raw = result.get("accounts")
    accounts = [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []

    def _dirty(account: dict[str, object]) -> bool:
        return bool(
            account.get("missing_account")
            or account.get("unbound_in_actual")
            or account.get("missing")
            or account.get("orphaned")
            or account.get("diverged")
            or account.get("duplicated")
        )

    badge = (
        '<span class="pill pill-bad">audit: differences</span>'
        if any(_dirty(a) for a in accounts)
        else '<span class="pill pill-ok">audit clean</span>'
    )
    lines = []
    for account in accounts:
        name = html.escape(str(account.get("name") or account.get("account_id", "")))
        if account.get("missing_account"):
            lines.append(
                f'<span class="warn">{name}: account missing from Actual '
                f"({account.get('expected', 0)} expected row(s))</span>"
            )
            continue
        if account.get("unbound_in_actual"):
            lines.append(
                f'<span class="warn">{name}: exists in Actual but no '
                f"canonical account maps to it ({account.get('rows', 0)} "
                "row(s)) - delete it there, or bind something to it</span>"
            )
            continue
        detail = (
            f"expected {account.get('expected', 0)}, "
            f"present {account.get('present', 0)}, "
            f"missing {account.get('missing', 0)}, "
            f"orphaned {account.get('orphaned', 0)}, "
            f"yours {account.get('human', 0)}, "
            f"diverged {account.get('diverged', 0)}, "
            f"duplicated {account.get('duplicated', 0)}"
        )
        css = "warn" if _dirty(account) else "muted"
        lines.append(f'<span class="{css}">{name}: {detail}</span>')
    return (
        f'<div class="row"><strong>{stamp}Z</strong> {badge}<br>'
        + "<br>".join(lines)
        + "</div>"
    )


def _actual_rows(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    push_available: bool,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    audit_available: bool = False,
    actual_heartbeat: Callable[[], str] | None = None,
    prune_available: bool = False,
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
    queued_html = ""
    if actual_queue is not None:
        try:
            queued = actual_queue()
        except Exception:
            queued = []
        parts = []
        for entry in queued:
            stamp = html.escape(str(entry.get("queued_at", ""))[11:19]) or html.escape(
                str(entry.get("name", ""))
            )
            kind_note = " (audit)" if entry.get("kind") == "audit" else ""
            since = str(entry.get("in_progress_since", ""))
            if since:
                what = f"in progress{kind_note}"
                note = (
                    "the applier picked this up at "
                    f"{html.escape(since[11:19])}Z and is working on it"
                )
            else:
                what = f"queued{kind_note}"
                note = "waiting for the applier"
            parts.append(
                f'<div class="row"><strong>{stamp}Z</strong> '
                f'<span class="pill pill-quiet">{what}</span>'
                f'<br><span class="muted">{note}</span></div>'
            )
        queued_html = "".join(parts)
        heartbeat = ""
        if actual_heartbeat is not None:
            try:
                heartbeat = actual_heartbeat()
            except Exception:
                heartbeat = ""
        queued_html += _applier_liveness(
            heartbeat, len(queued), datetime.now(UTC)
        )
    results = []
    if actual_status is not None:
        try:
            results = actual_status()
        except Exception:
            results = []
    rows = [_result_row(result) for result in results]
    button = (
        '<form method="post" action="/push-actual">'
        '<p><button class="button" type="submit" '
        'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
        "Push to Actual now</button></p></form>"
        if push_available
        else ""
    )
    audit_button = (
        '<form method="post" action="/audit-actual">'
        '<p><button class="button" type="submit" '
        'style="border:0;width:100%;font-size:inherit;cursor:pointer;'
        'background:#8882;color:inherit">'
        "Audit Actual now</button></p></form>"
        if audit_available
        else ""
    )
    prune_button = (
        '<form method="post" action="/prune-actual">'
        '<label style="display:block;margin:.35rem 0">'
        '<input type="checkbox" name="confirm" value="yes" required> '
        "I understand rows carrying obdi's imported ids that are no longer "
        "expected will be deleted from Actual</label>"
        '<p><button class="button" type="submit" '
        'style="border:0;width:100%;font-size:inherit;cursor:pointer;'
        'background:#dc262622;color:#b91c1c">'
        "Remove orphaned imports</button></p></form>"
        if prune_available
        else ""
    )
    return (
        "<h2>Actual sync</h2>"
        "<p>Pushes run through the applier container: bound accounts import, "
        "named accounts are created in Actual automatically (empty ones "
        "included) and their transactions ride the next push. The applier "
        "checks the queue about every 20 seconds; the scheduler also "
        "queues a push after each pull cycle, every six hours.</p>"
        "<p>The audit reads each bound account back from Actual and "
        "reports differences without changing anything - rows without an "
        "imported id are yours and are only counted.</p>"
        + roster_html
        + button
        + audit_button
        + prune_button
        + queued_html
        + "".join(rows)
        + ('<p><a href="/actual-history">Full sync history</a></p>' if rows else "")
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


def _extend_suggestions(accounts: list[ExtendableAccount]) -> dict[str, str]:
    """A suggested canonical name per unbound account, batch-aware.

    The display is "Title (TYPE)". A real title slugs through whole; a
    holder-name display is useless as a name and shows up as a COLLISION
    within the connection (every account titled after the same person),
    in which case the type is the informative part."""

    def _slug(text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")

    titles: dict[tuple[str, str], int] = {}
    parsed: dict[str, tuple[str, str, str]] = {}
    for account in accounts:
        head, _, parenthetical = account.display.partition("(")
        title = head.strip()
        kind = parenthetical.rstrip(")").strip()
        parsed[account.provider_ref] = (account.connection, title, kind)
        titles[(account.connection, title)] = (
            titles.get((account.connection, title), 0) + 1
        )
    suggestions: dict[str, str] = {}
    for ref, (connection, title, kind) in parsed.items():
        informative = kind if titles[(connection, title)] > 1 else title
        slug = _slug(f"{connection}-{informative}")
        if slug:
            suggestions[ref] = slug[:64].rstrip("-")
    return suggestions


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
    suggestions = _extend_suggestions([a for a in accounts if a.unbound])
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
            suggested = suggestions.get(account.provider_ref, "")
            bind_form = (
                '<form method="post" action="/bind" '
                'style="display:flex;gap:.4rem;margin:.4rem 0">'
                f'<input type="hidden" name="account" '
                f'value="{html.escape(account.provider_ref)}">'
                f'<input name="canonical" value="{html.escape(suggested)}" '
                'placeholder="name this account, '
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


def account_options(labels: dict[str, str]) -> str:
    """The import destination picker's options, every one self-identifying.

    Providers reuse display names (Starling labels a main account's uid
    AND its defaultCategory identically), so two different destinations
    can share a label - and a picker showing identical text for
    different refs is a wrong import waiting to happen. Colliding labels
    get their canonical ref appended; unique labels stay clean, because
    the ref is noise when nothing collides.
    """
    from collections import Counter

    counts = Counter(labels.values())
    options = []
    for ref, name in sorted(labels.items(), key=lambda kv: (kv[1], kv[0])):
        shown = f"{name} [{ref}]" if counts[name] > 1 else name
        if ":" in ref:
            # A provider-qualified ref is an UNBOUND passthrough (resolve's
            # deliberate fallback): importing into it lands rows in a
            # separate, uncorroborated account. Legitimate for genuinely
            # new accounts - but never as an innocent-looking twin of a
            # bound canonical, so the option states the consequence.
            shown += " (unbound - imports here land in a SEPARATE account)"
        options.append(
            f'<option value="{html.escape(ref)}">{html.escape(shown)}</option>'
        )
    return "".join(options)


def account_picker(
    labels: dict[str, str],
    *,
    field: str = "account",
    other_field: str = "account_other",
    other_placeholder: str = "or type a canonical name, e.g. hsbc-old-current",
) -> str:
    """The shared destination picker: a labelled dropdown plus a free-text
    escape hatch, one component wherever an account is chosen.

    Extracted after the refile form shipped as a bare text input - typing a
    canonical name by hand to CORRECT a mis-pick is exactly where a second
    typo compounds the first. Every chooser gets the same self-identifying
    options and the same fallback.
    """
    return (
        f'<p><select name="{html.escape(field)}" style="width:100%;padding:.6rem">'
        '<option value="">choose an account...</option>'
        f"{account_options(labels)}</select></p>"
        f'<p><input name="{html.escape(other_field)}" '
        f'placeholder="{html.escape(other_placeholder)}"></p>'
    )


def render_index(
    store: ConnectionStore,
    holdings: Callable[[], list[SourceCoverage]] | None = None,
    provider_knowledge: Callable[[], list[dict[str, object]]] | None = None,
    extendables: Callable[[], list[ExtendableAccount]] | None = None,
    starling_status: Callable[[], dict[str, object] | None] | None = None,
    display_labels: Callable[[], dict[str, str]] | None = None,
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None,
    account_feeders: Callable[[], dict[str, list[str]]] | None = None,
    push_actual: Callable[[], str] | None = None,
    actual_status: Callable[[], list[dict[str, object]]] | None = None,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    audit_actual: Callable[[], str] | None = None,
    prune_actual: Callable[[], str] | None = None,
    rename_connection: Callable[[str, str], str] | None = None,
    actual_heartbeat: Callable[[], str] | None = None,
    rebuild_available: bool = False,
    forget_available: bool = False,
    rebuild_status: Callable[[], dict[str, object]] | None = None,
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None = None,
    source_connections: dict[tuple[str, str], list[str]] | None = None,
    starling_probe_available: bool = False,
    probe_suggestions: Callable[[], list[object]] | None = None,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None,
    backfill_status: Callable[[], dict[str, object]] | None = None,
    feed_warnings: Callable[[], list[str]] | None = None,
) -> bytes:
    # The import form picks its destination FIRST (the preview verifies
    # the file against what that account already holds), so the picker's
    # options are needed here rather than on the confirm page.
    upload_labels: dict[str, str] = {}
    if display_labels is not None:
        with contextlib.suppress(Exception):
            upload_labels = display_labels()
    upload_picker = account_picker(upload_labels)
    body = f"""
{_credential_banner()}
{_rebuild_running_banner(rebuild_status)}
{_backfill_running_banner(backfill_status)}
{_connection_rows(store, rename_available=rename_connection is not None)}
{_starling_row(starling_status)}
{_holdings_rows(holdings, display_labels, account_timelines, account_feeders,
                source_connections, feed_warnings)}
{_knowledge_rows(provider_knowledge)}
{_scheduler_row(scheduler_heartbeat)}
{_actual_rows(actual_status, push_actual is not None, actual_roster, actual_queue,
              audit_available=audit_actual is not None,
              actual_heartbeat=actual_heartbeat,
              prune_available=prune_actual is not None)}
{_extend_rows(extendables)}
<p><a class="button" href="/artefacts">Browse raw artefacts</a></p>
<p><a class="button" href="/attempts">Fetch attempts</a>
<a class="button" href="/fetch-timeline">Fetch timeline</a></p>
<p><a class="button" href="/agreements">Cross-source agreement report</a></p>
<p><a class="button" href="/review">Categorise (uncategorised worklist)</a></p>
<p><a class="button" href="/statement-shape">Statement shape (PDF layout, values masked)</a></p>
<p><a class="button" href="/review-report">Review queue report</a></p>
<p><a class="button" href="/date-lag">Settlement lag report</a></p>
<p><a class="button" href="/balance-walk">Balance walk report</a></p>
<h2>Import a file</h2>
<p>Bank CSV or QIF exports. Choose the destination FIRST - the preview can
then verify the file against what that account already holds, before
anything is stored.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
  {upload_picker}
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
{_probe_section_html(starling_probe_available, probe_suggestions)}
{_danger_zone(rebuild_available, forget_available, rebuild_status, recent_rebuilds)}
"""
    return render_page("Bank connections", body)


#: Typed by hand to disclose a statement's real contents. A phrase costs
#: a deliberate keystroke sequence; a checkbox costs one field in one
#: request, which is what an automated caller sends without meaning to.
DISCLOSURE_PHRASE = "SHOW REAL VALUES"


class ConnectionHandler(BaseHTTPRequestHandler):
    config: WebConfig | None = None
    session: AuthorisationSession | None = None
    #: Statements awaiting an explicit disclosure confirmation. Same
    #: single-use, expiring shape as the upload stash, holding bytes only
    #: for the walk between the two requests.
    disclosures: UploadSession = UploadSession()

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
        began = time.perf_counter()
        try:
            self._dispatch_get(parsed, route)
        finally:
            _report_slow_route("GET", route, time.perf_counter() - began)

    def _dispatch_get(self, parsed: ParseResult, route: str) -> None:
        params = parse_qs(parsed.query)

        if route == "/account":
            self._account(params)
            return
        if route == "/fetch-timeline":
            self._fetch_timeline(parse_qs(parsed.query))
            return
        if route == "/attempts":
            self._attempts()
            return
        if route == "/agreements":
            self._agreements_page()
            return
        if route == "/statement-shape":
            params = parse_qs(parsed.query)
            kept = params.get("artefact", [""])[0].strip()
            if kept.isdigit():
                self._kept_statement_shape(int(kept))
                return
            self._statement_shape_form()
            return
        if route == "/review":
            self._review_page()
            return
        if route == "/actual-history":
            self._actual_history()
            return
        if route == "/review-report":
            self._review_report()
            return
        if route == "/date-lag":
            self._date_lag()
            return
        if route == "/balance-walk":
            self._balance_walk()
            return
        if route == "/artefacts":
            self._artefacts()
            return
        if route == "/artefact":
            self._artefact(params)
            return
        if route == "/healthz":
            # Liveness, nothing more: proves the serve loop answers. The
            # container healthcheck rides THIS, never a content page - the
            # index once grew past the probe's timeout and kept a working
            # container 'unhealthy' for its entire 3-day life, invisibly.
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/":
            # Every hook individually timed, and a slow render publishes
            # its own cost breakdown to the log. Three refuted theories
            # (locks, raw-JSON parsing, provider calls) proved nobody can
            # guess where 40 seconds lives - the page must say.
            hook_seconds: dict[str, float] = {}

            def timed(
                name: str, hook: Callable[_HookParams, _HookReturn] | None
            ) -> Callable[_HookParams, _HookReturn] | None:
                if hook is None:
                    return None
                bound = hook

                def call(
                    *args: _HookParams.args, **kwargs: _HookParams.kwargs
                ) -> _HookReturn:
                    began = time.perf_counter()
                    try:
                        return bound(*args, **kwargs)
                    finally:
                        hook_seconds[name] = (
                            hook_seconds.get(name, 0.0) + time.perf_counter() - began
                        )

                return call

            render_began = time.perf_counter()
            page = render_index(
                self.bound_config.connection_store,
                holdings=timed("holdings", self.bound_config.holdings),
                provider_knowledge=timed(
                    "provider_knowledge", self.bound_config.provider_knowledge
                ),
                extendables=timed("extendables", self.bound_config.extendables),
                starling_status=timed(
                    "starling_status", self.bound_config.starling_status
                ),
                display_labels=timed(
                    "display_labels", self.bound_config.display_labels
                ),
                account_timelines=timed(
                    "account_timelines", self.bound_config.account_timelines
                ),
                account_feeders=timed(
                    "account_feeders", self.bound_config.account_feeders
                ),
                push_actual=self.bound_config.push_actual,
                actual_status=timed("actual_status", self.bound_config.actual_status),
                actual_roster=timed("actual_roster", self.bound_config.actual_roster),
                actual_queue=timed("actual_queue", self.bound_config.actual_queue),
                audit_actual=self.bound_config.audit_actual,
                prune_actual=self.bound_config.prune_actual,
                rename_connection=self.bound_config.rename_connection,
                actual_heartbeat=timed(
                    "actual_heartbeat", self.bound_config.actual_heartbeat
                ),
                rebuild_available=self.bound_config.rebuild_derived is not None,
                forget_available=self.bound_config.forget_actual is not None,
                rebuild_status=timed(
                    "rebuild_status", self.bound_config.rebuild_status
                ),
                recent_rebuilds=timed(
                    "recent_rebuilds", self.bound_config.recent_rebuilds
                ),
                source_connections=(
                    connections_hook()
                    if (
                        connections_hook := timed(
                            "source_connections", self.bound_config.source_connections
                        )
                    )
                    is not None
                    else None
                ),
                starling_probe_available=self.bound_config.starling_probe is not None,
                probe_suggestions=timed(
                    "probe_suggestions", self.bound_config.probe_suggestions
                ),
                feed_warnings=timed(
                    "feed_warnings", self.bound_config.feed_warnings
                ),
                scheduler_heartbeat=timed(
                    "scheduler_heartbeat", self.bound_config.scheduler_heartbeat
                ),
                backfill_status=timed(
                    "backfill_status", self.bound_config.backfill_status
                ),
            )
            render_seconds = time.perf_counter() - render_began
            threshold = float(os.environ.get("OBDI_WEB_SLOW_RENDER_SECS", "2.0"))
            if render_seconds >= threshold:
                slowest = sorted(
                    hook_seconds.items(), key=lambda kv: kv[1], reverse=True
                )[:8]
                accounted = sum(hook_seconds.values())
                print(
                    f"web timing: / rendered in {render_seconds:.2f}s - "
                    + ", ".join(f"{name} {secs:.2f}s" for name, secs in slowest)
                    + f" (hooks total {accounted:.2f}s; the remainder is "
                    "templating and store-free work)",
                    flush=True,
                )
            self._respond(200, page)
        elif route == "/connect":
            self._connect(params)
        elif route == "/callback":
            self._callback(params)
        else:
            self._respond(404, error_page("Not found", "<p>Nothing is served here.</p>"))

    def _fetch_timeline(self, params: dict[str, list[str]]) -> None:
        """Every ask as a bar over the history it asked about."""
        hook = self.bound_config.recent_attempts
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No ledger wired.</p>"))
            return
        raw_days = (params.get("days") or ["7"])[0]
        days: int | None
        if raw_days == "all":
            days = None
        else:
            try:
                days = max(1, min(int(raw_days), 730))
            except ValueError:
                days = 7

        # The horizontal axis is its own control. `days` filters which
        # asks appear; `span` says how far back the CHART reaches - the
        # two once shared one knob and every row filter narrower than
        # the clamp drew the identical axis. "fit" stretches the domain
        # to hold every drawn bar, so nothing clips.
        raw_span = (params.get("span") or ["120"])[0]
        span: int | None
        if raw_span == "fit":
            span = None
        else:
            try:
                span = max(1, min(int(raw_span), 4000))
            except ValueError:
                span = 120

        # Pan: `until` moves the right edge; zoom is the range presets.
        # Server-side on purpose - the page stays script-free until the
        # redesign decides about client scripting once, deliberately.
        from datetime import UTC as _UTC

        until: datetime | None = None
        raw_until = (params.get("until") or [""])[0].strip()
        if raw_until:
            with contextlib.suppress(ValueError):
                parsed = datetime.fromisoformat(raw_until.replace("Z", "+00:00"))
                until = (
                    parsed if parsed.tzinfo else parsed.replace(tzinfo=_UTC)
                ).astimezone(_UTC)

        from .timeline import timeline_svg

        def link(query: str, label: str, current: bool = False) -> str:
            if current:
                return f"<strong>{label}</strong>"
            return f'<a href="/fetch-timeline?{query}">{label}</a>'

        # Links are rebuilt from the PARSED values, never the raw params:
        # a raw string that failed parsing must not be reflected into hrefs.
        until_query = f"&until={raw_until}" if until else ""
        span_value = "fit" if span is None else str(span)
        span_query = f"&span={span_value}" if span != 120 else ""
        days_query = f"days={'all' if days is None else days}"
        ranges = " | ".join(
            [
                link(f"days={n}{span_query}{until_query}", label, current=days == n)
                for n, label in (
                    (1, "24 hours"),
                    (7, "7 days"),
                    (30, "30 days"),
                    (56, "56 days"),
                    (72, "72 days"),
                    (365, "1 year"),
                    (730, "2 years"),
                )
            ]
            + [link(f"days=all{span_query}{until_query}", "everything", current=days is None)]
        )
        spans = " | ".join(
            [link(f"{days_query}&span=fit{until_query}", "fit", current=span is None)]
            + [
                link(f"{days_query}&span={n}{until_query}", label, current=span == n)
                for n, label in (
                    (30, "30 days"),
                    (56, "56 days"),
                    (90, "90 days"),
                    (120, "120 days"),
                    (365, "1 year"),
                    (730, "2 years"),
                )
            ]
        )

        pan = ""
        if days is not None:
            anchor = until or datetime.now(_UTC)
            older = (anchor - timedelta(days=days / 2)).isoformat().replace("+00:00", "Z")
            newer = (anchor + timedelta(days=days / 2)).isoformat().replace("+00:00", "Z")
            pan_links = [link(f"days={days}{span_query}&until={older}", "&larr; older")]
            if until is not None:
                pan_links.append(link(f"days={days}{span_query}&until={newer}", "newer &rarr;"))
                pan_links.append(link(f"days={days}{span_query}", "now"))
            pan = "<p>Pan: " + " | ".join(pan_links) + "</p>"

        body = (
            "<h1>Fetch timeline</h1>"
            '<p class="muted">Each row is one ask from the attempt ledger, '
            "newest at the top; the bar spans the history it asked about. "
            "The fetch strategy reads straight off the shapes: tier steps, "
            "cursor slivers hugging now, ladder bursts, probe cuts.</p>"
            f"<p>Asks made in the last: {ranges}</p>"
            f"<p>Chart reaches back: {spans} "
            '<span class="muted">(fit = stretch to the oldest drawn ask, '
            "nothing clipped)</span></p>"
            f"{pan}"
            + timeline_svg(hook(), days=days, clamp_days=span, now=until)
            + '<p><a class="button" href="/attempts">Fetch attempts ledger</a></p>'
            + HOME_LINK
        )
        self._respond(200, render_page("Fetch timeline", body))

    def _agreements_page(self) -> None:
        """The standing cross-source review: the import-page verdicts,
        browsable any time - built for the bulk-import-then-review workflow,
        where reading every transient import result is exactly what nobody
        does."""
        hook = self.bound_config.agreement_report
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No report wired.</p>"))
            return
        report = hook()
        parts: list[str] = [
            '<p class="muted">Every pair of sources that describes the same '
            "account, compared over the period they share. Each side is a "
            "ledger whose buckets sum to that side's own total. Alarms lead: "
            "a transposed date passes every count, and a missing month that "
            "another source contradicts is a file worth fetching.</p>"
        ]
        raw_transposed = report.get("transposed")
        if isinstance(raw_transposed, list) and raw_transposed:
            parts.append("<h2>Dates disagree - possible day/month transposition</h2>")
            parts += [
                f'<p class="warn">{html.escape(str(line))}</p>'
                for line in raw_transposed
            ]
        raw_missing = report.get("missing")
        if isinstance(raw_missing, list) and raw_missing:
            parts.append("<h2>Missing - another source has data for these months</h2>")
            parts += [
                f'<p class="warn">{html.escape(str(line))}</p>' for line in raw_missing
            ]
        raw_accounts = report.get("accounts")
        rendered_any = False
        for group in raw_accounts if isinstance(raw_accounts, list) else []:
            if not isinstance(group, dict):
                continue
            parts.append(f"<h2>{html.escape(str(group.get('account')))}</h2>")
            parts.append(_agreements_html(group.get("entries")))
            rendered_any = True
        if not rendered_any:
            parts.append(
                "<p>No account is described by more than one overlapping "
                "source yet - nothing to compare.</p>"
            )
        parts.append(HOME_LINK)
        self._respond(200, render_page("Cross-source agreement", "".join(parts)))

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
        refile_labels: dict[str, str] = {}
        if self.bound_config.display_labels is not None:
            with contextlib.suppress(Exception):
                refile_labels = self.bound_config.display_labels()
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
            + (
                '<form method="post" action="/replay-artefact">'
                f'<input type="hidden" name="id" value="{artefact_id}">'
                '<p><button class="button" type="submit" '
                'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
                "Replay into store</button></p></form>"
            )
            + (
                # The wrong-destination remedy: the payload is evidence, the
                # account_ref is our filing of it, and a mis-tapped picker
                # needed no way back until three statement chunks spent a
                # night deriving 1,571 rows into a Space.
                "<h2>Landed under the wrong account?</h2>"
                '<form method="post" action="/refile-artefact">'
                f'<input type="hidden" name="id" value="{artefact_id}">'
                + account_picker(
                    refile_labels,
                    other_placeholder="or type the correct canonical, "
                    "e.g. starling-personal",
                )
                + '<label style="display:block;margin:.35rem 0">'
                '<input type="checkbox" name="confirm" value="yes" required> '
                "I understand the filing changes and a rebuild re-derives</label>"
                '<p><button class="button" type="submit" '
                'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
                "Refile</button></p></form>"
            )
            + f'<p><a class="button" href="/artefact?id={artefact_id}&view=payload">'
            "View payload</a></p>" + HOME_LINK
        )
        self._respond(200, render_page("Artefact", body))

    def _refile_artefact(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.refile_artefact
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Refiling is not wired.</p>"))
            return
        if form.get("confirm") != ["yes"]:
            self._respond(
                400,
                error_page(
                    "Not confirmed",
                    "<p>Refiling changes which account the artefact's rows "
                    "derive into. Tick the confirmation box to proceed.</p>",
                ),
            )
            return
        try:
            artefact_id = int(form.get("id", ["0"])[0] or "0")
        except ValueError:
            artefact_id = 0
        account = (
            (form.get("account_other", [""])[0] or "").strip()
            or (form.get("account", [""])[0] or "").strip()
        )
        if not artefact_id or not account:
            self._respond(
                400,
                error_page("Bad request", "<p>Artefact id and account required.</p>"),
            )
            return
        old = hook(artefact_id, account)
        if old is None:
            self._respond(404, error_page("Not found", "<p>No such artefact.</p>"))
            return
        self._respond(
            200,
            render_page(
                "Refiled",
                f"<p>Refiled from <strong>{html.escape(old)}</strong> to "
                f"<strong>{html.escape(account)}</strong>. The correction is "
                "recorded in the artefact's provenance.</p>"
                "<p>Now run <strong>Rebuild from raw</strong> (danger zone) so "
                "the derived rows follow the corrected filing.</p>" + HOME_LINK,
            ),
        )

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
        raw_breakdown = shape.get("breakdown")
        breakdown = raw_breakdown if isinstance(raw_breakdown, dict) else {}
        body = (
            f"<p><strong>{heading}</strong>{id_line}{details_html}<br>"
            f"{shape.get('count', 0):,} merged transaction(s) "
            f"from {source_list or 'unknown sources'}</p>"
            "<p>This is the MERGED layer - what the store believes after "
            "matching - not one payload. The raw artefacts remain the "
            "evidence underneath.</p>"
            + _breakdown_html(breakdown)
            + "<h2>Computed shape</h2>"
            + _shape_html(summary)
            + (
                # Where the render cost went, on the page it cost - the
                # 40-second-index lesson: a slow page must name its own
                # hotspot where the person suffering it is already looking.
                '<p class="muted mono">computed: '
                + html.escape(
                    ", ".join(str(entry) for entry in raw_timings)
                    if isinstance(raw_timings := shape.get("timings"), list)
                    else ""
                )
                + "</p>"
                if shape.get("timings")
                else ""
            )
            + HOME_LINK
        )
        self._respond(200, render_page("Account", body))

    def _connect(self, params: dict[str, list[str]]) -> None:
        checker = self.bound_config.update_in_progress
        if checker is not None:
            in_progress = False
            with contextlib.suppress(Exception):
                in_progress = checker()
            if in_progress:
                self._respond(
                    503,
                    error_page(
                        "Stack update in progress",
                        "<p>A stack update is running and could interrupt an "
                        "authorisation mid-SCA - that five-minute window does "
                        "not come back. Try again in a minute.</p>",
                    ),
                )
                return
        take = self.bound_config.auth_lease_take
        if take is not None:
            with contextlib.suppress(Exception):
                take()
        name = (params.get("name", [""])[0] or "").strip()
        # Validated HERE as well as on rename: a namespace policed at one
        # entrance is not policed. Reconnects legitimately reuse an
        # existing name, so the already-taken rule is not applied - only
        # the shape, and the names no connection may ever have.
        try:
            validate_connection_name(name)
        except ValueError as exc:
            self._respond(
                400, error_page("Name required", f"<p>{html.escape(str(exc))}</p>")
            )
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

    def _actual_history(self) -> None:
        hook = self.bound_config.actual_history
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No history wired.</p>"))
            return
        try:
            results = hook()
        except Exception:
            results = []
        body = (
            "<h2>Actual sync history</h2>"
            "<p>Every recorded outcome, newest first - the home page shows "
            "only the latest handful. Times are UTC (marked Z).</p>"
            + "".join(_result_row(result) for result in results)
            + (
                "<p>Nothing recorded yet.</p>"
                if not results
                else f'<p class="muted">{len(results)} result(s)</p>'
            )
            + HOME_LINK
        )
        self._respond(200, render_page("Actual sync history", body))

    def _replay_artefact(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.replay_artefact
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        try:
            artefact_id = int((form.get("id", [""])[0] or "").strip())
        except ValueError:
            self._respond(400, error_page("Bad request", "<p>Artefact id required.</p>"))
            return
        try:
            summary = hook(artefact_id)
        except Exception as exc:
            self._respond(
                400, error_page("Could not replay", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        self._respond(
            200,
            render_page(
                "Artefact replayed",
                f"<p>{html.escape(summary)}</p>"
                "<p>Additive and idempotent: replaying again matches "
                "instead of duplicating. A full rebuild is only for rows "
                "that are wrong, not merely absent.</p>"
                f'<p><a class="button" href="/artefact?id={artefact_id}">'
                "Back to the artefact</a></p>" + HOME_LINK,
            ),
        )

    #: Nothing is stored and nothing is parsed here - the page reads a PDF's
    #: LAYOUT so a parser can be written for a bank's format. Masked output
    #: is the default and the only thing safe to send anywhere.
    STATEMENT_SHAPE_BLURB = (
        "<p>Bank statements carry facts no feed exposes: the terms (rates "
        "by kind, fees, promotional periods and when they revert), the "
        "opening and closing balances, and - for accounts with no feed at "
        "all - the transactions themselves. Writing a parser for a bank's "
        "format needs its LAYOUT, not its contents.</p>"
        "<p>This page reads a PDF and shows its shape with every value "
        "masked: digits become 9s, other words become Xs of the same "
        "length and casing, and spacing, headers and punctuation survive. "
        "The masked output is safe to share; the real contents are not.</p>"
        "<p>The file is KEPT as evidence, before any account is chosen - "
        "the exports worth keeping most are the ones that cannot be "
        "fetched twice. Nothing is imported until it is assigned to an "
        "account.</p>"
    )

    def _statement_shape_form(self, note: str = "") -> None:
        body = (
            "<h2>Statement shape</h2>"
            + self.STATEMENT_SHAPE_BLURB
            + note
            + '<form action="/statement-shape" method="post" '
            'enctype="multipart/form-data">'
            '<p><input type="file" name="file" accept="application/pdf" '
            'multiple required></p>'
            '<p class="muted">Several at once is fine - keeping a statement '
            "asks nothing about it, so a batch carries no more risk than "
            "one.</p>"
            '<p><label><input type="checkbox" name="show_values" value="1"> '
            "Show the REAL contents instead of the masked shape - this "
            "discloses transactions, balances and names</label></p>"
            '<p><button type="submit">Read the shape</button></p>'
            "</form>" + HOME_LINK
        )
        self._respond(200, render_page("Statement shape", body))

    def _statement_shape(self) -> None:

        length = int(self.headers.get("Content-Length") or 0)
        # Generous, because a batch of statements is the ordinary case now
        # and a person who has just chosen a dozen files should not have to
        # discover a limit by tripping over it.
        if length > 200 * 1024 * 1024:
            self._respond(
                413,
                error_page(
                    "Too large",
                    "<p>Even a batch of statements is not this big. Upload "
                    "them in smaller groups.</p>",
                ),
            )
            return
        try:
            uploaded, fields = _parse_multipart_files(
                self.headers.get("Content-Type") or "", self.rfile.read(length)
            )
        except Exception as exc:
            self._respond(
                400,
                error_page(
                    "Could not read the upload", f"<p>{html.escape(str(exc))}</p>"
                ),
            )
            return
        if not uploaded:
            self._statement_shape_form(
                '<p class="alarm">No file was chosen.</p>'
            )
            return

        # This request NEVER discloses, whatever it asked for. Disclosure
        # is a second request carrying a single-use token and a typed
        # phrase, because one request with one extra field is exactly the
        # shape an automated caller produces by accident - and these pages
        # are read programmatically as well as by a person.
        keeper = self.bound_config.keep_statement
        read: list[tuple[str, ShapeReport, int]] = []
        for payload, filename in uploaded:
            shape = self._read_shape(payload, filename, mask=True)
            artefact_id = 0
            if keeper is not None and shape.readable and shape.line_count:
                # Kept BEFORE an account is chosen, because the exports most
                # worth keeping are the ones that cannot be fetched twice.
                artefact_id = keeper(payload, filename)
            read.append((filename, shape, artefact_id))

        payload, filename = uploaded[0]
        shape = read[0][1]
        rows = "".join(
            "<tr><td>"
            + html.escape(name or "(unnamed)")
            + "</td><td>"
            + (
                f'<a href="/statement-shape?artefact={kept_id}">{kept_id}</a>'
                if kept_id
                else '<span class="alarm">not kept</span>'
            )
            + f"</td><td>{report.line_count} line(s), "
            f"{report.page_count} page(s)</td><td>"
            + (
                "kept"
                if kept_id
                else html.escape(report.describe().split(" - ")[0].split(": ", 1)[-1])
            )
            + "</td></tr>"
            for name, report, kept_id in read
        )
        kept_ids = [str(kept_id) for _n, _r, kept_id in read if kept_id]
        summary = (
            f"<p>{len(read)} file(s) read, {len(kept_ids)} kept. Each masked "
            "shape stays at its own address, so it can be read again without "
            "uploading anything again, and assigned to an account later.</p>"
            "<table><tr><th>File</th><th>Statement</th><th>Size</th>"
            f"<th>Outcome</th></tr>{rows}</table>"
            + (
                f'<p class="mono">statements {", ".join(kept_ids)}</p>'
                if len(kept_ids) > 1
                else ""
            )
        )
        body = "<h2>Statement shape</h2>" + summary
        if len(read) == 1:
            # One file, so show its shape here rather than making the
            # person follow a link to the thing they just asked for.
            body += (
                f'<pre class="scroll" style="white-space:pre">'
                f"{html.escape(shape.describe())}</pre>"
            )
        if fields.get("show_values") and shape.readable and shape.line_count:
            token = self.disclosures.stash(payload, filename)
            body += (
                '<h3>Show the real contents?</h3><p class="alarm">This '
                "names payees, amounts and balances. Type "
                f"<strong>{DISCLOSURE_PHRASE}</strong> to confirm - the "
                "phrase and this one-time token are both required, and the "
                "token works once.</p>"
                '<form action="/statement-shape-disclose" method="post">'
                f'<input type="hidden" name="disclose_token" value="{token}">'
                '<p><input type="text" name="confirm" size="24" '
                'autocomplete="off" required></p>'
                '<p><button type="submit">Disclose the real contents</button>'
                "</p></form>"
            )
        body += (
            '<p><a class="button" href="/statement-shape">Read another</a></p>'
            + HOME_LINK
        )
        self._respond(200, render_page("Statement shape", body))

    def _read_shape(
        self, payload: bytes, filename: str, *, mask: bool
    ) -> ShapeReport:
        from .statement_shape import shape_report

        with tempfile.TemporaryDirectory() as scratch:
            # Written to a temporary file because pypdf reads a path, and
            # removed on the way out: this page stores nothing.
            temporary = Path(scratch) / (filename or "statement.pdf")
            temporary.write_bytes(payload)
            return shape_report(temporary, mask=mask, limit=1200)

    def _kept_statement_shape(self, artefact_id: int) -> None:
        """A kept statement's masked shape, at an address that stays put.

        Masked ONLY. A fetchable URL that returned real contents would be a
        standing bypass of the two-step disclosure, and a link that exists
        at all is a link something can follow.
        """
        hook = self.bound_config.statement_payload
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        held = hook(artefact_id)
        if held is None:
            self._respond(
                404,
                error_page("Not found", "<p>No kept statement with that id.</p>"),
            )
            return
        filename, payload = held
        shape = self._read_shape(payload, filename, mask=True)
        body = (
            f"<h2>Statement shape</h2><p class=\"muted\">Kept statement "
            f"{artefact_id}, values masked. This address stays put, so the "
            "shape can be read again without uploading the file again.</p>"
            f'<pre class="scroll" style="white-space:pre">'
            f"{html.escape(shape.describe())}</pre>"
            '<p><a class="button" href="/statement-shape">Read another</a></p>'
            + HOME_LINK
        )
        self._respond(200, render_page("Statement shape", body))

    def _statement_shape_disclose(self) -> None:
        """The second half of the deliberate walk to real contents."""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        fields = {
            key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()
        }
        if fields.get("confirm", "").strip().upper() != DISCLOSURE_PHRASE:
            self._respond(
                400,
                error_page(
                    "Not disclosed",
                    "<p>The confirmation phrase did not match, so nothing was "
                    "shown. Read the shape again if you meant to disclose.</p>"
                    + HOME_LINK,
                ),
            )
            return
        try:
            payload, filename, _ = self.disclosures.claim(
                fields.get("disclose_token", "")
            )
        except KeyError:
            self._respond(
                400,
                error_page(
                    "Not disclosed",
                    "<p>That confirmation is spent or expired - a token works "
                    "once. Read the shape again if you meant to disclose.</p>"
                    + HOME_LINK,
                ),
            )
            return

        shape = self._read_shape(payload, filename, mask=False)
        body = (
            "<h2>Statement contents</h2>"
            '<p class="alarm"><strong>Real values shown.</strong> This output '
            "names payees, amounts and balances - do not paste it anywhere "
            "you would not paste the statement itself.</p>"
            f'<pre class="scroll" style="white-space:pre">'
            f"{html.escape(shape.describe())}</pre>"
            '<p><a class="button" href="/statement-shape">Read another</a></p>'
            + HOME_LINK
        )
        self._respond(200, render_page("Statement contents", body))

    def _review_page(self, note: str = "") -> None:
        """The worklist, with the evidence needed to judge each group.

        Deliberately not a list of transactions: the fastest way to empty a
        thousand-row pile is to answer the ten groups that dominate it, and
        the fastest way to answer one wrongly is to be shown a stripped
        label with no example behind it.
        """
        hook = self.bound_config.categorise_overview
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        try:
            overview = hook()
        except Exception as exc:
            self._respond(
                500, error_page("Worklist failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return

        covered = int(str(overview.get("covered", 0) or 0))
        eligible = int(str(overview.get("eligible", 0) or 0))
        legs = int(str(overview.get("transfer_legs", 0) or 0))
        share = f" ({covered / eligible:.0%})" if eligible else ""
        rows = []
        groups = overview.get("groups")
        for group in groups if isinstance(groups, list) else []:
            label = str(group.get("label", ""))
            example = str(group.get("example", ""))
            marks = []
            distinct = int(group.get("distinct", 0) or 0)
            if distinct > 1:
                marks.append(f"{distinct} distinct strings")
            if group.get("reference_coded") and group.get("repeating"):
                marks.append(
                    "opaque reference, but it repeats - identify it once, "
                    "then it can be answered exactly"
                )
            elif group.get("reference_coded"):
                marks.append(
                    "reference codes rather than a payee - an answer here "
                    "would be a guess"
                )
            rows.append(
                "<tr><td class=\"mono\">"
                + html.escape(label)
                + (
                    f'<br><span class="muted mono">{html.escape(example)}</span>'
                    if example and example != label
                    else ""
                )
                + (
                    f'<br><span class="muted">{html.escape("; ".join(marks))}</span>'
                    if marks
                    else ""
                )
                + f"</td><td>{group.get('count', 0)}</td><td>"
                + '<form action="/review-apply" method="post">'
                + f'<input type="hidden" name="label" value="{html.escape(label)}">'
                + '<input type="text" name="value" size="28" '
                'placeholder="Group: Leaf" autocomplete="off" required>'
                + '<button type="submit">Answer all</button>'
                + "</form>"
                + '<form action="/review-defer" method="post">'
                + f'<input type="hidden" name="label" value="{html.escape(label)}">'
                + '<button type="submit">Cannot decide yet</button>'
                + "</form>"
                + (
                    f'<span class="muted">{group.get("deferred", 0)} already '
                    "set aside</span>"
                    if int(str(group.get("deferred", 0) or 0))
                    else ""
                )
                + "</td></tr>"
            )

        body = (
            "<h2>Categorise</h2>"
            + note
            + f"<p>{covered} of {eligible} eligible transaction(s) carry a "
            f"category{share}. {legs} confirmed transfer leg(s) are excluded - "
            "money that stayed in the household is not spending.</p>"
            "<p>Answering a group here writes at HUMAN rank: it outranks "
            "every later rule sweep and survives every rebuild. Groups with "
            "no obvious answer are better left alone than guessed - the "
            "shape of a payment identifies it when the string does not.</p>"
            + (
                "<table><tr><th>Group</th><th>Rows</th><th>Answer</th></tr>"
                + "".join(rows)
                + "</table>"
                if rows
                else "<p>Nothing uncategorised.</p>"
            )
            + HOME_LINK
        )
        self._respond(200, render_page("Categorise", body))

    def _review_defer(self) -> None:
        hook = self.bound_config.categorise_defer
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        fields = {
            key: values[0]
            for key, values in parse_qs(raw, keep_blank_values=True).items()
        }
        label = fields.get("label", "").strip()
        if not label:
            self._review_page('<p class="alarm">No group was named.</p>')
            return
        try:
            marked = hook(label)
        except Exception as exc:
            self._respond(
                500, error_page("Could not defer", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        self._review_page(
            f'<p class="ok">Set aside {marked} row(s) in '
            f"{html.escape(label)} - recorded as looked at and undecided, "
            "and still listed.</p>"
        )

    def _review_apply(self) -> None:
        hook = self.bound_config.categorise_apply
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        fields = {
            key: values[0]
            for key, values in parse_qs(raw, keep_blank_values=True).items()
        }
        label = fields.get("label", "").strip()
        value = fields.get("value", "").strip()
        if not label or not value:
            self._review_page(
                '<p class="alarm">Nothing was answered - a group and a '
                "category are both needed.</p>"
            )
            return
        try:
            written = hook(label, value, "category")
        except Exception as exc:
            self._respond(
                500, error_page("Could not answer", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        self._review_page(
            f'<p class="ok">Answered {written} row(s) in '
            f"{html.escape(label)} as {html.escape(value)}.</p>"
        )

    def _review_report(self) -> None:
        hook = self.bound_config.review_report_text
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No report wired.</p>"))
            return
        try:
            text = hook()
        except Exception as exc:
            self._respond(
                500, error_page("Report failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        body = (
            "<h2>Review queue report</h2>"
            "<p>The queue decomposed - flag reasons, largest clusters, and "
            "how many flags match a declared standing order or direct "
            "debit. The raw material for calibrating the matcher.</p>"
            f'<pre class="scroll" style="white-space:pre-wrap">'
            f"{html.escape(text)}</pre>" + HOME_LINK
        )
        self._respond(200, render_page("Review queue report", body))

    def _date_lag(self) -> None:
        hook = self.bound_config.date_lag_text
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No report wired.</p>"))
            return
        try:
            text = hook()
        except Exception as exc:
            self._respond(
                500, error_page("Report failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        body = (
            "<h2>Settlement lag</h2>"
            "<p>Starling reports both when a payment happened and when it "
            "settled - the truth set for how often dates drift, and how "
            "often the drift would file a payment into the wrong week or "
            "month. Measurement before mechanism: this page decides "
            "whether a date-override layer earns building.</p>"
            f'<pre class="scroll" style="white-space:pre-wrap">'
            f"{html.escape(text)}</pre>" + HOME_LINK
        )
        self._respond(200, render_page("Settlement lag", body))

    def _balance_walk(self) -> None:
        hook = self.bound_config.balance_walk_text
        if hook is None:
            self._respond(404, error_page("Not available", "<p>No report wired.</p>"))
            return
        try:
            text = hook()
        except Exception as exc:
            self._respond(
                500, error_page("Report failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        body = (
            "<h2>Balance walk</h2>"
            "<p>TrueLayer reports the account's running balance on each "
            "transaction. Consecutive balances must differ by exactly the "
            "amounts in between - a break means money moved that no held "
            "transaction explains. This is the store checked against the "
            "bank's own arithmetic.</p>"
            f'<pre class="scroll" style="white-space:pre-wrap">'
            f"{html.escape(text)}</pre>" + HOME_LINK
        )
        self._respond(200, render_page("Balance walk", body))

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        return parse_qs(self.rfile.read(length).decode("utf-8"))

    def _is_cross_site(self) -> bool:
        """Is this POST being driven by a page on somebody else's site?

        Origin is the header browsers attach to cross-site form
        submissions, so a value that disagrees with the Host we were
        asked on is a forgery attempt by definition. ABSENCE is treated
        as trustworthy on purpose: only a browser can be tricked into
        submitting somebody else's form, and a client that sends no
        Origin is not one - so the CLI and deliberate automation keep
        working, as a decision rather than an oversight.
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return False
        host = (self.headers.get("Host") or "").strip().casefold()
        return urlparse(origin).netloc.casefold() != host

    def do_POST(self) -> None:
        began = time.perf_counter()
        route = urlparse(self.path).path.rstrip("/") or "/"
        try:
            self._dispatch_post()
        finally:
            _report_slow_route("POST", route, time.perf_counter() - began)

    def _dispatch_post(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        if self._is_cross_site():
            # Refused before the body is read, so a forged request cannot
            # even consume the request stream.
            self._respond(
                403,
                error_page(
                    "Refused",
                    "<p>This request came from a page on another site. "
                    "Actions that change anything are accepted only from "
                    "obdi's own pages.</p>",
                ),
            )
            return
        if route == "/statement-shape":
            self._statement_shape()
            return
        if route == "/statement-shape-disclose":
            self._statement_shape_disclose()
            return
        if route == "/review-apply":
            self._review_apply()
            return
        if route == "/review-defer":
            self._review_defer()
            return

        if route == "/upload":
            self._upload()
            return
        if route == "/upload-confirm":
            self._upload_confirm(parse_qs(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)
            ).decode("utf-8")))
            return
        if route == "/starling-probe":
            self._starling_probe(parse_qs(self.rfile.read(
                int(self.headers.get("Content-Length") or 0)
            ).decode("utf-8")))
            return
        if route == "/push-actual":
            self._push_actual()
            return
        if route == "/audit-actual":
            self._audit_actual()
            return
        if route == "/prune-actual":
            self._prune_actual(self._read_form())
            return
        if route == "/rename-connection":
            self._rename_connection(self._read_form())
            return
        if route == "/rebuild-derived":
            self._rebuild_derived(self._read_form())
            return
        if route == "/forget-actual-bindings":
            self._forget_actual(self._read_form())
            return
        if route == "/replay-artefact":
            self._replay_artefact(self._read_form())
            return
        if route == "/refile-artefact":
            self._refile_artefact(self._read_form())
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
            payload, filename, fields = _parse_multipart(
                self.headers.get("Content-Type") or "", self.rfile.read(length)
            )
        except Exception as exc:
            self._respond(
                400, error_page("Could not read the file", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        # Destination FIRST: the preview verifies the file against what
        # this account already holds, which is impossible to do after the
        # fact - and it makes the confirm page a single honest button.
        account = (fields.get("account_other") or fields.get("account") or "").strip()
        if not account:
            self._respond(
                400,
                error_page(
                    "Choose the destination first",
                    "<p>Pick the account this statement belongs to (or type a "
                    "canonical name) before uploading - the preview verifies "
                    "the file against what that account already holds.</p>",
                ),
            )
            return
        try:
            preview = hook(payload, filename, account)
        except Exception as exc:
            self._respond(
                400, error_page("Could not read the file", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        doubt_messages = []
        for key in ("destination_doubt", "lifecycle_doubt"):
            raw_doubt = preview.get(key)
            if isinstance(raw_doubt, dict) and raw_doubt.get("message"):
                doubt_messages.append(str(raw_doubt.get("message")))
        doubt_message = " ALSO: ".join(doubt_messages)
        token = self.uploads.stash(payload, filename, doubted=bool(doubt_message))
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
        raw_verdicts = preview.get("verdicts")
        verdict_rows = ""
        for v in raw_verdicts if isinstance(raw_verdicts, list) else []:
            if not isinstance(v, dict):
                continue
            ok = v.get("ok")
            mark, style = (
                ("PASS", "") if ok is True
                else ("FAIL", ' class="warn"') if ok is False
                else ("n/a", ' class="muted"')
            )
            verdict_rows += (
                f"<tr{style}><td>{html.escape(str(v.get('name')))}</td>"
                f"<td>{mark}</td>"
                f"<td>{html.escape(str(v.get('detail')))}</td></tr>"
            )
        verdicts_html = (
            "<h2>Should this parse be believed?</h2>"
            '<div class="scroll"><table><tr><th>check</th><th></th>'
            f"<th>evidence</th></tr>{verdict_rows}</table></div>"
            if verdict_rows
            else ""
        )
        raw_agreements = preview.get("agreement_preview")
        agreement_html = ""
        rendered_agreements = _agreements_html(raw_agreements)
        if rendered_agreements:
            agreement_html = (
                f"<h2>Against what {html.escape(account)} already holds</h2>"
                + rendered_agreements
            )
        body = (
            f"<p><strong>{html.escape(filename)}</strong> parsed as "
            f"{html.escape(str(preview.get('parser')))} "
            f"(dates {html.escape(str(preview.get('date_format')))}): "
            f"{preview.get('rows')} row(s), "
            f"{preview.get('earliest')} .. {preview.get('latest')}</p>"
            + (
                (
                    f'<p class="warn">{html.escape(str(preview.get("claimed_window")))}</p>'
                    if "OUTSIDE" in str(preview.get("claimed_window"))
                    else f'<p class="muted">{html.escape(str(preview.get("claimed_window")))}</p>'
                )
                if preview.get("claimed_window")
                else ""
            )
            + warning
            + verdicts_html
            + agreement_html
            + '<div class="scroll"><table><tr><th>date</th><th>amount</th>'
            f"<th>description</th></tr>{sample_rows}</table></div>"
            + (
                # The wrong-destination guard, prevention half of the misfile
                # that put three statement chunks in a Space: the doubt rides
                # the stash, so the override below is enforced server-side at
                # confirm - a checkbox the browser merely requires is
                # decoration on a phone at 22:51.
                '<h2 class="bad">Wrong destination?</h2>'
                f'<p class="warn">{html.escape(doubt_message)}</p>'
                if doubt_message
                else ""
            )
            + f"<p>Nothing has been stored yet. Confirm to import into "
            f"<strong>{html.escape(account)}</strong>.</p>"
            '<form method="post" action="/upload-confirm">'
            f'<input type="hidden" name="token" value="{token}">'
            f'<input type="hidden" name="account" value="{html.escape(account)}">'
            + (
                '<label style="display:block;margin:.35rem 0">'
                '<input type="checkbox" name="override" value="yes" required> '
                "Import here anyway - I have checked the destination</label>"
                if doubt_message
                else ""
            )
            + '<p><button class="button" type="submit" '
            'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
            f"Import into {html.escape(account)}</button></p></form>" + HOME_LINK
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
            payload, filename, doubted = self.uploads.claim(token)
        except KeyError as exc:
            self._respond(410, error_page("Upload expired", f"<p>{html.escape(str(exc))}</p>"))
            return
        if doubted and form.get("override") != ["yes"]:
            # The claim consumed the stash, so hand back a fresh token with
            # the override stated plainly - refusal must not cost the person
            # their upload.
            fresh = self.uploads.stash(payload, filename, doubted=True)
            self._respond(
                409,
                render_page(
                    "Destination doubted",
                    "<p>The preview doubted this destination: most of this "
                    "file's rows match rows another source filed under a "
                    "DIFFERENT account. Import only if you have checked "
                    "the destination is right.</p>"
                    '<form method="post" action="/upload-confirm">'
                    f'<input type="hidden" name="token" value="{fresh}">'
                    f'<input type="hidden" name="account" value="{html.escape(account)}">'
                    '<label style="display:block;margin:.35rem 0">'
                    '<input type="checkbox" name="override" value="yes" required> '
                    "Import here anyway - I have checked the destination</label>"
                    '<p><button class="button" type="submit" '
                    'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
                    f"Import into {html.escape(account)}</button></p></form>" + HOME_LINK,
                ),
            )
            return
        try:
            summary = hook(payload, filename, account)
        except Exception as exc:
            self._respond(
                500, error_page("Import failed", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        print(f"web import: {filename} -> {account}", file=sys.stderr)
        if isinstance(summary, dict):
            summary_html = (
                f"<p>{html.escape(str(summary.get('summary')))}</p>"
                + _agreements_html(summary.get("agreements"))
            )
        else:
            summary_html = "".join(
                f"<p>{html.escape(line)}</p>" for line in summary.splitlines()
            )
        # A statement-chunk session imports many files into ONE account;
        # sending the person back to the homepage to re-scroll and re-pick
        # the same destination each time taxes exactly the workflow the
        # door exists for. The destination is already known - only the
        # file is new - and the pre-filled form still routes through the
        # same preview, so destination-first verification is preserved.
        another = (
            f"<h2>Import another into {html.escape(account)}</h2>"
            '<form action="/upload" method="post" enctype="multipart/form-data">'
            f'<input type="hidden" name="account" value="{html.escape(account)}">'
            '<p><input type="file" name="statement" required></p>'
            '<p><button class="button" type="submit" '
            'style="border:0;width:100%;font-size:inherit;cursor:pointer">'
            "Preview import</button></p></form>"
        )
        self._respond(
            200,
            render_page("Imported", summary_html + another + HOME_LINK),
        )

    def _starling_probe(self, params: dict[str, list[str]]) -> None:
        """Run the changesSince experiment and say what one response proves."""
        probe = self.bound_config.starling_probe
        if probe is None:
            self._respond(
                503,
                error_page(
                    "Probe unavailable",
                    "<p>The web process has no Starling token configured.</p>",
                ),
            )
            return
        cutoff = (params.get("cutoff") or [""])[0].strip()
        if not cutoff:
            self._respond(
                400,
                error_page("Missing cutoff", "<p>Choose or enter a cutoff.</p>"),
            )
            return
        try:
            report = probe(cutoff)
        except ValueError as exc:
            self._respond(
                400, error_page("Bad cutoff", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        except Exception as exc:
            self._respond(
                502,
                error_page(
                    "Probe failed", f"<p>{html.escape(str(exc))}</p>"
                ),
            )
            return
        print(f"starling probe via page: cutoff={cutoff}", file=sys.stderr)
        self._respond(200, render_page("changesSince probe", _probe_result_html(report)))

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

    def _audit_actual(self) -> None:
        hook = self.bound_config.audit_actual
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Audit is not wired.</p>"))
            return
        try:
            summary = hook()
        except Exception as exc:
            self._respond(500, error_page("Could not queue", f"<p>{html.escape(str(exc))}</p>"))
            return
        print(f"actual audit queued via page: {summary}", file=sys.stderr)
        self._respond(
            200,
            render_page(
                "Audit queued",
                f"<p>{html.escape(summary)}</p>"
                "<p>Read-only: the applier reads each bound account back "
                "from Actual and reports differences in the Actual sync "
                "section - nothing is changed on either side.</p>" + HOME_LINK,
            ),
        )

    def _rename_connection(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.rename_connection
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        old_name = (form.get("old_name", [""])[0] or "").strip()
        new_name = (form.get("new_name", [""])[0] or "").strip()
        if not old_name or not new_name:
            self._respond(
                400,
                error_page("Name required", "<p>Both names are needed.</p>"),
            )
            return
        try:
            summary = hook(old_name, new_name)
        except Exception as exc:
            self._respond(
                400, error_page("Could not rename", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        self._respond(
            200,
            render_page(
                "Connection renamed",
                f"<p>{html.escape(summary)}</p>"
                "<p>The bank was not contacted: a name is obdi's label for a "
                "connection, so renaming moves the label and leaves every "
                "payload, digest and account reference exactly as landed.</p>"
                + HOME_LINK,
            ),
        )

    def _prune_actual(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.prune_actual
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        if form.get("confirm") != ["yes"]:
            self._respond(
                400,
                error_page(
                    "Not confirmed",
                    "<p>Pruning deletes rows from Actual (only ones carrying "
                    "obdi's imported ids). Tick the confirmation box.</p>",
                ),
            )
            return
        try:
            summary = hook()
        except Exception as exc:
            self._respond(
                500, error_page("Could not queue", f"<p>{html.escape(str(exc))}</p>")
            )
            return
        self._respond(
            200,
            render_page(
                "Prune queued",
                f"<p>{html.escape(summary)}</p>"
                "<p>Only rows whose imported id has obdi's own shape are "
                "ever considered - rows without an imported id, and rows "
                "imported by Actual itself (file imports, bank sync), are "
                "never touched. An account with an empty expected set is "
                "skipped rather than pruned blind. Results appear in the "
                "Actual sync section.</p>" + HOME_LINK,
            ),
        )

    def _rebuild_derived(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.rebuild_derived
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Rebuild is not wired.</p>"))
            return
        if form.get("confirm") != ["yes"]:
            self._respond(
                400,
                error_page(
                    "Not confirmed",
                    "<p>Rebuilding wipes the derived transaction layer. Tick "
                    "the confirmation box to proceed.</p>",
                ),
            )
            return
        try:
            summary = hook()
        except Exception as exc:
            message = str(exc)
            if "database is locked" in message:
                message = (
                    "the store is busy (a scheduled pull is writing) - "
                    "try again in a moment"
                )
            self._respond(500, error_page("Rebuild failed", f"<p>{html.escape(message)}</p>"))
            return
        print(f"rebuild via page: {summary}", file=sys.stderr)
        self._respond(
            200,
            render_page(
                "Rebuild",
                f"<p>{html.escape(summary)}</p>"
                "<p>Raw artefacts are untouched; every derived row is "
                "replayed through the current account map and rules.</p>" + HOME_LINK,
            ),
        )

    def _forget_actual(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.forget_actual
        if hook is None:
            self._respond(404, error_page("Not available", "<p>Not wired.</p>"))
            return
        if form.get("confirm") != ["yes"]:
            self._respond(
                400,
                error_page(
                    "Not confirmed",
                    "<p>Tick the confirmation box to drop the Actual account "
                    "links.</p>",
                ),
            )
            return
        try:
            count = hook()
        except Exception as exc:
            self._respond(500, error_page("Could not forget", f"<p>{html.escape(str(exc))}</p>"))
            return
        self._respond(
            200,
            render_page(
                "Actual links forgotten",
                f"<p>Dropped {count} link(s). The account names are kept; "
                "the next push will re-provision by name - existing "
                "same-named accounts in Actual are reused, and imports "
                "dedupe by imported id.</p>" + HOME_LINK,
            ),
        )

    def _bind(self, form: dict[str, list[str]]) -> None:
        """Name an account from the page: the map entry plus label moves.

        Mutating, so POST only. The result page is just the confirmation -
        repeating the extend section here made a naming action look like a
        fetching context, and the home page is one tap away.
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
                f"<p>{html.escape(summary)}</p>" + HOME_LINK,
            ),
        )

    def _requester_address(self) -> str | None:
        """The pressing device's address: forwarded first, never loopback."""
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        peer = self.client_address[0]
        return forwarded or (peer if not peer.startswith("127.") else None)

    def _callback(self, params: dict[str, list[str]]) -> None:
        release = self.bound_config.auth_lease_release
        if release is not None:
            with contextlib.suppress(Exception):
                release()
        error = params.get("error", [""])[0]
        if error:
            described = params.get("error_description", [""])[0]
            # The name is only knowable through the state we minted, and a
            # failed journey may not carry it back - peek without consuming,
            # so a retry with the same state still works.
            name = ""
            with contextlib.suppress(Exception):
                name = self.bound_session.peek(params.get("state", [""])[0])
            recorder = self.bound_config.record_auth_failure
            if recorder is not None:
                with contextlib.suppress(Exception):
                    recorder(name, error, described)
            self._respond(
                400,
                error_page(
                    "Authorisation failed",
                    _auth_failure_body(name, error, described),
                ),
            )
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
            "<p>Fetching your history now, then walking every account and "
            "card back as far as this provider allows - this is the one "
            "moment deep history is reachable, so all of it starts "
            "automatically. It runs in the background; check back shortly.</p>"
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
