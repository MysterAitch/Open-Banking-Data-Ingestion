"""Five accounts, each built to be one of the states an account's page has, with its answers decided
here before any page reads it. Every date is an offset from today, so the world is the same
whenever it is built, and every figure is invented.

The states are the redesign's prototypes (`docs/design/2026-10-clean-slate/pages/account-*.html`):

- `EVERYDAY` ("Everyday card"): rows every five days from 200 days ago to today. Known balances
  stated at the end of days -140 and -80, both true. So it adds up to -80, nothing tests the rows
  since (a statement is due), the 40 days before the first balance are not tested, and what adds
  up (-140 to -80) is not locked, so locking in is offered, covering the transactions from -140
  to -80.
- `JOINT` ("Joint current"): rows every four days from -200. Known balances at -150 and -100 are
  true; the one at -60 is 12.50 too high. So it adds up to -100 and does not add up from -60,
  more than 45 days ago, which is a thing to do with its own control.
- `HOLIDAY` ("Holiday pot"): rows every six days from -60 and no known balance at all: nothing
  to check against, and the thing to do is to confirm a balance for a day.
- `RAINY` ("Rainy day saver"): rows every seven days from -300 to -9, a known balance stated at
  the end of the first day, at -120, and at -8, all true, and locked in through -8. It adds up
  to -8 and nothing is due.
- `MOVED` ("Rainy day mover"): the same as `RAINY`, then a transaction dated 150 days ago lands
  after the lock was made, so the stretch that was locked in has changed.
"""

from __future__ import annotations

from datetime import date, timedelta

from obdi.core.models import Transaction
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.movement_completeness import MovementCompleteness
from obdi.verify.protection import press
from test_ledger import land, txn

EVERYDAY = "everyday-card"
JOINT = "joint-current"
HOLIDAY = "holiday-pot"
RAINY = "rainy-day-saver"
MOVED = "rainy-day-mover"

LABELS = {
    EVERYDAY: "Everyday card",
    JOINT: "Joint current",
    HOLIDAY: "Holiday pot",
    RAINY: "Rainy day saver",
    MOVED: "Rainy day mover",
}

OPENING_MINOR = 100_000
FAULT_MINOR = 1_250
#: The days before today of the balance the joint account has wrong.
JOINT_FAULT_OFFSET = 60


def money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    pounds, pence = divmod(abs(minor), 100)
    return f"{sign}{pounds}.{pence:02d}"


def _rows(ref: str, today: date, first: int, last: int, step: int) -> list[Transaction]:
    """One transaction every `step` days from `first` days ago to `last` days ago, each a
    different amount so the matcher never opens a review for two equal payments."""
    rows = []
    for number, offset in enumerate(range(first, last - 1, -step)):
        rows.append(
            txn(
                ref,
                "starling",
                f"{ref}-{number}",
                today - timedelta(days=offset),
                -(1000 + 7 * number + len(ref)),
                f"SHOP {number}",
            )
        )
    return rows


def _state(
    store: Store,
    ref: str,
    today: date,
    rows: list[Transaction],
    offsets: list[int],
    *,
    wrong: int = -1,
) -> None:
    """Known balances at the end of the days `offsets` before today, each the true running balance
    but the one at `wrong`, which is `FAULT_MINOR` too high."""
    for offset in offsets:
        day = today - timedelta(days=offset)
        true = OPENING_MINOR + sum(t.amount_minor for t in rows if t.value_date <= day)
        stated = true + (FAULT_MINOR if offset == wrong else 0)
        record_stated_anchor(store, ref, day.isoformat(), money(stated), today=today)


def _lock(store: Store, ref: str, through: date) -> None:
    opening = effective_opening(store, ref)
    press(
        store,
        ref,
        through.isoformat(),
        opening=opening,
        standing=standing_of(opening, [ref], MovementCompleteness()),
    )


def build_states(store: Store, today: date) -> None:
    for ref, label in LABELS.items():
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))

    everyday = _rows(EVERYDAY, today, 200, 0, 5)
    land(store, "everyday", *everyday)
    _state(store, EVERYDAY, today, everyday, [140, 80])

    joint = _rows(JOINT, today, 200, 0, 4)
    land(store, "joint", *joint)
    _state(store, JOINT, today, joint, [150, 100, JOINT_FAULT_OFFSET], wrong=JOINT_FAULT_OFFSET)

    holiday = _rows(HOLIDAY, today, 60, 0, 6)
    land(store, "holiday", *holiday)

    for ref in (RAINY, MOVED):
        rows = _rows(ref, today, 300, 9, 7)
        land(store, ref, *rows)
        _state(store, ref, today, rows, [300, 120, 8])
        _lock(store, ref, today - timedelta(days=8))
    land(
        store,
        "moved-late",
        txn(MOVED, "starling", "late-arrival", today - timedelta(days=150), -4321, "LATE ARRIVAL"),
    )
