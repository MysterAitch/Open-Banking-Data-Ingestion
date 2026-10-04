"""A small household whose open review flags are decided here, before anything is built.

Everything is landed through the doors a person's data comes in by: the CSV importer, and an
aggregator and a bank feed response landed as raw artefacts. Every figure and payee is a token
that appears nowhere else, so a page that prints one can be caught.

    Everyday account   the aggregator listed a payment under one id, and a later response listed
                       it again under another, never together              1 open flag, 1 neighbour
    Bakery card        the export lists the same payment three times in one file
                                                                           2 open flags, each with
                                                                           2 neighbours
    Tickets            the aggregator listed a payment, and an export listing it and a second one
                       a day later                                         1 open flag, 1 neighbour
    Settled bus        the bank's own feed named two payments by two ids   settled by the evidence
    Settled train      one response listed two payments under two ids      settled by the evidence

Four flags are real questions, and the rebuild that ends the build closes the two the evidence
answers. `add_unsettled_pair` raises one of those again, through the matching door, so that a
flag the evidence has answered can be seen standing in the queue.
"""

from __future__ import annotations

import json
from pathlib import Path

from obdi.accounts import AccountRecord, AccountRef
from obdi.ingest import import_file
from obdi.providers import starling, truelayer
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store

EVERYDAY = "everyday"
BAKERY = "bakery"
TICKETS = "tickets"
SETTLED_BUS = "settled-bus"
SETTLED_TRAIN = "settled-train"

LABELS = {
    EVERYDAY: "Everyday account",
    BAKERY: "Bakery card",
    TICKETS: "Ticket account",
    SETTLED_BUS: "Settled bus",
    SETTLED_TRAIN: "Settled train",
}

#: Each payee and figure appears nowhere else, in any case or format.
PAYEES = {
    EVERYDAY: "Zephyrine Quokka Ltd",
    BAKERY: "Marmalade Foundry",
    TICKETS: "Wyvern Chandlery",
}
FIGURES = {EVERYDAY: "21.37", BAKERY: "38.46", TICKETS: "54.19"}

OPEN_QUESTIONS = 4
SETTLED_BY_EVIDENCE = 2


def _truelayer(store: Store, ref: str, records: list[dict], cycle: int) -> None:
    store.land_artefact(
        truelayer.artefact_for(
            json.dumps({"results": records, "status": "Succeeded"}).encode(),
            account_id=ref,
            kind="booked",
            requested=f"c={cycle}",
            account_ref=ref,
        )
    )


def _record(tid: str, day: int, ref: str) -> dict:
    return {
        "transaction_id": tid,
        "normalised_provider_transaction_id": tid,
        "timestamp": f"2026-09-{day:02}T09:15:00Z",
        "amount": -float(FIGURES.get(ref, "9.99")),
        "currency": "GBP",
        "description": PAYEES.get(ref, "Anonymous Fares"),
    }


def _csv(root: Path, name: str, ref: str, days: list[int]) -> Path:
    path = root / f"{name}.csv"
    lines = "".join(
        f"{day:02}/09/2026,{PAYEES[ref]},{PAYEES[ref]} ref {n},CARD,-{FIGURES[ref]},0\n"
        for n, day in enumerate(days)
    )
    path.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n" + lines,
        encoding="utf-8",
    )
    return path


def add_unsettled_pair(db: Path) -> None:
    """Two bank-feed payments of one size on one day, through the matching door.

    The feed names a payment by one id for life, so the second is a second payment and the
    evidence has answered its flag; nothing has run the settlement pass, so the flag stands.
    """
    from datetime import date

    from obdi.identity import content_key
    from obdi.ingest import reconcile_batch
    from obdi.models import SourceTier, Transaction

    with Store(db) as store:
        for uid in ("late-1", "late-2"):
            reconcile_batch(
                store,
                [
                    Transaction(
                        account_id=SETTLED_BUS,
                        amount_minor=-250,
                        value_date=date(2026, 9, 20),
                        booking_date=date(2026, 9, 20),
                        description="Anonymous Fares",
                        source="starling",
                        source_id=uid,
                        tier=SourceTier.AUTHORITATIVE,
                        content_key=content_key(
                            amount_minor=-250,
                            value_date=date(2026, 9, 20),
                            description="Anonymous Fares",
                        ),
                    )
                ],
                digest=f"late-{uid}",
            )


def build_flag_world(root: Path) -> Path:
    """Land the household at `root/store.sqlite3` and return its path."""
    db = root / "store.sqlite3"
    with Store(db) as store:
        for ref, label in LABELS.items():
            store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))
        _truelayer(store, EVERYDAY, [_record("ev-1", 14, EVERYDAY)], 0)
        _truelayer(store, EVERYDAY, [_record("ev-2", 14, EVERYDAY)], 1)
        import_file(store, _csv(root, "bakery", BAKERY, [14, 14, 14]), account_id=BAKERY)
        _truelayer(store, TICKETS, [_record("tk-1", 14, TICKETS)], 0)
        import_file(store, _csv(root, "tickets", TICKETS, [14, 15]), account_id=TICKETS)
        for n in (1, 2):
            store.land_artefact(
                starling.artefact_for(
                    json.dumps(
                        {
                            "feedItems": [
                                {
                                    "feedItemUid": f"bus-{n}",
                                    "amount": {"currency": "GBP", "minorUnits": 250},
                                    "direction": "OUT",
                                    "transactionTime": "2026-09-14T09:15:00.000Z",
                                    "source": "MASTER_CARD",
                                    "status": "SETTLED",
                                    "counterPartyName": "Anonymous Fares",
                                    "reference": "REF",
                                }
                            ]
                        }
                    ).encode(),
                    account_id=f"starling:{SETTLED_BUS}",
                    kind="feed",
                    origin=f"https://api.example.com/feed/{SETTLED_BUS}?c={n}",
                )
            )
        _truelayer(
            store,
            SETTLED_TRAIN,
            [_record("tr-1", 14, SETTLED_TRAIN), _record("tr-2", 14, SETTLED_TRAIN)],
            0,
        )
        rebuild_from_raw(store)
    return db
