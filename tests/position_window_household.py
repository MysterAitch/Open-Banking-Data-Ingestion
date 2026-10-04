"""An invented household for the position chart's windows, worked out by hand.

Pence, today 2026-10-04 (a Sunday). Each account's opening is derived from its one
stated balance, and applies from the day before its first row (or the day it was
stated, where it holds no row). These are the figures the first run is held to,
written BEFORE it ran:

    everyday   stated 2026-09-30: 500.00; rows 08-10 +300.00, 09-10 -100.00,
               09-20 +200.00, 10-02 -50.00; opening 100.00 from 08-09
        08-09: 10,000   08-10 to 09-09: 40,000   09-10 to 09-19: 30,000
        09-20 to 10-01: 50,000   10-02 on: 45,000
    card       stated 2026-09-30: 0.00; rows 09-12 -8,000.00 (a purchase), 09-26
               +8,000.00 (paid off); opening nil from 09-11
        09-11: 0   09-12 to 09-25: -800,000   09-26 on: 0
    saver      stated 2026-09-14: 1,000.00; row 09-15 +100.00
        09-14: 100,000   09-15 on: 110,000   (nothing before 09-14: it opens mid-window)
    pension    a balance-only account, stated 2026-08-01: 2,000.00 and again
               2026-09-15: 2,300.00 (one unitemised change of +300.00 on 09-15)
        08-01 to 09-14: 200,000   09-15 on: 230,000
    bond       an asset, valued 2026-06-15 at 2,800.00 and 2026-09-01 at 3,200.00
        06-15 to 08-31: 280,000   09-01 on: 320,000
    mystery    NOT COUNTED (no stated balance): rows 08-20 +70.00, 09-18 -20.00
        moved: 08-20 to 09-17: 7,000   09-18 on: 5,000

The five counted items give, on the days that matter (included of 5 in brackets):

    06-15 to 07-31   280,000 (1)        08-01        480,000 (2)
    08-09            490,000 (3)        08-10 to 08-31   520,000 (3)
    09-01 to 09-09   560,000 (3)        09-10, 09-11     550,000 (4: card from 09-11)
    09-12, 09-13    -250,000 (4)        09-14       -150,000 (5)
    09-15 to 09-19  -110,000 (5)        09-20 to 09-25    -90,000 (5)
    09-26 to 10-01   710,000 (5)        10-02 to 10-04   705,000 (5)

The household is below nil from 09-12 to 09-25, fourteen days. At month-ends it
never is: 06: 280,000   07: 280,000   08: 520,000   09: 710,000   10 (today): 705,000.
At Sundays from 2026-06-21 (the first with a figure), sixteen of them:
    06-21 to 07-26   280,000    08-02  480,000   08-09  490,000
    08-16 to 08-30   520,000    09-06  560,000   09-13 -250,000
    09-20            -90,000    09-27  710,000   10-04  705,000

The provisional line adds the uncounted account's movement (7,000 from 08-20, 5,000
from 09-18) to each.
"""

from __future__ import annotations

from datetime import date

from obdi.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.balance_anchors import record_stated_anchor
from obdi.store import Store
from obdi.valuations import Asset, AssetKind, record_observation
from test_ledger import land, txn

D = date
TODAY = D(2026, 10, 4)

EVERYDAY = "account:everyday"
CARD = "account:card"
SAVER = "account:saver"
PENSION = "account:pension"
BOND = "asset:bond"
MYSTERY = "account:mystery"

#: The household's total on each day it changes (see the docstring), counted items only.
KNOWN = {
    D(2026, 6, 15): 280000,
    D(2026, 7, 31): 280000,
    D(2026, 8, 1): 480000,
    D(2026, 8, 9): 490000,
    D(2026, 8, 10): 520000,
    D(2026, 8, 31): 520000,
    D(2026, 9, 1): 560000,
    D(2026, 9, 9): 560000,
    D(2026, 9, 10): 550000,
    D(2026, 9, 11): 550000,
    D(2026, 9, 12): -250000,
    D(2026, 9, 13): -250000,
    D(2026, 9, 14): -150000,
    D(2026, 9, 15): -110000,
    D(2026, 9, 19): -110000,
    D(2026, 9, 20): -90000,
    D(2026, 9, 25): -90000,
    D(2026, 9, 26): 710000,
    D(2026, 10, 1): 710000,
    D(2026, 10, 2): 705000,
    D(2026, 10, 4): 705000,
}


def window_household(store: Store) -> None:
    land(
        store,
        "d-everyday",
        txn("everyday", "s", "e1", D(2026, 8, 10), 30000, "PAY"),
        txn("everyday", "s", "e2", D(2026, 9, 10), -10000, "SHOP"),
        txn("everyday", "s", "e3", D(2026, 9, 20), 20000, "PAY"),
        txn("everyday", "s", "e4", D(2026, 10, 2), -5000, "SHOP"),
    )
    land(
        store,
        "d-card",
        txn("card", "s", "c1", D(2026, 9, 12), -800000, "PURCHASE"),
        txn("card", "s", "c2", D(2026, 9, 26), 800000, "PAYMENT"),
    )
    land(store, "d-saver", txn("saver", "s", "v1", D(2026, 9, 15), 10000, "SAVED"))
    land(
        store,
        "d-mystery",
        txn("mystery", "s", "y1", D(2026, 8, 20), 7000, "IN"),
        txn("mystery", "s", "y2", D(2026, 9, 18), -2000, "OUT"),
    )
    store.declare_account(AccountRecord(ref=AccountRef("pension"), kind=BALANCE_ONLY_KIND))
    record_stated_anchor(store, "everyday", "2026-09-30", "500.00", today=TODAY)
    record_stated_anchor(store, "card", "2026-09-30", "0.00", today=TODAY)
    record_stated_anchor(store, "saver", "2026-09-14", "1000.00", today=TODAY)
    record_stated_anchor(store, "pension", "2026-08-01", "2000.00", today=TODAY)
    record_stated_anchor(store, "pension", "2026-09-15", "2300.00", today=TODAY)
    bond = Asset("bond", AssetKind.INVESTMENT)
    record_observation(
        store, bond, observed_at=D(2026, 6, 15), source="statement", value_minor=280000
    )
    record_observation(
        store, bond, observed_at=D(2026, 9, 1), source="statement", value_minor=320000
    )


def known_on(day: date) -> int:
    """The hand-worked total on `day`: the latest day in `KNOWN` on or before it."""
    return KNOWN[max(d for d in KNOWN if d <= day)]
