"""An invented current account with no Spaces, a bank balance, and one statement.

September 2026, amounts in pence, every time UTC. The bank's own balance
defines the opening and the single held statement is the one check, which is the
shape of a real current account whose anchors differ.

    id        day  at     what                 amount
    deposit    1   09:45  opening deposit      +80000
    salary     1   10:00  salary              +300000
    cafe       4   12:00  cafe                  -2500
    bakery     6   12:00  bakery                -1800
    grocer    15   12:00  grocer                -7000
    garage    22   12:00  garage                -9000
    refund    25   11:00  refund              +120000

KNOWN ANSWER, worked by hand before the first run:

    bank balance fetched on the 2nd at 11:00           380000   (deposit and salary)
    the statement, 5 to 30 September, opens at          3775.00  (the end of the 4th)
    and closes at                                       4797.00
        (3775.00 - 18.00 - 70.00 - 90.00 + 1200.00)
    the rows agree with both, so nothing differs

The statement lists its first row on the 6th, so a row dated before then is outside what it
covers and is not one it omits (`fault_explanation._view`).

Each scenario then adds one fault to the FEED and says which row the explanation
must name. The statement and the bank state the truth throughout.
"""

from __future__ import annotations

import pathlib
from datetime import datetime
from typing import Any

from bank_balance_corpus import at, balance_body, item, land_balance, land_feed
from obdi.accounts import AccountBinding, AccountMap
from obdi.ingest import import_file
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN
from test_starling_statement import build_starling_pdf

OWN_MAP = AccountMap(
    [AccountBinding(MAIN, "starling", "acc-main"), AccountBinding(MAIN, "truelayer", "tl-main")]
)

BANK_ON_THE_2ND = 380000
CLOSING = 479700

STATEMENT = [
    "SUMMARY|05/09/2026 - 30/09/2026|3775.00|1200.00|178.00|4797.00",
    "HEAD",
    "OPENING|3775.00",
    "ROW|06/09/2026|FASTER PAYMENT|Bakery||18.00|3757.00",
    "ROW|15/09/2026|FASTER PAYMENT|Grocer||70.00|3687.00",
    "ROW|22/09/2026|FASTER PAYMENT|Garage||90.00|3597.00",
    "ROW|25/09/2026|DIRECT CREDIT|Refund|1200.00||4797.00",
    "END",
]


def feed_items() -> list[dict[str, Any]]:
    return [
        item("o-deposit", 80000, at(1, 9, 45), name="Deposit"),
        item("o-salary", 300000, at(1, 10), name="Salary"),
        item("o-cafe", -2500, at(4), name="Cafe"),
        item("o-bakery", -1800, at(6), name="Bakery"),
        item("o-grocer", -7000, at(15), name="Grocer"),
        item("o-garage", -9000, at(22), name="Garage"),
        item("o-refund", 120000, at(25, 11), name="Refund"),
    ]


def own_household(
    directory: pathlib.Path,
    *,
    extra: list[dict[str, Any]] | None = None,
    feed: list[dict[str, Any]] | None = None,
    statement: list[str] | None = None,
    balances: list[tuple[datetime, bytes]] | None = None,
    feed_fetched: datetime | None = None,
) -> Store:
    """The feed (`feed` or the corpus's, with any `extra` items), the statement, and the
    bank's balances, replayed."""
    store = Store(directory / "own.sqlite3")
    land_evidence(store)
    land_feed(
        store,
        [*(feed if feed is not None else feed_items()), *(extra or [])],
        origin=FEED_ORIGIN,
        fetched=feed_fetched or at(28),
    )
    for fetched, body in balances if balances is not None else [
        (at(2, 11), balance_body(BANK_ON_THE_2ND, BANK_ON_THE_2ND))
    ]:
        land_balance(store, body, fetched)
    path = directory / "statement.pdf"
    path.write_bytes(build_starling_pdf(statement or STATEMENT))
    import_file(store, path, account_id=MAIN, account_map=OWN_MAP)
    rebuild_from_raw(store, account_map=OWN_MAP)
    return store
