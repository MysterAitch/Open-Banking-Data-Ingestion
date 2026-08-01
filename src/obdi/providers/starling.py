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
from ..models import RawArtefact, Transaction, TransactionStatus

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
    """A Starling call failed in a way worth surfacing rather than retrying."""


def _get(
    path: str, token: str, *, client: httpx.Client | None = None, **params
) -> tuple[dict, bytes]:
    http = client or httpx.Client(timeout=30.0)
    response = http.get(
        f"{API_HOST}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params or None,
    )
    if response.status_code == 403:
        raise StarlingError(
            f"Starling refused {path} (403). The token is probably missing a scope - "
            "read-only pulls need account:read, balance:read, transaction:read and space:read."
        )
    if response.status_code != 200:
        raise StarlingError(f"Starling call to {path} failed (HTTP {response.status_code})")
    return json.loads(response.text), response.content


def fetch_accounts(token: str, *, client: httpx.Client | None = None) -> list[dict]:
    payload, _ = _get("/api/v2/accounts", token, client=client)
    return payload.get("accounts", [])


@dataclass(frozen=True)
class Category:
    """A feed partition: either the account itself or one of its Spaces."""

    uid: str
    name: str
    is_space: bool


def fetch_categories(
    token: str, account_uid: str, *, client: httpx.Client | None = None
) -> list[Category]:
    """Every category holding transactions: the account plus one per Space.

    Fetching only the default category is the commonest way to lose data here,
    because the omission is invisible - the feed simply returns less.

    Spaces are returned as distinct categories rather than folded in, because
    each is its own account and its transactions belong to it.
    """
    accounts = fetch_accounts(token, client=client)
    categories = [
        Category(uid=account["defaultCategory"], name=account.get("name", "main"), is_space=False)
        for account in accounts
        if account.get("accountUid") == account_uid and account.get("defaultCategory")
    ]

    payload, _ = _get(f"/api/v2/account/{account_uid}/spaces", token, client=client)
    for goal in payload.get("savingsGoals", []):
        if goal.get("savingsGoalUid"):
            categories.append(
                Category(
                    uid=goal["savingsGoalUid"],
                    name=goal.get("name", "space"),
                    is_space=True,
                )
            )
    return categories


def fetch_feed(
    token: str,
    account_uid: str,
    category_uid: str,
    *,
    since: date | None = None,
    client: httpx.Client | None = None,
) -> tuple[list[dict], bytes]:
    """Feed items for one category, with the raw body for landing."""
    start = since or (date.today() - timedelta(days=DEFAULT_BACKFILL_DAYS))
    payload, body = _get(
        f"/api/v2/feed/account/{account_uid}/category/{category_uid}",
        token,
        client=client,
        changesSince=datetime.combine(start, datetime.min.time()).isoformat() + "Z",
    )
    return payload.get("feedItems", []), body


def to_transaction(item: dict, *, account_id: str) -> Transaction | None:
    """Map one feed item, or None if it should not be stored.

    Returns None only for movements that never happened - declined cards and
    account checks. Space transfers ARE kept: they are real movements between
    two accounts you own, and dropping them makes the money vanish and leaves
    the Space balance untrackable. They are marked as internal so that pairing
    can keep them out of spending without discarding them.
    """
    status = STATUS_MAP.get((item.get("status") or "").upper())
    if status is None:
        return None

    amount = item.get("amount") or {}
    currency = amount.get("currency", "GBP")
    if currency != "GBP":
        # minorUnits sidesteps float problems but says nothing about which
        # currency's minor units these are. Storing a euro figure as sterling
        # would be silent, and the budgeting tool downstream is single-currency
        # so there is nowhere correct for it to go.
        raise StarlingError(
            f"feed item {item.get('feedItemUid')} is in {currency}; only GBP is supported"
        )

    minor_units = amount.get("minorUnits")
    if not isinstance(minor_units, int):
        raise StarlingError(
            f"feed item {item.get('feedItemUid')} has a non-integer minorUnits; "
            "refusing to coerce an amount"
        )

    # minorUnits is unsigned; the sign lives in direction. Ignoring it makes
    # every payment look like income.
    direction = (item.get("direction") or "").upper()
    if direction == "OUT":
        minor_units = -abs(minor_units)
    elif direction == "IN":
        minor_units = abs(minor_units)
    else:
        raise StarlingError(
            f"feed item {item.get('feedItemUid')} has direction {direction!r}; "
            "refusing to guess the sign"
        )

    timestamp = item.get("transactionTime") or item.get("settlementTime")
    if not timestamp:
        raise StarlingError("feed item has no transaction time")
    when = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()

    counterparty = (item.get("counterPartyName") or "").strip()
    description = (item.get("reference") or "").strip() or counterparty

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
        source_id=item.get("feedItemUid") or None,
        # Marked here, confirmed later by pairing against the other side. The
        # flag is what keeps a Space transfer out of spending without losing it.
        is_internal_transfer=item.get("source") == INTERNAL_SOURCE,
        content_key=content_key(
            account_id=account_id,
            amount_minor=minor_units,
            value_date=when,
            description=description,
        ),
        raw=item,
    )


def artefact_for(body: bytes, *, account_id: str, category_uid: str) -> RawArtefact:
    return RawArtefact(
        source="starling-feed",
        account_ref=account_id,
        fetched_at=datetime.now(UTC),
        media_type="application/json",
        digest=artefact_digest(body),
        payload=body,
        origin=f"{API_HOST}/api/v2/feed/.../category/{category_uid}",
    )
