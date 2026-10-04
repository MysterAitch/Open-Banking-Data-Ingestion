"""An invented card whose aggregator lists a payment under two ids, with the answers decided first.

A card account fed by an aggregator's card feed (`truelayer-card-booked`) and by two statements
(`santander-cc-pdf`), with no first-party feed and so no id tier. All amounts and names are
invented, in pence.

    statements   closing 2025-10-10 and 2025-11-10
                 the second lists a Post Office row (777, 2025-10-20), a Bakery row (333,
                 2025-10-21), and the PAYMENT: one row of 2000 on 2025-10-30, or two
    aggregator   a fetch listing the Post Office row and the first of the payment's ids, and
                 another listing the Post Office row and the second id: two fetches that
                 never list the two ids together, so the largest count one artefact lists
                 is one

The two ids of 2000 each are the aggregator's own, distinct; whether they are two payments or
one listed twice is what the statement's balance says.

KNOWN ANSWERS, decided before the first run:

    the statement counts ONE payment of 2000
        the store holds two rows of it from the aggregator, so the balance of 2025-11-10 differs
        from the rows as held by exactly that row: the aggregator listed one payment twice
    the statement counts TWO payments of 2000
        the store holds two rows of it from the aggregator and the balances are met by them:
        the aggregator's two ids are two payments
"""

from __future__ import annotations

import json
import pathlib
from datetime import date
from typing import Any

from card_chain_corpus import printed, statement_day, text_day
from obdi.ingest import import_file, reconcile_batch
from obdi.providers import truelayer
from obdi.rebuild import parse_artefact_transactions, rebuild_from_raw
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf

CARD = "invented-card"
PAYMENT = 2000
PAYMENT_DAY = date(2025, 10, 30)
POST_OFFICE = (777, date(2025, 10, 20), "Post Office")
BAKERY = (333, date(2025, 10, 21), "Bakery")
FIRST_CLOSING = date(2025, 10, 10)
SECOND_CLOSING = date(2025, 11, 10)
OPENING_OWED = 10000
FIRST_PERIOD_ROW = (1111, date(2025, 9, 25), "Opening Purchase")


def item(item_id: str | None, size: int, day: date, name: str) -> dict[str, Any]:
    """One card record as the aggregator's card endpoint states it: a debit arrives positive."""
    record: dict[str, Any] = {
        "transaction_id": f"volatile-{name}-{item_id}",
        "timestamp": f"{day.isoformat()}T10:00:00Z",
        "description": name.upper(),
        "amount": f"{size / 100:.2f}",
        "currency": "GBP",
        "transaction_type": "DEBIT",
    }
    if item_id is not None:
        record["normalised_provider_transaction_id"] = item_id
    return record


def post_office(item_id: str = "tl-post") -> dict[str, Any]:
    return item(item_id, *POST_OFFICE)


def bakery(item_id: str = "tl-bakery") -> dict[str, Any]:
    return item(item_id, *BAKERY)


def payment(item_id: str | None, name: str = "Garage") -> dict[str, Any]:
    return item(item_id, PAYMENT, PAYMENT_DAY, name)


def statements(directory: pathlib.Path, *, payments_printed: int) -> list[pathlib.Path]:
    """Two held statements, the second printing `payments_printed` rows of the payment."""
    paths = []
    owed = OPENING_OWED
    for number, (closing, rows) in enumerate(
        (
            (FIRST_CLOSING, [FIRST_PERIOD_ROW]),
            (
                SECOND_CLOSING,
                [
                    POST_OFFICE,
                    BAKERY,
                    *[(PAYMENT, PAYMENT_DAY, "Garage")] * payments_printed,
                ],
            ),
        )
    ):
        closing_owed = owed + sum(size for size, _, _ in rows)
        lines = [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {statement_day(closing)}      Page No: 1 / 1",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {owed / 100:,.2f}",
            *(f"{text_day(day)} {name}   {printed(size)}" for size, day, name in rows),
            f"Your new balance:                                        {closing_owed / 100:,.2f}",
        ]
        path = directory / f"statement-{number}.pdf"
        path.write_bytes(build_pdf(lines))
        paths.append(path)
        owed = closing_owed
    return paths


def land_aggregator(store: Store, records: list[dict[str, Any]], *, derive: bool = True) -> str:
    """Land one card fetch through the door a pull uses, and (by default) derive its rows."""
    artefact = truelayer.artefact_for(
        json.dumps({"results": records}).encode(),
        account_id="invented-card-provider-id",
        kind="card-booked",
        account_ref=CARD,
    )
    store.land_artefact(artefact)
    if derive:
        reconcile_batch(
            store,
            parse_artefact_transactions(artefact.source, artefact.payload, CARD, artefact.digest),
            digest=artefact.digest,
        )
    return artefact.digest


def card_store(
    directory: pathlib.Path,
    order: tuple[str, ...],
    *,
    payments_printed: int,
    fetches: list[list[dict[str, Any]]],
    rebuild: bool = False,
) -> Store:
    """The statements and the aggregator's fetches arriving in `order`, then maybe a rebuild.

    `order` is ("statements", "aggregator") or the reverse.
    """
    store = Store(directory / "card.sqlite3")
    paths = statements(directory, payments_printed=payments_printed)
    for step in order:
        if step == "statements":
            for path in paths:
                import_file(store, path, account_id=CARD)
        else:
            for records in fetches:
                land_aggregator(store, records)
    if rebuild:
        assert rebuild_from_raw(store).problems == []
    return store
