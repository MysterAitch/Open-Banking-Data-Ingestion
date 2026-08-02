"""Pull accounts and transactions from TrueLayer into the canonical store.

Two hazards shape this module.

**Floats at the JSON boundary.** TrueLayer returns `amount` as a JSON number,
and the standard decoder turns 14.99 into a float that is not exactly 14.99.
Every amount here is decoded with `parse_float=Decimal` and converted through a
string, so no float ever touches a stored amount. This is not fastidiousness:
sub-penny drift is a documented cause of transactions failing to match between
two sightings.

**Overlapping windows.** A community integration reports that the `from`
parameter is not always honoured and the full history comes back regardless.
That is survivable precisely because identity resolution is already mandatory -
re-fetching the same transaction must be, and is, harmless.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from ..identity import artefact_digest, content_key
from ..jsontypes import JsonObject, as_object, rows, text
from ..models import RawArtefact, SourceTier, Transaction, TransactionStatus
from ..money import parse_amount

AUTH_HOST = "https://auth.truelayer.com"
API_HOST = "https://api.truelayer.com"

# First connect is the one chance at deep history: the extra authentication
# that unlocks more than ninety days only happens at authorisation time.
#
# 730 days is not an arbitrary number - it is 24 months, the maximum a UK bank
# is REQUIRED to provide under the Open Banking rules. Anything older is at the
# bank's discretion, and most decline.
#
# But "required to provide 24 months" is not "will refuse more", and the cost of
# not asking is permanent: history you did not fetch during the post-SCA window
# is gone. So ask wide and fall back rather than assume either way. A provider
# that clamps silently returns what it has; one that rejects the range gets
# retried narrower, instead of the whole backfill failing and taking the
# irreplaceable window with it.
# Top rung reaches back past 1970. Not because any UK current account has
# transactions that old, but because "ask for everything" is only risky if the
# provider REJECTS it - and the rung below catches that. Providers behave in
# three ways here and only one of them is documented anywhere:
#
#   clamp     returns what exists and ignores the rest - asking wide is free
#   reject    a 4xx on the range - the next rung down handles it
#   cap span  limits how much may be requested at ONCE, needing pagination
#
# The third is the dangerous one, because narrowing "succeeds" and quietly
# returns a fraction of what was there. That cannot be distinguished from a
# genuinely short history without saying which window was actually satisfied,
# which is why falling back is reported rather than silent.
BACKFILL_LADDER_DAYS = (20000, 3650, 730, 90)
DEFAULT_BACKFILL_DAYS = BACKFILL_LADDER_DAYS[1]

# Routine pulls ask for ninety days, and this is regulation rather than a
# tuning choice: SCA-RTS limits unattended account access to ninety days of
# history, anything older needing the authentication that only happens at
# connection time. It is also self-healing - a missed cycle is covered by the
# next pull's window - and it is safe against the behaviour the first real
# bank demonstrated: rejecting wide ranges outright, which would have failed
# every scheduled pull forever had they kept asking for years.
ROUTINE_WINDOW_DAYS = 90


def backfill_ladder() -> tuple[int, ...]:
    """Windows to try, widest first. `OBDI_BACKFILL_DAYS` prepends an override.

    Configurable because the real ceiling is per-bank and undocumented: the only
    way to find out what one will serve is to ask it, and that should not need a
    code change.
    """
    override = os.getenv("OBDI_BACKFILL_DAYS", "").strip()
    if override.isdigit() and int(override) > 0:
        return (int(override), *BACKFILL_LADDER_DAYS)
    return BACKFILL_LADDER_DAYS


class TrueLayerError(RuntimeError):
    """A provider call failed in a way worth surfacing rather than retrying.

    Carries the provider's error body in parts - machine code, human prose,
    upstream provider detail - because the parts have different audiences.
    Blurring them into one string is how "SCA exemption has expired" once hid
    inside a truncated JSON blob on a phone screen.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str = "",
        description: str = "",
        provider_details: str = "",
        raw: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.description = description
        self.provider_details = provider_details
        self.raw = raw
        #: The response headers worth keeping from a refusal: Retry-After is
        #: the provider stating its own cooldown, the correlation id is what
        #: their support asks for, Date anchors clock disputes.
        self.headers = headers or {}


def _refusal(prefix: str, response: httpx.Response) -> TrueLayerError:
    """Parse a refusal into its parts, falling back to the raw excerpt."""
    code = description = details = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        code = str(body.get("error") or "")
        description = str(body.get("error_description") or "")
        raw_details = body.get("error_details")
        if isinstance(raw_details, dict):
            details = str(raw_details.get("provider_details") or "")
    summary = " - ".join(part for part in (code, description) if part) or response.text[:300]
    kept = {
        name: response.headers[name]
        for name in ("retry-after", "tl-correlation-id", "x-correlation-id", "date")
        if name in response.headers
    }
    return TrueLayerError(
        f"{prefix} (HTTP {response.status_code}): {summary}",
        status=response.status_code,
        code=code,
        description=description,
        provider_details=details,
        raw=response.text[:4000],
        headers=kept,
    )


def build_auth_link(
    *,
    client_id: str,
    redirect_uri: str,
    state: str = "",
    providers: str = "uk-ob-all uk-oauth-all",
    scopes: str = "info accounts balance cards transactions offline_access",
) -> str:
    """The URL a person opens to authorise a bank.

    `offline_access` is not optional in practice: without it no refresh token
    is issued, and every sync would need re-authorising by hand.
    """
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "providers": providers,
            **({"state": state} if state else {}),
        }
    )
    return f"{AUTH_HOST}/?{query}"


def exchange_code(
    *, code: str, client_id: str, client_secret: str, redirect_uri: str
) -> JsonObject:
    """Swap a single-use authorisation code for tokens."""
    response = httpx.post(
        f"{AUTH_HOST}/connect/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30.0,
    )
    if response.status_code != 200:
        # The provider's error body is the diagnosis, so it travels with the
        # failure: invalid_client means the secret is wrong, invalid_grant a
        # spent or expired code, invalid_redirect_uri a registration mismatch.
        # Each has a different next step, and swallowing the body collapses
        # them into one unanswerable page - read on a phone, mid-flow, with
        # the single-use code already burnt. The body carries error codes,
        # never credentials, so surfacing it leaks nothing.
        raise _refusal("Code exchange failed", response)
    return decode(response.text)


def decode(payload: bytes | str) -> JsonObject:
    """Decode provider JSON without letting floats near an amount."""
    body = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    return as_object(json.loads(body, parse_float=Decimal), field="response")


def refresh_access_token(
    *, refresh_token: str, client_id: str, client_secret: str, client: httpx.Client | None = None
) -> JsonObject:
    """Exchange a refresh token for a new access token.

    Note what this does NOT do: extend consent. That clock is independent and
    keeps running.
    """
    http = client or httpx.Client(timeout=30.0)
    response = http.post(
        f"{AUTH_HOST}/connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )
    if response.status_code != 200:
        raise TrueLayerError(
            f"Token refresh failed (HTTP {response.status_code}). If consent has "
            f"expired this cannot be recovered without re-authorising at the bank."
        )
    return decode(response.text)


def _headers(access_token: str, psu_ip: str | None = None) -> dict[str, str]:
    """Auth, plus the attended-access declaration when it is TRUE.

    X-PSU-IP asserts the customer actively requested this access, which is the
    regulation's exemption from the four-per-day unattended cap. It is a
    statement of fact, not a knob: it is sent only when a human drove the
    request and their address is known, and the scheduler - genuinely
    unattended - never sends it. Declaring presence that did not happen would
    be lying to a regulated counterparty about the one thing the rule exists
    to know.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    if psu_ip:
        headers["X-PSU-IP"] = psu_ip
    return headers


def fetch_accounts(
    access_token: str, *, psu_ip: str | None = None, client: httpx.Client | None = None
) -> tuple[list[JsonObject], bytes]:
    """Accounts plus the raw body, which lands like any other payload.

    The body carries the display names and account types the provider sends -
    exactly what a person needs to tell three opaque account ids apart when
    binding them to canonical accounts, and what was previously discarded.
    """
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}/data/v1/accounts",
        headers=_headers(access_token, psu_ip),
    )
    if response.status_code != 200:
        raise _refusal("Account fetch failed", response)
    return rows(decode(response.content), "results"), response.content


def fetch_transactions(
    access_token: str,
    account_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
    pending: bool = False,
    deep: bool = False,
    known_ceiling_days: int | None = None,
    psu_ip: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[JsonObject], bytes, str]:
    """Return the transactions, the raw body, and the range actually requested.

    The third element is what lets the landed artefact record what was ASKED
    for, not merely what came back. Without it an empty result is unreadable
    forever: a dormant account and a too-narrow window are the same bytes.

    The raw bytes are returned alongside deliberately: landing the verbatim
    payload is what makes the derived tables rebuildable later.
    """
    http = client or httpx.Client(timeout=30.0)
    suffix = "/pending" if pending else ""
    url = f"{API_HOST}/data/v1/accounts/{account_id}/transactions{suffix}"
    headers = _headers(access_token, psu_ip)

    if pending:
        response = http.get(url, headers=headers)
        if response.status_code != 200:
            raise _refusal("Transaction fetch failed", response)
        body = response.content
        # The same status guard as the booked path. Both endpoints share the
        # response envelope, and a Queued/Running pending payload stored at
        # face value records partial results as the truth - the guard existing
        # on only one of two sibling paths was found by review, with the test
        # suite encoding the same asymmetry.
        payload_status = text(decode(body), "status")
        if payload_status and payload_status.casefold() not in ("succeeded", "ok"):
            raise TrueLayerError(
                f"Pending fetch returned status '{payload_status}' for account "
                f"{account_id}: the results are not final and were NOT stored."
            )
        return rows(decode(body), "results"), body, ""

    # The ladder costs one API call per rung, and the provider documents a limit
    # of FOUR calls per day per account unless the end user's IP is supplied to
    # show they are present. Walking four rungs on a routine pull would spend a
    # whole day's quota on a single account, so it is reserved for the one
    # occasion that justifies it: the post-authorisation backfill, which happens
    # once and cannot be repeated.
    #
    # An explicit `since` is an instruction rather than a preference, so it is
    # used as given either way.
    if since:
        windows: tuple[int | None, ...] = (None,)
    elif deep and known_ceiling_days:
        # What the provider refused yesterday, do not ask again tomorrow: a
        # recorded ceiling starts the ladder AT the known-good rung, so a
        # reconnection spends one call where discovery spent three. It is a
        # starting point rather than a dead end - the narrower rungs remain
        # beneath it in case the provider has tightened since.
        windows = (
            known_ceiling_days,
            *(rung for rung in backfill_ladder() if rung < known_ceiling_days),
        )
    elif deep:
        windows = backfill_ladder()
    else:
        windows = (ROUTINE_WINDOW_DAYS,)
    last_response: httpx.Response | None = None
    for days in windows:
        earliest = since or datetime.now(UTC).date() - timedelta(days=days or 0)
        params = {
            "from": earliest.isoformat(),
            "to": (until or datetime.now(UTC).date()).isoformat(),
        }
        response = http.get(url, headers=headers, params=params)
        if response.status_code == 200:
            # The payload carries its own status alongside the results, and a
            # non-final one means the results are not the whole answer. Taking
            # them at face value would record "this account has no transactions"
            # for data that had simply not arrived - indistinguishable from a
            # genuinely dormant account, and wrong in the one direction that
            # cannot be corrected later.
            payload_status = text(decode(response.content), "status")
            if payload_status and payload_status.casefold() not in ("succeeded", "ok"):
                raise TrueLayerError(
                    f"Transaction fetch returned status '{payload_status}' for account "
                    f"{account_id}: the results are not final and were NOT stored. "
                    "Retry rather than treating this as an empty account."
                )
            if days != windows[0]:
                # Say when less was fetched than was asked for. A provider that
                # caps the span per request makes a narrowed window look like a
                # success, so without this the difference between "this account
                # is young" and "we silently took a fraction" is invisible - and
                # only discoverable once the missing years are unrecoverable.
                refused = [str(w) for w in windows[: windows.index(days)]]
                print(
                    f"backfill narrowed to {days} days for account {account_id}: "
                    f"the provider refused {', '.join(refused)}. If this account "
                    "is older, some history was NOT fetched.",
                    file=sys.stderr,
                )
            body = response.content
            return rows(decode(body), "results"), body, urlencode(sorted(params.items()))
        last_response = response
        if response.status_code == 429:
            # Narrowing cannot help, and each further attempt digs the hole
            # deeper against a quota that resets daily rather than in seconds.
            raise TrueLayerError(
                f"Rate limited by the provider for account {account_id}. Unattended "
                "access is capped at four calls per day per account; supplying the "
                "end user's IP address lifts that, but only while they are present."
            )
        # Only a rejected RANGE is worth narrowing for. A 401 means the token is
        # wrong and every rung will fail identically; retrying it three times
        # just spends the post-authorisation window on the same error.
        if response.status_code not in (400, 416, 422):
            break

    # The body is the diagnosis: a 403 alone cannot distinguish a scope
    # problem from an expired SCA exemption from a refused window, and each
    # has a different next step. Parsed into parts so no display has to.
    if last_response is None:
        raise TrueLayerError("Transaction fetch failed: no window was attempted")
    raise _refusal("Transaction fetch failed", last_response)


def to_transaction(
    record: JsonObject, *, account_id: str, pending: bool = False
) -> Transaction:
    """Map one provider record onto the canonical model.

    The amount arrives signed. Where `transaction_type` is also present the two
    are cross-checked, because a sign convention that silently flips is the
    single most damaging thing that can happen to this data.
    """
    raw_amount = record.get("amount")
    if raw_amount is None:
        raise TrueLayerError("transaction has no amount")

    # The record's own currency, not an assumed one. Parsing every amount as
    # sterling routes around money.py's guard while still storing the true
    # currency alongside, leaving the figure and its label disagreeing in
    # silence.
    currency = text(record, "currency", default="GBP")
    amount_minor = parse_amount(str(raw_amount), currency=currency)

    transaction_type = text(record, "transaction_type").upper()
    if transaction_type == "DEBIT" and amount_minor > 0:
        amount_minor = -amount_minor
    elif transaction_type == "CREDIT" and amount_minor < 0:
        raise TrueLayerError(
            f"transaction {text(record, 'transaction_id')} is typed CREDIT but carries a "
            "negative amount; refusing to guess the sign convention"
        )

    timestamp = text(record, "timestamp")
    if not timestamp:
        raise TrueLayerError("transaction has no timestamp")
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()

    description = text(record, "description")
    merchant = text(record, "merchant_name").strip()

    # `transaction_id` is deliberately NOT used, despite the name. The provider
    # documents it as "it may change between requests", and an identifier that
    # changes between fetches is the one thing tier one must never rest on: it
    # would assert "the source says these are the same payment" on no evidence,
    # and a re-fetch under a new id reads as a second payment. The damage is
    # asymmetric - a missed match duplicates real money in the copy that is
    # meant to be authoritative, and nothing downstream can tell.
    #
    # `normalised_provider_transaction_id` is the one the provider states will
    # NOT change, so it is the only value here worth treating as durable.
    durable_id = text(record, "normalised_provider_transaction_id") or None

    return Transaction(
        account_id=account_id,
        amount_minor=amount_minor,
        currency=currency,
        value_date=when,
        booking_date=when,
        description=description,
        counterparty=merchant,
        status=TransactionStatus.PENDING if pending else TransactionStatus.BOOKED,
        source="truelayer",
        # Pending records carry a different id from the settled version of the
        # same payment, which is why supersession exists rather than update.
        source_id=durable_id,
        # No durable id means exactly that. Claiming AUTHORITATIVE without one
        # would license merges the evidence does not support; SYNTHETIC routes
        # identity through the content key, which is what that tier is for.
        tier=SourceTier.AUTHORITATIVE if durable_id else SourceTier.SYNTHETIC,
        content_key=content_key(
            amount_minor=amount_minor,
            value_date=when,
            description=description or merchant,
        ),
        raw=json.loads(json.dumps(record, default=str)),
    )


def fetch_regulars(
    access_token: str,
    account_id: str,
    kind: str,
    *,
    psu_ip: str | None = None,
    client: httpx.Client | None = None,
) -> bytes:
    """Standing orders or direct debits, raw, for landing as evidence.

    `kind` is the endpoint path segment: standing_orders or direct_debits.
    These are the recurring-payment DECLARATIONS - the raw material for
    calming the review queue, since a transaction matching a standing order
    is expected by definition. They change rarely, so they are fetched only
    on deep (post-authorisation) pulls: refreshed each re-authorisation at
    zero unattended-quota cost.
    """
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}/data/v1/accounts/{account_id}/{kind}",
        headers=_headers(access_token, psu_ip),
    )
    if response.status_code != 200:
        raise _refusal(f"{kind} fetch failed", response)
    return response.content


def fetch_balance(
    access_token: str,
    account_id: str,
    *,
    psu_ip: str | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[JsonObject], bytes]:
    """The account balance now, with the raw body so it lands as evidence.

    A balance at a timestamp is a reconciliation anchor: if the transactions in
    the window do not sum to it, activity exists outside the window - a fact
    about coverage that no amount of transaction data can supply by itself.
    Each scheduled pull adds another anchor to the timeline.
    """
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}/data/v1/accounts/{account_id}/balance",
        headers=_headers(access_token, psu_ip),
    )
    if response.status_code != 200:
        raise _refusal("Balance fetch failed", response)
    return rows(decode(response.content), "results"), response.content


def artefact_for(
    body: bytes,
    *,
    account_id: str,
    kind: str,
    requested: str = "",
    request_meta: str = "",
) -> RawArtefact:
    """Land a payload together with the request that produced it.

    `requested` carries the query string, and it is not decoration. Rebuilding
    the derived layers from raw is what makes a matching bug recoverable rather
    than permanent - but only for questions the raw layer can still answer. An
    empty result is meaningless without knowing what was asked: "this account
    was dormant" and "only ninety days were requested" are the same bytes, and
    no rebuild can tell them apart afterwards.

    So the range asked for is part of the evidence, not part of the fetch.
    """
    # The URL actually requested, including the pending suffix. Recording a
    # query-string form of the pending flag wrote a URL that was never fetched
    # into layer 0 - provenance must describe the request that happened, not a
    # paraphrase of it.
    if kind == "accounts":
        origin = f"{API_HOST}/data/v1/accounts"
    elif kind == "balance":
        origin = f"{API_HOST}/data/v1/accounts/{account_id}/balance"
    elif kind in ("standing_orders", "direct_debits"):
        origin = f"{API_HOST}/data/v1/accounts/{account_id}/{kind}"
    else:
        suffix = "/pending" if kind == "pending" else ""
        origin = f"{API_HOST}/data/v1/accounts/{account_id}/transactions{suffix}"
    return RawArtefact(
        source=f"truelayer-{kind}",
        account_ref=account_id,
        fetched_at=datetime.now(UTC),
        media_type="application/json",
        digest=artefact_digest(body),
        payload=body,
        origin=f"{origin}?{requested}" if requested else origin,
        request_meta=request_meta,
    )
