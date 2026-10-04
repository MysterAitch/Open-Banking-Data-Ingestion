"""An invented family whose card payments carry round-ups, with the answer decided first.

A main account and one savings Space, September 2026, amounts in pence. The
feed reports a card payment ONCE in the main category, at the payment's own
amount, with a `roundUp` object naming the Space and the spare change moved. The
Space's own feed reports the money arriving as an IN item, and the main
category holds no item for the money leaving. The export is Space-blind: it
lists the payments at their own amounts and a whole-account running balance,
which a round-up does not change.

    day  what                                  payment   round-up   main moves
    1    opening deposit, into main            +100000                +100000
    2    ordinary transfer, main to the Space                           -10000
    3    Coffee, card                             -350        50         -400
    5    Grocer, card                            -2310        90        -2400
    5    Book, card, a round-up of nothing       -1000         0        -1000
    8    Taxi, card                              -1275        25        -1300
    10   Hotel, card, from the Space itself      -3000                      0
    12   Lunch, card                              -800        20         -820

The Space's own payment and the ordinary transfer are there because a source's
balance is only read as the whole account's when steps tell the readings apart
(`balance_meaning`), and the export is blind to the Space.

KNOWN ANSWER, worked by hand before the first run:

    payments in all                       8735   (5735 from main, 3000 from the Space)
    round-ups in all                       185   (four: the nil one is not a movement)
    the whole account at the end        91265   = 100000 - 8735
    main's own balance                  84080   = 100000 - 10000 - 5735 - 185
    the Space's balance                  7185   = 10000 + 185 - 3000
    confirmed transfer pairs                5   (four round-ups and the ordinary transfer)
    every stated whole-account balance agrees with the family's rows
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from obdi.ingest import import_file
from obdi.models import Transaction, TransactionStatus
from obdi.providers import starling
from obdi.store import Store
from test_export_cuts import Row, export_lines
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import MAIN, MAP

SPACE_FEED_ORIGIN = FEED_ORIGIN.replace("cat-main", "cat-bills")

DEPOSIT = 100000
MAIN_BALANCE = 84080
SPACE_BALANCE = 7185
ROUND_UPS = 185


def card_payment(
    uid: str,
    name: str,
    minor: int,
    day: int,
    *,
    round_up: Any = None,
    status: str = "SETTLED",
    **more: Any,
) -> dict[str, Any]:
    """A card payment as the main category's feed reports it."""
    item: dict[str, Any] = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "OUT",
        "transactionTime": f"2026-09-{day:02}T10:00:00.000Z",
        "source": "MASTER_CARD",
        "status": status,
        "counterPartyName": name,
        "reference": name,
    }
    if round_up is not None:
        item["roundUp"] = round_up
    item.update(more)
    return item


def rows_the_provider_makes(
    account: str, uid: str, name: str, minor: int, when: str, **more: Any
) -> list[Transaction]:
    """What `providers.starling` makes of one invented booked payment dated `when` (an ISO date).

    For scenarios built on a hand-chosen account that need a feed row without the household:
    the source and the raw record are the provider's, never invented by the test.
    """
    item = card_payment(uid, name, minor, 1, transactionTime=f"{when}T10:00:00.000Z", **more)
    return starling.to_transactions(item, account_id=account)


def round_up_of(minor: int, space: str = "cat-bills") -> dict[str, Any]:
    return {"goalCategoryUid": space, "amount": {"currency": "GBP", "minorUnits": minor}}


def deposit_item() -> dict[str, Any]:
    return {
        "feedItemUid": "f-deposit",
        "amount": {"currency": "GBP", "minorUnits": DEPOSIT},
        "direction": "IN",
        "transactionTime": "2026-09-01T09:45:00.000Z",
        "source": "FASTER_PAYMENTS_IN",
        "status": "SETTLED",
        "counterPartyName": "Employer",
        "reference": "Opening deposit",
    }


def main_feed() -> list[dict[str, Any]]:
    return [
        deposit_item(),
        card_payment(
            "f-topup",
            "Savings",
            10000,
            2,
            source="INTERNAL_TRANSFER",
            counterPartyType="CATEGORY",
            counterPartyUid="cat-bills",
        ),
        card_payment("f-coffee", "Coffee", 350, 3, round_up=round_up_of(50)),
        card_payment("f-grocer", "Grocer", 2310, 5, round_up=round_up_of(90)),
        card_payment("f-book", "Book", 1000, 5, round_up=round_up_of(0)),
        card_payment("f-taxi", "Taxi", 1275, 8, round_up=round_up_of(25)),
        card_payment("f-lunch", "Lunch", 800, 12, round_up=round_up_of(20)),
    ]


def space_arrival(uid: str, minor: int, day: int, **more: Any) -> dict[str, Any]:
    """Money arriving in the Space, as the Space's own category feed reports it."""
    item: dict[str, Any] = {
        "feedItemUid": uid,
        "amount": {"currency": "GBP", "minorUnits": minor},
        "direction": "IN",
        "transactionTime": f"2026-09-{day:02}T10:00:01.000Z",
        "source": "INTERNAL_TRANSFER",
        "status": "SETTLED",
        "counterPartyType": "CATEGORY",
        "counterPartyUid": "cat-main",
        "counterPartyName": "Main account",
    }
    item.update(more)
    return item


def space_feed() -> list[dict[str, Any]]:
    return [
        space_arrival("s-topup", 10000, 2),
        space_arrival("s-coffee", 50, 3),
        space_arrival("s-grocer", 90, 5),
        space_arrival("s-taxi", 25, 8),
        card_payment("s-hotel", "Hotel", 3000, 10),
        space_arrival("s-lunch", 20, 12),
    ]


def feed_body(items: list[dict[str, Any]]) -> bytes:
    return json.dumps({"feedItems": items}).encode("utf-8")


def land_feed(
    store: Store,
    items: list[dict[str, Any]],
    *,
    origin: str,
    asked: str = "2026-09-01T00:00:00Z",
    window: bool = False,
) -> bytes:
    """Land a feed fetch asked `changesSince` `asked`, or (with `window`) as a window of
    transaction time from `asked` to the end of September, which lists what exists."""
    body = feed_body(items)
    ask = (
        f"minTransactionTimestamp={asked}&maxTransactionTimestamp=2026-09-30T23:59:59.000Z"
        if window
        else f"changesSince={asked}"
    )
    store.land_artefact(
        starling.artefact_for(
            body,
            account_id="starling:cat-main" if origin == FEED_ORIGIN else "starling:cat-bills",
            kind="feed",
            origin=f"{origin}?{ask}",
        )
    )
    return body


def export_of_the_payments(directory: pathlib.Path, extra: tuple[Row, ...] = ()) -> pathlib.Path:
    """The Space-blind export: every payment at its own amount, no round-up.

    `extra` are further rows the export lists, placed among the others by the
    day it dates them.
    """
    rows = [
        Row("Deposit", DEPOSIT, 1, 1),
        Row("Coffee", -350, 3, 3),
        Row("Grocer", -2310, 5, 5),
        Row("Book", -1000, 5, 5),
        Row("Taxi", -1275, 8, 8),
        Row("Hotel", -3000, 10, 10),
        Row("Lunch", -800, 12, 12),
    ]
    rows = sorted([*rows, *extra], key=lambda row: row.export_day)
    path = directory / "export.csv"
    path.write_text("\n".join(export_lines(rows)) + "\n", encoding="utf-8")
    return path


def household_store(
    directory: pathlib.Path,
    main: list[dict[str, Any]] | None = None,
    space: list[dict[str, Any]] | None = None,
    export_last: bool = False,
    export_extra: tuple[Row, ...] = (),
    main_refetches: tuple[list[dict[str, Any]], ...] = (),
    refetch_windows: bool = False,
) -> Store:
    """The corpus held as raw artefacts and the export, nothing derived yet.

    `main` and `space` replace the two feeds' items where a scenario varies them.
    `export_last` has the export arrive after both feeds, so a rebuild replays
    it as the later sighting of every payment.
    `export_extra` adds rows to the export, and each of `main_refetches` is a
    later fetch of the main feed, landed after every other feed, in the order given, each asked
    `changesSince` its day or, with `refetch_windows`, as a window of transaction time.
    """
    store = Store(directory / "household.sqlite3")
    land_evidence(store)
    if not export_last:
        import_file(
            store, export_of_the_payments(directory, export_extra), account_id=MAIN, account_map=MAP
        )
    land_feed(
        store,
        main if main is not None else main_feed(),
        origin=FEED_ORIGIN,
        asked="2026-09-02T00:00:00Z",
    )
    land_feed(store, space if space is not None else space_feed(), origin=SPACE_FEED_ORIGIN)
    for later, items in enumerate(main_refetches, start=3):
        land_feed(
            store,
            items,
            origin=FEED_ORIGIN,
            asked=f"2026-09-{later:02}T00:00:00" + (".000Z" if refetch_windows else "Z"),
            window=refetch_windows,
        )
    if export_last:
        import_file(
            store, export_of_the_payments(directory, export_extra), account_id=MAIN, account_map=MAP
        )
    return store


def counted(store: Store, ref: str) -> list[Transaction]:
    return [t for t in store.transactions_for_account(ref) if not t.status.is_history]


def balance(store: Store, ref: str) -> int:
    return sum(t.amount_minor for t in counted(store, ref))


def booked(store: Store, ref: str) -> list[Transaction]:
    return [t for t in counted(store, ref) if t.status is TransactionStatus.BOOKED]
