"""Transactions a person typed, as evidence, and the withdrawal of one.

An account obdi has no feed for (a mortgage at another bank) has nothing to land
rows from, so a person types them. Typing is not an edit of the merged layer: it
is a WITNESS, like a feed, and goes through the same layers.

  entry        lands as its own raw artefact (source `MANUAL_SOURCE`), a small
               JSON payload carrying a minted entry id, the date, the signed
               figure, and the description. It parses into a row with
               `SourceTier.MANUAL`, source `manual`, and the entry id as its
               provider id, so a rebuild replays it in arrival order into the
               same row, with the same content key and so the same Actual
               imported id.
  withdrawal   a SECOND artefact (source `MANUAL_WITHDRAWAL_SOURCE`) naming the
               entry it retracts. A raw artefact is never edited or deleted, so
               the entry stays in layer 0 for good.

A WITHDRAWN ENTRY HAS NO ROW. It is not kept as a VOID row: void means a payment
that was seen and then vanished from a provider's list, whereas a withdrawn entry
is a claim its author took back. A void row also stays a candidate for the
matcher, so a later feed row of the same amount could be folded into it and the
retracted claim would reappear as a real payment. The history is in layer 0, and
the ledger lists the withdrawn entries by date beside the live ones.

WITHDRAWING AN ENTRY A FEED HAS SINCE CLAIMED removes only the typed sighting:
the row is by then the feed's payment (see `matching.could_be_one_payment`), and
retracting what a person once typed does not make the bank's record go away.
A rebuild after the withdrawal reaches the same state, with the feed row alone.

REFUSALS NEVER QUOTE WHAT WAS TYPED. A refusal page is reachable by an address,
and a figure or a description echoed into one is a value on a page whose rule is
that values appear only in the direct answer to a POST.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from .accounts import AccountMap
from .arrival_order import in_arrival_order
from .balance_anchors import known_account, parse_calendar_day, parse_pounds_and_pence
from .declined_items import void_declined_items
from .errors import DataError
from .identity import artefact_digest, content_key
from .ingest import pair_transfers_across_store, reconcile_batch
from .jsontypes import JsonObject, as_object, text, whole_number
from .models import RawArtefact, SourceTier, Transaction, TransactionStatus
from .namespaces import MANUAL_SOURCE, MANUAL_WITHDRAWAL_SOURCE
from .protection import recheck
from .review_settlement import settle_review_flags
from .store import Store

ENTRY_KIND = "typed-transaction"
WITHDRAWAL_KIND = "typed-withdrawal"
PAYLOAD_VERSION = 1

#: Long enough for a payee and a reference, short enough that a pasted paragraph
#: is refused rather than becoming a payee name in the budget.
MAX_DESCRIPTION = 140

IN = "in"
OUT = "out"

_ENTRY_ID = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SIGN = re.compile(r"[-+()]")

_NOT_TYPED = (
    "no typed transaction with that identity is held for this account, so there is "
    "nothing to remove. A row a bank reported is not one: it is evidence about "
    "the account, not something typed"
)


class TypedRefused(DataError):
    """A typed transaction or a withdrawal could not be recorded as asked.

    The text is a fixed sentence and never quotes the figure, the description, or
    the date that was typed.
    """


@dataclass(frozen=True)
class TypedEntry:
    """One typed transaction as the evidence says it stands now."""

    entry_id: str
    account: str
    day: date
    #: Signed: money leaving the account is negative, as for every row.
    amount_minor: int
    description: str
    digest: str
    withdrawn: bool


def encode_entry(entry_id: str, day: date, amount_minor: int, description: str) -> bytes:
    """The entry's payload, canonical so identical entries are identical bytes.

    The account is NOT in it: the artefact's own `account_ref` carries that, and a
    rebind moves that column, so a copy in the payload would go stale.
    """
    return json.dumps(
        {
            "kind": ENTRY_KIND,
            "version": PAYLOAD_VERSION,
            "entry_id": entry_id,
            "date": day.isoformat(),
            "amount_minor": amount_minor,
            "currency": "GBP",
            "description": description,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode(payload: bytes | str, *, expected: str) -> JsonObject:
    try:
        decoded = json.loads(payload)
    except ValueError as exc:
        raise DataError(f"typed artefact is not readable as JSON: {exc}") from exc
    body = as_object(decoded, field="typed artefact")
    if text(body, "kind") != expected:
        raise DataError(
            f"typed artefact has kind {text(body, 'kind')!r}, expected {expected!r}"
        )
    if whole_number(body, "version") != PAYLOAD_VERSION:
        raise DataError("typed artefact has a payload version this build does not read")
    return body


def _entry_fields(body: JsonObject) -> tuple[str, date, int, str]:
    entry_id = text(body, "entry_id")
    if not _ENTRY_ID.match(entry_id):
        raise DataError("typed entry has no well-formed entry id")
    try:
        day = date.fromisoformat(text(body, "date"))
    except ValueError as exc:
        raise DataError("typed entry has no valid date") from exc
    minor = whole_number(body, "amount_minor")
    if minor is None or minor == 0:
        raise DataError("typed entry has no non-zero whole-number amount")
    if text(body, "currency") != "GBP":
        raise DataError("typed entry is not in GBP")
    return entry_id, day, minor, text(body, "description")


def transaction_from_entry(payload: bytes, account_ref: str, digest: str) -> Transaction:
    """The row an entry stands for. Raises DataError for a payload that is not one."""
    body = _decode(payload, expected=ENTRY_KIND)
    entry_id, day, minor, description = _entry_fields(body)
    return Transaction(
        account_id=account_ref,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source=MANUAL_SOURCE,
        # The entry id is what makes two typed rows of the same figure on one
        # day two rows: nothing in their content tells them apart, and the
        # matcher never merges two typed rows in any case.
        source_id=entry_id,
        tier=SourceTier.MANUAL,
        status=TransactionStatus.BOOKED,
        artefact_digest=digest,
        content_key=content_key(amount_minor=minor, value_date=day, description=description),
        raw=body,
    )


def withdrawn_entry_ids(
    artefacts: Iterable[tuple[str, bytes | str]], problems: list[str] | None = None
) -> set[str]:
    """The entry ids retracted by any withdrawal among (source, payload) pairs.

    Order does not matter: a withdrawal retracts its entry whenever it arrived,
    so a replay reaches the same answer however the two were interleaved.

    An unreadable withdrawal raises, unless `problems` is given, in which case it
    is recorded there and skipped: a rebuild asks after it has wiped the derived
    layers, and one unreadable artefact must not abort it half way.
    """
    retracted: set[str] = set()
    for source, payload in artefacts:
        if source != MANUAL_WITHDRAWAL_SOURCE:
            continue
        try:
            retracted.add(text(_decode(payload, expected=WITHDRAWAL_KIND), "withdraws"))
        except DataError as exc:
            if problems is None:
                raise
            problems.append(f"{source}: {exc}")
    return retracted


def typed_entries(store: Store, ref: str) -> list[TypedEntry]:
    """Every entry typed for the account, oldest first, withdrawn ones included."""
    held = in_arrival_order(
        store.connection.execute(
            "SELECT rowid, source, digest, payload, fetched_at FROM raw_artefacts "
            "WHERE account_ref = ? AND source IN (?, ?)",
            (ref, MANUAL_SOURCE, MANUAL_WITHDRAWAL_SOURCE),
        ).fetchall()
    )
    retracted = withdrawn_entry_ids((str(r["source"]), r["payload"]) for r in held)
    entries = []
    for row in held:
        if str(row["source"]) != MANUAL_SOURCE:
            continue
        entry_id, day, minor, description = _entry_fields(
            _decode(row["payload"], expected=ENTRY_KIND)
        )
        entries.append(
            TypedEntry(
                entry_id=entry_id,
                account=ref,
                day=day,
                amount_minor=minor,
                description=description,
                digest=str(row["digest"]),
                withdrawn=entry_id in retracted,
            )
        )
    return entries


def record_typed_transaction(
    store: Store,
    ref: str,
    day_text: str,
    direction: str,
    amount_text: str,
    description: str,
    *,
    today: date | None = None,
    now: datetime | None = None,
    entry_id: str | None = None,
    account_map: AccountMap | None = None,
) -> str:
    """Land one typed transaction, resolve it into the account, and return its entry id.

    Every refusal is raised BEFORE anything is written: an account the store has
    never heard of, a date that is not a real one or has not happened yet (it would
    count towards today's balance), a direction that is neither in nor out, a
    figure that is not pounds and pence, nil, or signed (the direction says which
    way it went), and a description that is empty, over `MAX_DESCRIPTION`, or
    carries a control character.
    """
    ref = ref.strip()
    if not ref or not known_account(store, ref):
        raise TypedRefused(
            "no account is declared or holds rows under that reference, so there "
            "is nothing to type a transaction into"
        )
    day = parse_calendar_day(day_text)
    if day > (today or datetime.now(UTC).date()):
        raise TypedRefused("a transaction cannot be typed for a date that has not happened yet")
    if direction not in (IN, OUT):
        raise TypedRefused("say whether the money came in or went out")
    typed = amount_text.strip()
    if _SIGN.search(typed):
        raise TypedRefused(
            "type the figure without a sign: the choice of in or out says which way it went"
        )
    magnitude = parse_pounds_and_pence(typed)
    if magnitude <= 0:
        raise TypedRefused("a transaction needs a figure above nil")
    words = description.strip()
    if not words:
        raise TypedRefused("a transaction needs a description, which is its payee in the budget")
    if len(words) > MAX_DESCRIPTION:
        raise TypedRefused(f"the description is longer than {MAX_DESCRIPTION} characters")
    if _CONTROL.search(words):
        raise TypedRefused("the description holds a character that cannot be kept")
    minor = magnitude if direction == IN else -magnitude
    minted = entry_id or secrets.token_hex(8)
    payload = encode_entry(minted, day, minor, words)
    digest = artefact_digest(payload)
    store.land_artefact(
        RawArtefact(
            source=MANUAL_SOURCE,
            account_ref=ref,
            fetched_at=now or datetime.now().astimezone(),
            media_type="application/json",
            digest=digest,
            payload=payload,
            origin="typed",
        )
    )
    blind = None
    if account_map is not None:
        # Imported here: `family_anchors` reaches the store's readers, which reach `ingest`.
        from .family_anchors import families_of

        blind = families_of(store, account_map).blind_in
    reconcile_batch(
        store, [transaction_from_entry(payload, ref, digest)], digest=digest, space_blind=blind
    )
    void_declined_items(store)
    settle_review_flags(store)
    pair_transfers_across_store(store, account_map)
    recheck(store)
    return minted


def withdraw_typed_transaction(
    store: Store,
    ref: str,
    entry_id: str,
    *,
    now: datetime | None = None,
    account_map: AccountMap | None = None,
) -> None:
    """Retract one typed transaction by landing a withdrawal, then apply it live.

    Refused for anything that is not a live typed entry of this account: an id
    nobody minted, a row a bank reported, an entry of another account, an entry
    already withdrawn.
    """
    ref = ref.strip()
    wanted = entry_id.strip()
    if not _ENTRY_ID.match(wanted):
        raise TypedRefused(_NOT_TYPED)
    entry = next((e for e in typed_entries(store, ref) if e.entry_id == wanted), None)
    if entry is None:
        raise TypedRefused(_NOT_TYPED)
    if entry.withdrawn:
        raise TypedRefused("that typed transaction has already been removed")
    payload = json.dumps(
        {"kind": WITHDRAWAL_KIND, "version": PAYLOAD_VERSION, "withdraws": wanted},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    store.land_artefact(
        RawArtefact(
            source=MANUAL_WITHDRAWAL_SOURCE,
            account_ref=ref,
            fetched_at=now or datetime.now().astimezone(),
            media_type="application/json",
            digest=artefact_digest(payload),
            payload=payload,
            origin="typed",
        )
    )
    _retract_live(store, entry.digest)
    pair_transfers_across_store(store, account_map)
    recheck(store)


def _retract_live(store: Store, entry_digest: str) -> None:
    """Make the merged layer read as a rebuild would after the withdrawal.

    The entry's sighting goes. The row goes with it only when that was its sole
    sighting, which is to say no feed has claimed it since.
    """
    connection = store.connection
    sighted = [
        str(row[0])
        for row in connection.execute(
            "SELECT entity_id FROM transaction_sources WHERE source = ? AND artefact_digest = ?",
            (MANUAL_SOURCE, entry_digest),
        )
    ]
    for entity_id in sighted:
        connection.execute(
            "DELETE FROM transaction_sources "
            "WHERE entity_id = ? AND source = ? AND artefact_digest = ?",
            (entity_id, MANUAL_SOURCE, entry_digest),
        )
        remaining = connection.execute(
            "SELECT COUNT(*) FROM transaction_sources WHERE entity_id = ?", (entity_id,)
        ).fetchone()[0]
        if remaining:
            continue
        connection.execute("DELETE FROM transactions WHERE entity_id = ?", (entity_id,))
        connection.execute("DELETE FROM review_queue WHERE entity_id = ?", (entity_id,))
        connection.execute(
            "DELETE FROM transfer_pairs WHERE debit_entity_id = ? OR credit_entity_id = ?",
            (entity_id, entity_id),
        )
    connection.commit()
