"""Pull from Starling's first-party API.

Better than any aggregator for a Starling account: free, no aggregator in the
chain, and because first-party access to your own bank is not an account
information service, **no 90-day consent clock**. A personal access token does
not expire on that cycle.

Three shapes of this API cause silent data loss if missed.

**Spaces are separate categories.** Transactions live in a feed partitioned by
category, and every Space (savings goal) is its own category. Fetching only the
default category silently drops all Space activity.

**Amounts are unsigned integers with the direction alongside.** `minorUnits` is
already the integer this project stores, which sidesteps float problems
entirely - but the sign lives in `direction`, and ignoring it makes every
payment look like income.

**A Space is an account, and must be modelled as one.** Moving money into a
Space is a transfer between two accounts you own, not spending. Budgeting tools
that flatten Spaces into the parent account get this wrong in both directions:
treating the movement as external inflates spending and income alike, while
discarding it makes the money vanish and leaves the Space balance untrackable.

So each Space is resolved to its own canonical account and both sides of the
movement are kept - an outflow from the parent, an inflow to the Space - then
paired as an internal transfer. The pairing is what keeps it out of spending
while preserving the fact that it happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from ..identity import artefact_digest, content_key
from ..jsontypes import JsonObject, as_object, nested, rows, text, whole_number
from ..models import RawArtefact, SourceTier, Transaction, TransactionStatus

API_HOST = "https://api.starlingbank.com"

# Starling serves history back to account opening, so the only limit on the
# first pull is patience.
DEFAULT_BACKFILL_DAYS = 3650

# Movements between your own Spaces. Real to the bank, noise to a budget, and
# double-counted if kept.
INTERNAL_SOURCE = "INTERNAL_TRANSFER"

# DECLINED never moved money. The rest did, including refunds and reversals,
# which are genuine movements rather than corrections to be swallowed.
STATUS_MAP = {
    "SETTLED": TransactionStatus.BOOKED,
    "PENDING": TransactionStatus.PENDING,
    "REFUNDED": TransactionStatus.BOOKED,
    "REVERSED": TransactionStatus.REVERSED,
    "ACCOUNT_CHECK": None,
    "DECLINED": None,
}


class StarlingError(RuntimeError):
    """A Starling call failed in a way worth surfacing rather than retrying.

    Carries the status and the body excerpt, the lesson learnt live on the
    TrueLayer side: a bare status number cannot be acted on, and the attempt
    ledger wants the parts, not a blob.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        raw: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.raw = raw
        #: Retry-After and friends: the provider's own words about when to
        #: come back, kept so the ledger can show them.
        self.headers = headers or {}


def _get(
    path: str, token: str, *, client: httpx.Client | None = None, **params: str
) -> tuple[JsonObject, bytes]:
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or None,
    )
    kept = {
        name: response.headers[name]
        for name in ("retry-after", "x-ratelimit-remaining", "date")
        if name in response.headers
    }
    if response.status_code == 403:
        raise StarlingError(
            f"Starling refused {path} (403): {response.text[:200]}. The token is "
            "probably missing a scope - read-only pulls need account:read, "
            "balance:read, transaction:read and space:read.",
            status=403,
            raw=response.text[:1000],
            headers=kept,
        )
    if response.status_code != 200:
        raise StarlingError(
            f"Starling call to {path} failed (HTTP {response.status_code}): "
            f"{response.text[:200]}",
            status=response.status_code,
            raw=response.text[:1000],
            headers=kept,
        )
    return as_object(json.loads(response.text), field="response"), response.content


def fetch_accounts(
    token: str, *, client: httpx.Client | None = None
) -> tuple[list[JsonObject], bytes]:
    """The accounts, AND the raw body - evidence to land, not just parse."""
    payload, body = _get("/api/v2/accounts", token, client=client)
    return rows(payload, "accounts"), body


def fetch_balance(
    token: str, account_uid: str, *, client: httpx.Client | None = None
) -> bytes:
    """The balance body for landing: a reconciliation anchor at a timestamp,
    same role as on the TrueLayer side."""
    _, body = _get(f"/api/v2/accounts/{account_uid}/balance", token, client=client)
    return body


def fetch_identifiers(
    token: str, account_uid: str, *, client: httpx.Client | None = None
) -> bytes:
    """The account's sort code, number and IBAN, as raw evidence.

    Starling's accounts call carries none of these - they live on their
    own endpoint - which is why the first-party side of an account could
    not be matched against any other source until this was fetched. One
    first-party call, so it costs nothing against an aggregator's daily
    cap and never touches an SCA window.
    """
    _, body = _get(
        f"/api/v2/accounts/{account_uid}/identifiers", token, client=client
    )
    return body


@dataclass(frozen=True)
class Category:
    """A feed partition: either the account itself or one of its Spaces."""

    uid: str
    name: str
    is_space: bool


def fetch_categories(
    token: str, account_uid: str, *, client: httpx.Client | None = None
) -> tuple[list[Category], bytes]:
    """Every category holding transactions: the account plus one per Space.

    Fetching only the default category is the commonest way to lose data here,
    because the omission is invisible - the feed simply returns less.

    Spaces are returned as distinct categories rather than folded in, because
    each is its own account and its transactions belong to it.
    """
    accounts, _ = fetch_accounts(token, client=client)
    categories = [
        Category(
            uid=text(account, "defaultCategory"),
            name=text(account, "name", default="main"),
            is_space=False,
        )
        for account in accounts
        if text(account, "accountUid") == account_uid and text(account, "defaultCategory")
    ]

    payload, spaces_body = _get(
        f"/api/v2/account/{account_uid}/spaces", token, client=client
    )
    for goal in rows(payload, "savingsGoals"):
        if text(goal, "savingsGoalUid"):
            categories.append(
                Category(
                    uid=text(goal, "savingsGoalUid"),
                    name=text(goal, "name", default="space"),
                    is_space=True,
                )
            )
    return categories, spaces_body


def fetch_feed(
    token: str,
    account_uid: str,
    category_uid: str,
    *,
    since: date | None = None,
    since_at: datetime | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[JsonObject], bytes, str]:
    """Feed items, the raw body for landing, and the range actually asked.

    since_at asks to the minute rather than the midnight - the
    changes-probe needs a cutoff BETWEEN a transaction's own time and
    the moment its record changed, and days are too blunt for that.
    """
    # Explicitly UTC: date.today() reads the process timezone, so a
    # container and a workstation can disagree about which day it is and
    # silently shift the window boundary.
    if since_at is not None:
        stamp = since_at.astimezone(UTC).replace(tzinfo=None).isoformat() + "Z"
    else:
        start = since or (
            datetime.now(UTC).date() - timedelta(days=DEFAULT_BACKFILL_DAYS)
        )
        stamp = datetime.combine(start, datetime.min.time()).isoformat() + "Z"
    payload, body = _get(
        f"/api/v2/feed/account/{account_uid}/category/{category_uid}",
        token,
        client=client,
        changesSince=stamp,
    )
    # The range actually asked, in the API's own vocabulary - recorded so an
    # empty feed stays distinguishable from a feed never asked about, and so
    # the coverage trackers can read the window edges back.
    return rows(payload, "feedItems"), body, f"changesSince={stamp}"


def to_transaction(item: JsonObject, *, account_id: str) -> Transaction | None:
    """Map one feed item, or None if it should not be stored.

    Returns None only for movements that never happened - declined cards and
    account checks. Space transfers ARE kept: they are real movements between
    two accounts you own, and dropping them makes the money vanish and leaves
    the Space balance untrackable. They are marked as internal so that pairing
    can keep them out of spending without discarding them.
    """
    status = STATUS_MAP.get(text(item, "status").upper())
    if status is None:
        return None

    amount = nested(item, "amount")
    currency = text(amount, "currency", default="GBP")
    if currency != "GBP":
        # minorUnits sidesteps float problems but says nothing about which
        # currency's minor units these are. Storing a euro figure as sterling
        # would be silent, and the budgeting tool downstream is single-currency
        # so there is nowhere correct for it to go.
        raise StarlingError(
            f"feed item {text(item, 'feedItemUid')} is in {currency}; only GBP is supported"
        )

    minor_units = whole_number(amount, "minorUnits")
    if minor_units is None:
        raise StarlingError(
            f"feed item {text(item, 'feedItemUid')} has a non-integer minorUnits; "
            "refusing to coerce an amount"
        )

    # minorUnits is unsigned; the sign lives in direction. Ignoring it makes
    # every payment look like income.
    direction = text(item, "direction").upper()
    if direction == "OUT":
        minor_units = -abs(minor_units)
    elif direction == "IN":
        minor_units = abs(minor_units)
    else:
        raise StarlingError(
            f"feed item {text(item, 'feedItemUid')} has direction {direction!r}; "
            "refusing to guess the sign"
        )

    timestamp = text(item, "transactionTime") or text(item, "settlementTime")
    if not timestamp:
        raise StarlingError("feed item has no transaction time")
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()

    counterparty = text(item, "counterPartyName").strip()
    description = text(item, "reference").strip() or counterparty

    return Transaction(
        account_id=account_id,
        amount_minor=minor_units,
        currency=currency,
        value_date=when,
        booking_date=when,
        description=description,
        counterparty=counterparty,
        status=status,
        source="starling",
        source_id=text(item, "feedItemUid") or None,
        tier=SourceTier.AUTHORITATIVE,
        # Marked here, confirmed later by pairing against the other side. The
        # flag is what keeps a Space transfer out of spending without losing it.
        is_internal_transfer=text(item, "source") == INTERNAL_SOURCE,
        content_key=content_key(
            amount_minor=minor_units,
            value_date=when,
            description=description,
        ),
        raw=item,
    )


def _connection_of(request_meta: str) -> str:
    """The fetching connection, read from the request circumstances.

    Extracted here, at the single point every landing passes through,
    rather than threaded as one more parameter through every call site -
    the value already travels in the metadata the pull builds."""
    try:
        return str(json.loads(request_meta or "{}").get("connection_id", ""))
    except ValueError:
        return ""


def artefact_for(
    body: bytes,
    *,
    account_id: str,
    kind: str = "feed",
    origin: str = "",
    request_meta: str = "",
    category_uid: str = "",
) -> RawArtefact:
    """Land any Starling payload with its provenance, TrueLayer-style.

    `origin` is the REAL request including its query string - the coverage
    trackers parse the window edges back out of it, and provenance must
    describe the request that happened, not a paraphrase.
    """
    return RawArtefact(
        source=f"starling-{kind}",
        account_ref=account_id,
        fetched_at=datetime.now(UTC),
        media_type="application/json",
        digest=artefact_digest(body),
        payload=body,
        origin=origin or f"{API_HOST}/api/v2/feed/.../category/{category_uid}",
        request_meta=request_meta,
        connection_id=_connection_of(request_meta),
    )
