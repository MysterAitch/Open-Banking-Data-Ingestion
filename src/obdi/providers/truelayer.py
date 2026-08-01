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
    """A provider call failed in a way worth surfacing rather than retrying."""


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
        raise TrueLayerError(
            f"Code exchange failed (HTTP {response.status_code}). A redirect URI "
            "mismatch is the usual cause - it must match what is registered byte "
            "for byte, trailing slash included."
        )
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


def fetch_accounts(
    access_token: str, *, client: httpx.Client | None = None
) -> list[JsonObject]:
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}/data/v1/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code != 200:
        raise TrueLayerError(f"Account fetch failed (HTTP {response.status_code})")
    return rows(decode(response.text), "results")


def fetch_transactions(
    access_token: str,
    account_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
    pending: bool = False,
    client: httpx.Client | None = None,
) -> tuple[list[JsonObject], bytes]:
    """Return parsed transactions and the raw body, so the raw body can be landed.

    The raw bytes are returned alongside deliberately: landing the verbatim
    payload is what makes the derived tables rebuildable later.
    """
    http = client or httpx.Client(timeout=30.0)
    suffix = "/pending" if pending else ""
    url = f"{API_HOST}/data/v1/accounts/{account_id}/transactions{suffix}"
    headers = {"Authorization": f"Bearer {access_token}"}

    if pending:
        response = http.get(url, headers=headers)
        if response.status_code != 200:
            raise TrueLayerError(f"Transaction fetch failed (HTTP {response.status_code})")
        body = response.content
        return rows(decode(body), "results"), body

    # An explicit `since` is an instruction, not a preference, so it is used as
    # given. Only the open-ended case walks the ladder.
    windows = (None,) if since else backfill_ladder()
    last_status = 0
    for days in windows:
        earliest = since or datetime.now(UTC).date() - timedelta(days=days or 0)
        response = http.get(
            url,
            headers=headers,
            params={
                "from": earliest.isoformat(),
                "to": (until or datetime.now(UTC).date()).isoformat(),
            },
        )
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
                print(
                    f"backfill narrowed to {days} days (from {windows[0]}) for account "
                    f"{account_id}: the provider refused the wider range. If this "
                    "account is older, some history was NOT fetched.",
                    file=sys.stderr,
                )
            body = response.content
            return rows(decode(body), "results"), body
        last_status = response.status_code
        # Only a rejected RANGE is worth narrowing for. A 401 means the token is
        # wrong and every rung will fail identically; retrying it three times
        # just spends the post-authorisation window on the same error.
        if response.status_code not in (400, 416, 422):
            break

    raise TrueLayerError(f"Transaction fetch failed (HTTP {last_status})")


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
            account_id=account_id,
            amount_minor=amount_minor,
            value_date=when,
            description=description or merchant,
        ),
        raw=json.loads(json.dumps(record, default=str)),
    )


def artefact_for(body: bytes, *, account_id: str, kind: str) -> RawArtefact:
    return RawArtefact(
        source=f"truelayer-{kind}",
        account_ref=account_id,
        fetched_at=datetime.now(UTC),
        media_type="application/json",
        digest=artefact_digest(body),
        payload=body,
        origin=f"{API_HOST}/data/v1/accounts/{account_id}/transactions",
    )
