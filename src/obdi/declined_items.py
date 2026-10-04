"""A payment the bank later declined is history, not money.

THE RULE. The newest landed statement of a feed item's status is its row's status: a row
made from an earlier fetch whose item the bank's newest landed feed gives a status that makes
no row (`feed_statuses.makes_no_row`, so DECLINED, ACCOUNT_CHECK, or a status the provider's
map does not list) is VOID, whether it was booked or pending.
Measured as a defect by `test_no_row_items`: the provider maps such a status to no record, so
the later fetch said nothing the matcher could apply and the row stayed counted.
The reverse needs no rule: an item declined first and settled later has the settled fetch's
row, and the newest statement is that it settled.

WHAT IS RECORDED, AND WHERE. The row's status is VOID, and the reason is not stored: it is
the item's newest landed status, which `declined_void_rows` reads back from the feed by the
same detection (`declined_void_entities`). A column for it was rejected because the schema does
not change for this, and a stored reason would be a second copy that a later fetch settling the
item would leave wrong.

A row another source has also sighted is NOT voided
(`feed_statuses.RowWithNoRowStatus.corroborated`).
A bank's export or an aggregator listing the payment says money moved, which contradicts the
feed's statement; nothing in the evidence says which is right, so the row stays counted and is
queued for a person (`DECLINED_DOUBT`). Starting strict is the point: voiding it would hide a
payment the bank's own statement lists.

A ROUND-UP LEG is never touched. The leg of a reversed or dropped payment is a booked leg of its
own on purpose (`providers.starling._round_up_leg`: the Space received the money), and its derived
uid is no feed item's, so no detection names it.
A TRANSFER PAIR is not offered a declined row (`declined_void_entities` is what
`ingest.pair_transfers_across_store` excludes), since the money never moved.

WHY A PASS AND NOT A RECORD. The alternative was for the provider to yield a withdrawal record for
an item that makes no row and whose uid the store holds, so the matcher voids the row by its own id.
Rejected: the provider has no store, so every declined item would become a record the matcher must
then discard unless held; the record would carry only its own fetch's say, where "newest" is a
property of the landing order across fetches, which `feed_statuses` already decides once; and it
touches the nine places a batch is reconciled, where a pass is one call beside the other
post-batch passes and reads the finished state, so a live pull and a rebuild agree by construction.
WHAT IT READS. Read whole, every landed feed artefact of an account cost one statement and one
parse each, at every door: 400 artefacts of five items took 0.155 s and 406 statements cold,
growing in step. So the pass is gated (`feed_statuses.uids_with_a_no_row_status_anywhere`): one
statement scans the landed bodies as bytes, only the bodies that may hold a no-row item are
parsed, and an account's artefacts are read whole only if a counted row of it was sighted
under such a uid. Most such items (a declined card attempt) never made a row, so the whole
reading is the exception. `test_declined_items` holds the statement count flat as artefacts grow.
"""

from __future__ import annotations

from dataclasses import dataclass

from .feed_statuses import (
    FeedStatuses,
    accounts_holding_counted_rows_of,
    makes_no_row,
    rows_with_no_row_status,
    uids_with_a_no_row_status_anywhere,
)
from .matching import EXACT_RULE_DOUBT
from .models import TransactionStatus
from .store import Store

#: The start of the reason of a review flag raised for a row left counted although the newest
#: landed feed gives its item a status that makes no row. It ends with `EXACT_RULE_DOUBT`, which is
#: what stops the duplicate-report passes closing it for want of a neighbour
#: (`review_report.assess_flags`): two sources disagree and only a person can say which is right.
DECLINED_DOUBT = "the bank's newest feed says this payment is"


@dataclass
class DeclinedOutcome:
    voided: int = 0
    #: Rows left counted because another source also sighted them, and queued for review.
    left_counted: int = 0

    def describe(self) -> str:
        return (
            f"{self.voided} row(s) whose feed item the bank later declined voided, "
            f"{self.left_counted} left counted for review"
        )


def void_declined_items(store: Store) -> DeclinedOutcome:
    """Void every counted row whose feed item the newest landed feed gives a no-row status.

    A pure function of the landed feeds and the stored rows, so it is called after every batch
    and once after a rebuild and reaches the same state either way. It writes only the status of
    a row that is not history, and a review flag; sightings are never touched.
    """
    outcome = DeclinedOutcome()
    declared = uids_with_a_no_row_status_anywhere(store)
    for account in accounts_holding_counted_rows_of(store, declared):
        for found in rows_with_no_row_status(store, account):
            if found.corroborated:
                store.queue_for_review(
                    found.row.entity_id,
                    f"{DECLINED_DOUBT} {found.status}, which makes no row, but another source "
                    f"also lists it, so it was left counted {EXACT_RULE_DOUBT}",
                )
                outcome.left_counted += 1
                continue
            store.connection.execute(
                "UPDATE transactions SET status = ? WHERE entity_id = ?",
                (TransactionStatus.VOID.value, found.row.entity_id),
            )
            outcome.voided += 1
    store.connection.commit()
    return outcome


def declined_void_entities(store: Store) -> frozenset[str]:
    """The void rows whose own feed item the newest landed feed still gives a no-row status.

    The reason a row is void, read back: `void_declined_items` stores none. A void row whose item
    was later settled is not one of these, and a row voided because it vanished from a pending set
    never is, because its item makes a row.
    """
    voids = [
        (str(row[0]), str(row[1]))
        for row in store.connection.execute(
            "SELECT entity_id, account_id FROM transactions WHERE status = ?",
            (TransactionStatus.VOID.value,),
        )
    ]
    if not voids:
        return frozenset()
    statuses = FeedStatuses(store, sorted({account for _, account in voids}))
    found: set[str] = set()
    for entity, _ in voids:
        item = statuses.item_of(entity)
        if item is not None and makes_no_row(item.status):
            found.add(entity)
    return frozenset(found)
