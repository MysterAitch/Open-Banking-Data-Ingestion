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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from ...core.jsontypes import JsonObject, as_object, nested, rows, text, whole_number
from ...core.models import RawArtefact, SourceTier, Transaction, TransactionStatus
from ..identity import artefact_digest, content_key
from ..party_fields import source_party_id, uk_account

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


#: Injectable so tests assert WHAT we waited without actually waiting.
_retry_sleep: Callable[[float], None] = time.sleep

#: One retry, waits capped: a provider asking for an hour is answered
#: with patience the next CYCLE can afford, not this call.
_RETRY_AFTER_DEFAULT_SECONDS = 2.0
_RETRY_AFTER_CAP_SECONDS = 30.0


def _retry_wait(retry_after: str | None) -> float:
    """The provider's own words about when to come back, made safe.

    Integer seconds honoured up to the cap; a missing or unparseable
    header (some send an HTTP-date) gets a modest default rather than a
    guess at date arithmetic.
    """
    try:
        seconds = float(retry_after) if retry_after is not None else _RETRY_AFTER_DEFAULT_SECONDS
    except ValueError:
        seconds = _RETRY_AFTER_DEFAULT_SECONDS
    return min(max(seconds, 0.0), _RETRY_AFTER_CAP_SECONDS)


def _get(
    path: str, token: str, *, client: httpx.Client | None = None, **params: str
) -> tuple[JsonObject, bytes]:
    http = client or httpx.Client(timeout=30.0)
    for attempt in (1, 2):
        response = http.get(
            f"{API_HOST}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params or None,
        )
        if response.status_code == 429 and attempt == 1:
            # The 429s arrived daily on the spaces' category feeds - once
            # seven seconds after the previous call, which refutes blind
            # client-side spacing. The provider SAYS when to come back;
            # obey it once, visibly, then land or refuse honestly.
            wait = _retry_wait(response.headers.get("retry-after"))
            print(
                f"starling: 429 on {path} - waiting {wait:g}s per the "
                "provider's retry-after, then retrying once",
                flush=True,
            )
            _retry_sleep(wait)
            continue
        break
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


#: The query-string names of the bounded-window ask. UNCONFIRMED against the
#: provider: built from its documented shape (a `transactions-between` path
#: under the category feed taking these two timestamps) and not yet answered
#: by it, so the first real pull is the confirmation and every refusal lands in
#: the attempt ledger.
WINDOW_MIN_PARAM = "minTransactionTimestamp"
WINDOW_MAX_PARAM = "maxTransactionTimestamp"

_WINDOW_STAMP = "%Y-%m-%dT%H:%M:%S.000Z"
_WINDOW_SPEC = re.compile(
    rf"^{WINDOW_MIN_PARAM}=(?P<lo>[0-9T:.\-]+Z)&{WINDOW_MAX_PARAM}=(?P<hi>[0-9T:.\-]+Z)$"
)


def window_spec(minimum: datetime, maximum: datetime) -> str:
    """The ask in the API's own vocabulary: what the ledger and the origin record."""
    lo = minimum.astimezone(UTC).strftime(_WINDOW_STAMP)
    hi = maximum.astimezone(UTC).strftime(_WINDOW_STAMP)
    return f"{WINDOW_MIN_PARAM}={lo}&{WINDOW_MAX_PARAM}={hi}"


def parse_window_spec(asked: str) -> tuple[datetime, datetime] | None:
    """The window a recorded ask named, or None for any other kind of ask."""
    found = _WINDOW_SPEC.match(asked)
    if found is None:
        return None
    return (
        datetime.strptime(found["lo"], _WINDOW_STAMP).replace(tzinfo=UTC),
        datetime.strptime(found["hi"], _WINDOW_STAMP).replace(tzinfo=UTC),
    )


def fetch_feed_between(
    token: str,
    account_uid: str,
    category_uid: str,
    *,
    minimum: datetime,
    maximum: datetime,
    client: httpx.Client | None = None,
) -> tuple[list[JsonObject], bytes, str]:
    """Feed items whose transaction time lies in a bounded window.

    `changesSince` runs from its stamp to now, so the provider's maximum range
    (refused with QUERY_EXCEEDING_MAX_TIME_RANGE somewhere between 180 and 365
    days) puts every older movement out of its reach; this ask names both ends.
    Returns the items, the raw body for landing, and the window asked.
    """
    asked = window_spec(minimum, maximum)
    payload, body = _get(
        f"/api/v2/feed/account/{account_uid}/category/{category_uid}/transactions-between",
        token,
        client=client,
        **{
            WINDOW_MIN_PARAM: minimum.astimezone(UTC).strftime(_WINDOW_STAMP),
            WINDOW_MAX_PARAM: maximum.astimezone(UTC).strftime(_WINDOW_STAMP),
        },
    )
    return rows(payload, "feedItems"), body, asked


def _settlement_day(item: JsonObject, transacted: date) -> date:
    """The day the item states it settled, else the day it was made.

    `value_date` carries `transactionTime` (when the owner acted) and `booking_date` the
    `settlementTime` (when the bank took it), so a rhythm can be measured on whichever date its
    kind needs (`analysis.recurring`). An item with no settlement time has only the one date.
    """
    stated = text(item, "settlementTime")
    if not stated:
        return transacted
    return datetime.fromisoformat(stated.replace("Z", "+00:00")).date()


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
    settled = _settlement_day(item, when)

    counterparty = text(item, "counterPartyName").strip()
    description = text(item, "reference").strip() or counterparty

    return Transaction(
        account_id=account_id,
        amount_minor=minor_units,
        currency=currency,
        value_date=when,
        booking_date=settled,
        description=description,
        counterparty=counterparty,
        party_account=uk_account(
            text(item, "counterPartySubEntityIdentifier"),
            text(item, "counterPartySubEntitySubIdentifier"),
        ),
        party_source_id=source_party_id("starling", text(item, "counterPartyUid")),
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


#: What a round-up leg's identity adds to its payment's feed item uid.
ROUND_UP_LEG_SUFFIX = ":round-up"

#: The leg's own words, a payee-less description kept constant so that two
#: round-ups of one amount on one day are told apart by occurrence alone.
ROUND_UP_DESCRIPTION = "Round-up"


@dataclass(frozen=True)
class RoundUpReading:
    """What a feed item's `roundUp` says, tolerantly read.

    The shape is Starling's `AssociatedFeedRoundUp` as understood from its
    published feed item: the Space's category as `goalCategoryUid` and the spare
    change as an `amount` of `currency` and `minorUnits`. Nothing here has been
    checked against a real feed item, so anything else is `unreadable` and
    counted, never guessed at.
    """

    #: The item has a `roundUp` that is not null.
    carried: bool = False
    #: The item has one that cannot be read as a movement of money.
    unreadable: bool = False
    #: The spare change moved, and the category it moved to; nil when there is no leg.
    amount_minor: int = 0
    space_uid: str = ""

    @property
    def moves_money(self) -> bool:
        return self.amount_minor > 0


def round_up_of(item: JsonObject) -> RoundUpReading:
    """Read an item's round-up: absent, nothing, a movement, or unreadable.

    A round-up of nothing, or one with no amount, is not a movement and not a
    fault. Anything present that cannot be read - and any round-up on an item
    with no uid to derive the leg's identity from - is unreadable.
    """
    value = item.get("roundUp")
    if value is None:
        return RoundUpReading()
    if not isinstance(value, dict):
        return RoundUpReading(carried=True, unreadable=True)
    amount = value.get("amount")
    if amount is None:
        return RoundUpReading(carried=True)
    if not isinstance(amount, dict):
        return RoundUpReading(carried=True, unreadable=True)
    minor = amount.get("minorUnits")
    space = value.get("goalCategoryUid")
    if (
        isinstance(minor, bool)
        or not isinstance(minor, int)
        or minor < 0
        or amount.get("currency", "GBP") != "GBP"
    ):
        return RoundUpReading(carried=True, unreadable=True)
    if minor == 0:
        return RoundUpReading(carried=True)
    uid = item.get("feedItemUid")
    if not isinstance(space, str) or not space.strip() or not isinstance(uid, str) or not uid:
        return RoundUpReading(carried=True, unreadable=True)
    return RoundUpReading(carried=True, amount_minor=minor, space_uid=space.strip())


def to_transactions(item: JsonObject, *, account_id: str) -> list[Transaction]:
    """Every row one feed item yields: the item itself, then any round-up leg.

    A card payment with round-ups on is ONE feed item in the main category at
    the payment's own amount, and the money leaving for the Space is reported
    nowhere in that category. The leg is therefore a second row beside the
    payment rather than part of it: the payment's amount must stay the
    payment's, because the export and the aggregator sight the payment at that
    amount and every match depends on it.

    `_round_up_leg` says when a leg exists, which can be beside no payment row
    at all.
    """
    payment = to_transaction(item, account_id=account_id)
    leg = _round_up_leg(item, account_id=account_id)
    rows = [] if payment is None else [payment]
    return rows if leg is None else [*rows, leg]


def payment_unsettled(status: str) -> bool:
    """Whether a feed item's status names a payment that was reversed or yields no row.

    A status the map does not know is dropped like a declined one, so it is
    unsettled here too. Every reader of "on a reversed or dropped payment" asks
    this, so the answer is given once.
    """
    mapped = STATUS_MAP.get(status.upper())
    return mapped is None or mapped is TransactionStatus.REVERSED


def _round_up_leg(item: JsonObject, *, account_id: str) -> Transaction | None:
    """The OUT leg of an item's round-up, or None where the round-up did not leave the account.

    The round-up of a reversed or dropped payment is made a leg too, whatever
    the payment's direction and whether or not it yields a row, and that leg is
    BOOKED: on the one real account examined, every such carrier's round-up had
    arrived in its Space as a booked item, and the main category held nothing
    for the money leaving, so the Space's family was over by exactly it.
    That is evidence from one account and not a rule of the bank. The count of
    legs with no pair in a Space (`round_up_accounts.round_up_gaps`, those on a
    reversed or dropped payment) is where a counter-example would show.

    An item that is money IN and neither reversed nor dropped is refused a leg:
    nothing seen says a refund's round-up moves money out of the account.
    A transfer, which has its own main-side item, never gains a second one.
    """
    reading = round_up_of(item)
    status = text(item, "status")
    unsettled = payment_unsettled(status)
    if (
        not reading.moves_money
        or text(item, "source") == INTERNAL_SOURCE
        or text(item, "counterPartyType").upper() == "CATEGORY"
        or (not unsettled and text(item, "direction").upper() != "OUT")
    ):
        return None
    when_text = text(item, "transactionTime") or text(item, "settlementTime")
    if not when_text:
        return None
    when = datetime.fromisoformat(when_text.replace("Z", "+00:00")).date()
    leg_status = TransactionStatus.BOOKED if unsettled else STATUS_MAP[status.upper()]
    if leg_status is None:
        return None
    uid = text(item, "feedItemUid") + ROUND_UP_LEG_SUFFIX
    minor = -reading.amount_minor
    raw: JsonObject = {
        "feedItemUid": uid,
        "roundUpOf": text(item, "feedItemUid"),
        "amount": {"currency": "GBP", "minorUnits": reading.amount_minor},
        "direction": "OUT",
        "source": INTERNAL_SOURCE,
        "status": text(item, "status"),
        "transactionTime": text(item, "transactionTime") or text(item, "settlementTime"),
        "counterPartyType": "CATEGORY",
        "counterPartyUid": reading.space_uid,
        "counterPartyName": "",
    }
    return Transaction(
        account_id=account_id,
        amount_minor=minor,
        currency="GBP",
        value_date=when,
        booking_date=_settlement_day(item, when),
        description=ROUND_UP_DESCRIPTION,
        counterparty="",
        status=leg_status,
        source="starling",
        source_id=uid,
        tier=SourceTier.AUTHORITATIVE,
        is_internal_transfer=True,
        content_key=content_key(
            amount_minor=minor, value_date=when, description=ROUND_UP_DESCRIPTION
        ),
        raw=raw,
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
