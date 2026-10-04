"""An invented Starling household whose bank states its own balance, with the answer decided first.

A main account and one Space, September 2026, amounts in pence, every time UTC.
The feed's own items carry the instant each payment happened, and the balance
the bank states with each pull is an instant too, so a balance is judged against
the rows as they stood at ITS moment.

    id        day  at     what                              main      Bills
    deposit    1   09:45  opening deposit                  +80000
    salary     1   10:00  salary                          +300000
    topup      2   10:00  transfer main -> Bills (legs)    -40000    +40000
    cafe       4   12:00  cafe                              -2500
    water     12   12:00  water bill, from Bills                      -5000
    grocer    15   12:00  grocer                            -7000
    gym       20   12:00  gym, from Bills                             -3000
    garage    22   12:00  garage                            -9000
    refund    25   11:00  refund                         +120000

KNOWN ANSWER, worked by hand before the first run:

    main at the end of the 22nd     321500 = 80000 + 300000 - 40000 - 2500 - 7000 - 9000
    Bills at the end of the 22nd     32000 = 40000 - 5000 - 3000
    the whole account                353500
    main at the end of the 25th     441500 (the refund of 120000 is in at 11:00)
    the whole account               473500

At the end of the 20th main holds 330500 (80000 + 300000 - 40000 - 2500 - 7000) and
Bills 32000, so the whole account is 362500.

An unchanged balance lands as one artefact, at the first moment it was stated
(`Store.land_artefact` is idempotent on the bytes), so every balance below is
distinct from the ones around it.

The bank states, with every pull, `clearedBalance` (the main account's own
settled balance) and `totalClearedBalance` (the main account and its Spaces).
The first reading of the two was a belief and not a fact, so the meaning is
tested against the Spaces' rows, which the corpus makes true.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from obdi.ingest import import_file
from obdi.providers import starling
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from test_export_cuts import Row, export_lines
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

UID = "acc-main"
BILLS_FEED_ORIGIN = FEED_ORIGIN.replace("cat-main", "cat-bills")

#: Before the gym (20th, 12:00 counted) and the garage (22nd): main 330500, Bills 32000.
CLEARED_20 = 330500
TOTAL_20 = 362500
CLEARED_22 = 321500
TOTAL_22 = 353500
CLEARED_25_MORNING = CLEARED_22
CLEARED_25 = 441500
TOTAL_25 = 473500


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def item(
    uid: str,
    minor: int,
    when: datetime,
    *,
    status: str = "SETTLED",
    settled: datetime | None = None,
    name: str | None = None,
    **more: Any,
) -> dict[str, Any]:
    """One feed item, as the bank reports it: unsigned amount, a direction, its own times."""
    found: dict[str, Any] = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": abs(minor)},
        "direction": "IN" if minor >= 0 else "OUT",
        "transactionTime": when.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "source": "FASTER_PAYMENTS_IN" if minor >= 0 else "FASTER_PAYMENTS_OUT",
        "status": status,
        "counterPartyName": name or uid,
        "reference": name or uid,
    }
    if settled is not None:
        found["settlementTime"] = settled.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    found.update(more)
    return found


def main_items() -> list[dict[str, Any]]:
    return [
        item("m-deposit", 80000, at(1, 9, 45)),
        item("m-salary", 300000, at(1, 10)),
        item(
            "m-topup", -40000, at(2, 10), source="INTERNAL_TRANSFER",
            counterPartyType="CATEGORY", counterPartyUid="cat-bills",
        ),
        item("m-cafe", -2500, at(4)),
        item("m-grocer", -7000, at(15)),
        item("m-garage", -9000, at(22)),
        item("m-refund", 120000, at(25, 11)),
    ]


def bills_items() -> list[dict[str, Any]]:
    return [
        item(
            "b-topup", 40000, at(2, 10, 0), source="INTERNAL_TRANSFER",
            counterPartyType="CATEGORY", counterPartyUid="cat-main",
        ),
        item("b-water", -5000, at(12)),
        item("b-gym", -3000, at(20)),
    ]


#: What the Space-blind export lists: every external payment, from either pot.
EXPORT_ROWS = [
    Row("Deposit", 80000, 1, 1),
    Row("Salary", 300000, 1, 1),
    Row("Cafe", -2500, 4, 4),
    Row("Water", -5000, 12, 12, space=True),
    Row("Grocer", -7000, 15, 15),
    Row("Gym", -3000, 20, 20, space=True),
    Row("Garage", -9000, 22, 22),
    Row("Refund", 120000, 25, 25),
]


def money(minor: int, currency: str = "GBP") -> dict[str, Any]:
    return {"currency": currency, "minorUnits": minor}


def balance_body(
    cleared: int,
    total: int,
    *,
    pending: int = 0,
    currency: str = "GBP",
    omit: tuple[str, ...] = (),
) -> bytes:
    """The balance call's body: the main account's own figures, and the whole account's."""
    body: dict[str, Any] = {
        "clearedBalance": money(cleared, currency),
        "effectiveBalance": money(cleared + pending, currency),
        "pendingTransactions": money(pending, currency),
        "acceptedOverdraft": money(0, currency),
        "amount": money(cleared + pending, currency),
        "totalClearedBalance": money(total, currency),
        "totalEffectiveBalance": money(total + pending, currency),
    }
    for field in omit:
        del body[field]
    return json.dumps(body).encode("utf-8")


def land_feed(store: Store, items: list[dict[str, Any]], *, origin: str, fetched: datetime) -> None:
    category = "starling:cat-main" if origin == FEED_ORIGIN else "starling:cat-bills"
    artefact = starling.artefact_for(
        json.dumps({"feedItems": items}).encode("utf-8"),
        account_id=category,
        kind="feed",
        origin=f"{origin}?changesSince=2026-09-01T00:00:00Z",
    )
    store.land_artefact(replace(artefact, fetched_at=fetched))


def land_balance(store: Store, body: bytes, fetched: datetime) -> None:
    artefact = starling.artefact_for(
        body,
        account_id=f"starling:{UID}",
        kind="balance",
        origin=f"{starling.API_HOST}/api/v2/accounts/{UID}/balance",
    )
    store.land_artefact(replace(artefact, fetched_at=fetched))


def household(
    directory: pathlib.Path,
    balances: list[tuple[datetime, bytes]],
    *,
    main: list[dict[str, Any]] | None = None,
    bills: list[dict[str, Any]] | None = None,
    export: list[Row] | None = None,
    feed_fetched: datetime | None = None,
) -> Store:
    """The corpus as landed evidence, replayed in arrival order.

    `export` is the rows the Space-blind export lists; None means no export is held.
    The feeds are fetched at `feed_fetched` (the end of the month unless said), after
    every balance, which is how a pull that came later finds rows the balance
    cannot have counted.
    """
    store = Store(directory / "household.sqlite3")
    land_evidence(store)
    land_feed(
        store, main if main is not None else main_items(), origin=FEED_ORIGIN,
        fetched=feed_fetched or at(28),
    )
    land_feed(
        store, bills if bills is not None else bills_items(), origin=BILLS_FEED_ORIGIN,
        fetched=feed_fetched or at(28),
    )
    for fetched, body in balances:
        land_balance(store, body, fetched)
    if export is not None:
        path = directory / "export.csv"
        path.write_text("\n".join(export_lines(export)) + "\n", encoding="utf-8")
        import_file(store, path, account_id=MAIN, account_map=MAP)
    rebuild_from_raw(store, account_map=MAP)
    return store
