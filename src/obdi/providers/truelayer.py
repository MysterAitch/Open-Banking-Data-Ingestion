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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from ..identity import artefact_digest, content_key
from ..models import RawArtefact, Transaction, TransactionStatus
from ..money import parse_amount

AUTH_HOST = "https://auth.truelayer.com"
API_HOST = "https://api.truelayer.com"

# First connect is the one chance at deep history: the extra authentication
# that unlocks more than ninety days only happens at authorisation time.
DEFAULT_BACKFILL_DAYS = 730


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
) -> dict:
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


def decode(payload: bytes | str) -> dict:
    """Decode provider JSON without letting floats near an amount."""
    text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    return json.loads(text, parse_float=Decimal)


def refresh_access_token(
    *, refresh_token: str, client_id: str, client_secret: str, client: httpx.Client | None = None
) -> dict:
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


def fetch_accounts(access_token: str, *, client: httpx.Client | None = None) -> list[dict]:
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}/data/v1/accounts",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code != 200:
        raise TrueLayerError(f"Account fetch failed (HTTP {response.status_code})")
    return decode(response.text).get("results", [])


def fetch_transactions(
    access_token: str,
    account_id: str,
    *,
    since: date | None = None,
    until: date | None = None,
    pending: bool = False,
    client: httpx.Client | None = None,
) -> tuple[list[dict], bytes]:
    """Return parsed transactions and the raw body, so the raw body can be landed.

    The raw bytes are returned alongside deliberately: landing the verbatim
    payload is what makes the derived tables rebuildable later.
    """
    http = client or httpx.Client(timeout=30.0)
    suffix = "/pending" if pending else ""
    params = {}
    if not pending:
        params["from"] = (since or date.today() - timedelta(days=DEFAULT_BACKFILL_DAYS)).isoformat()
        params["to"] = (until or date.today()).isoformat()

    response = http.get(
        f"{API_HOST}/data/v1/accounts/{account_id}/transactions{suffix}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
    )
    if response.status_code != 200:
        raise TrueLayerError(f"Transaction fetch failed (HTTP {response.status_code})")
    body = response.content
    return decode(body).get("results", []), body


def to_transaction(record: dict, *, account_id: str, pending: bool = False) -> Transaction:
    """Map one provider record onto the canonical model.

    The amount arrives signed. Where `transaction_type` is also present the two
    are cross-checked, because a sign convention that silently flips is the
    single most damaging thing that can happen to this data.
    """
    raw_amount = record.get("amount")
    if raw_amount is None:
        raise TrueLayerError("transaction has no amount")

    amount_minor = parse_amount(str(raw_amount))

    transaction_type = (record.get("transaction_type") or "").upper()
    if transaction_type == "DEBIT" and amount_minor > 0:
        amount_minor = -amount_minor
    elif transaction_type == "CREDIT" and amount_minor < 0:
        raise TrueLayerError(
            f"transaction {record.get('transaction_id')} is typed CREDIT but carries a "
            "negative amount; refusing to guess the sign convention"
        )

    timestamp = record.get("timestamp", "")
    if not timestamp:
        raise TrueLayerError("transaction has no timestamp")
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()

    description = record.get("description", "")
    merchant = (record.get("merchant_name") or "").strip()

    return Transaction(
        account_id=account_id,
        amount_minor=amount_minor,
        currency=record.get("currency", "GBP"),
        value_date=when,
        booking_date=when,
        description=description,
        counterparty=merchant,
        status=TransactionStatus.PENDING if pending else TransactionStatus.BOOKED,
        source="truelayer",
        # Pending records carry a different id from the settled version of the
        # same payment, which is why supersession exists rather than update.
        source_id=record.get("transaction_id") or None,
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
