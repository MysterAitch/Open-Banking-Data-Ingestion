"""A week of one payee paid one amount on consecutive days, each settling the day after it is made.

The commonest pattern in real spending, and the one the join of two stored rows has to leave
alone: payment one's settlement day IS payment two's transaction day, so the settlement rule
names the other payment's row for every payment.

    payee   Coffee Co, invented; amount 450 pence; paid on 15 to 21 September 2026 at 10:00
    feed    each payment's own day, settled at 03:00 the next day, its own uid
    export  each payment on its settlement day (16 to 22 September)
    aggregator
            each payment on its own day, stating the feed's uid for it

KNOWN ANSWER, decided before the first run: one stored row per payment, seven in all, each
sighted by all three sources and none by another payment's; nothing joined; nothing lost;
the rows each source lists equal the rows held.
"""

from __future__ import annotations

from datetime import date

from late_settlement_corpus import Payment

AMOUNT = 450
FIRST_DAY = 15
PAYEE = "Coffee Co"


def consecutive_payments(days: int = 7) -> list[Payment]:
    return [
        Payment(
            f"f-coffee-{day}",
            PAYEE,
            AMOUNT,
            f"2026-09-{day:02}T10:00:00.000Z",
            f"2026-09-{day + 1:02}T03:00:00.000Z",
            date(2026, 9, day + 1),
            day,
        )
        for day in range(FIRST_DAY, FIRST_DAY + days)
    ]
