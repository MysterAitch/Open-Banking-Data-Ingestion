"""The household the "About this account" tests read, and the way they read its pages.

DECIDED BEFORE THE FIRST RUN (dates relative to the day the page is served on):

- `bare` is declared with a name and nothing else.
- `busy` declares a rate that ends in 10 days but states no known balance, so its row already
  carries a thing to do.
- `inferred-kept` and `inferred-changed` are opened 2020-01-01 and close 2031-06-01 with both dates
  inferred; the edit tests rename one without touching its dates and move a date of the other.
- `dated` is opened 2019-05-01 and closes 2031-02-01, both stated.
- `terms` is a credit card under `main`, opened 2020-03-01 with that date stated, closed
  2031-01-01 with that date inferred from its first and last movement. It declares a promotional
  rate (0%) that began 100 days ago and ends in 20 days, a purchases rate (22.9%) from 21 days
  ahead with no end, and a credit limit of 4321.00 from 400 days ago with no end. No statement
  is kept for it.
- `differs` declares a promotional rate of 0% over 100 days ago to 200 days ahead, and holds a
  statement from 30 days ago that prints a purchases rate of 19.9%.
- `same` declares the same window and holds a statement that prints 0%.
- `bare-rate` declares no rate at all and holds a statement from 30 days ago printing a cash rate
  of 27.4% under the label "CARD 1234".
- `ending` declares a rate that ends in 20 days and `far` one that ends in 90; `quiet` declares a
  rate that agrees with its newest statement and ends in 200 days.
- `old-differs` declares 8.8% from 400 days ago and holds a statement from 300 days ago printing
  6.1%, then three (60, 40, and 10 days ago) printing 8.8%: two lines, the three agreeing
  statements one line saying "and 2 earlier".
- `issuer-named` holds a statement that names Santander and prints the label "EVERYDAY SAVER
  4455".

No figure is read from a real account: every rate and limit is invented, and each distinctive
figure (19.9, 27.4, 22.9, 4321.00) must be absent from a page served masked.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest

from obdi.accounts import AccountRecord, AccountRef, LimitWindow, RateWindow
from obdi.balance_anchors import record_stated_anchor
from obdi.core.models import RawArtefact, SourceTier, Transaction, TransactionStatus
from obdi.identity import artefact_digest, content_key
from obdi.ingest import reconcile_batch
from obdi.parsers.statement_reading import StatementReading, reading_to_json
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from page_dom import Node, elements, parse
from served_store import environment_for, served_store

TODAY = datetime.now(UTC).date()
MAIN = "main"
BARE = "bare"
TERMS = "terms"
DIFFERS = "differs"
SAME = "same"
BARE_RATE = "bare-rate"
ENDING = "ending"
FAR = "far"
QUIET = "quiet"
OLD_DIFFERS = "old-differs"
ISSUER_NAMED = "issuer-named"
DATED = "dated"
BUSY = "busy"
INFERRED_KEPT = "inferred-kept"
INFERRED_CHANGED = "inferred-changed"
ACCOUNTS = [
    MAIN, BARE, TERMS, DIFFERS, SAME, BARE_RATE, ENDING, FAR, QUIET, OLD_DIFFERS, ISSUER_NAMED,
    DATED, BUSY, INFERRED_KEPT, INFERRED_CHANGED,
]


def ahead(days: int) -> date:
    return TODAY + timedelta(days=days)


def _rows(store: Store, ref: str) -> None:
    items = [
        Transaction(
            account_id=ref,
            amount_minor=-(500 + number),
            value_date=ahead(-number),
            booking_date=ahead(-number),
            description=f"Shop {number}",
            source="starling",
            source_id=f"{ref}-{number}",
            content_key=content_key(
                amount_minor=-(500 + number),
                value_date=ahead(-number),
                description=f"Shop {number}",
            ),
            tier=SourceTier.AUTHORITATIVE,
            status=TransactionStatus.BOOKED,
        )
        for number in range(1, 6)
    ]
    reconcile_batch(store, items, digest=f"rows-{ref}")
    if ref == BUSY:
        return
    # Two known balances that the five rows carry from one to the other, so that the account asks
    # nothing of the owner and its row on Today has a free slot for a note on its terms.
    record_stated_anchor(store, ref, ahead(-6).isoformat(), "1000.00", today=TODAY)
    record_stated_anchor(store, ref, TODAY.isoformat(), "974.85", today=TODAY)


def statement(
    store: Store,
    ref: str,
    day: date,
    rates: dict[str, float],
    *,
    label: str = "",
    lines: tuple[str, ...] = (),
) -> None:
    """A kept statement: a document held under `ref` and the reading kept of it."""
    payload = build_pdf([*lines, f"A statement for {ref} dated {day.isoformat()}"])
    digest = artefact_digest(payload)
    store.land_artefact(
        RawArtefact(
            source="statement",
            account_ref=ref,
            fetched_at=datetime.combine(day, time(12), UTC),
            media_type="application/pdf",
            digest=digest,
            payload=payload,
            origin=f"{ref}-{day.isoformat()}.pdf",
        )
    )
    reading = StatementReading(statement_date=day, rates=rates, account_name=label)
    store.keep_statement_reading(digest, "synthetic-card", reading_to_json(reading))
    store.connection.commit()


def build(store: Store) -> None:
    for ref in ACCOUNTS:
        _rows(store, ref)
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref.title()))
    store.declare_account(
        AccountRecord(
            ref=AccountRef(TERMS),
            label="Terms Card",
            kind="credit-card",
            parent=AccountRef(MAIN),
            opened=date(2020, 3, 1),
            closed=date(2031, 1, 1),
            date_basis="inferred from the first and last movement in the feed",
            rates=(
                RateWindow("purchases", ahead(21), None, 22.9),
                RateWindow("promotional", ahead(-100), ahead(20), 0.0),
            ),
            limits=(LimitWindow("credit", ahead(-400), None, 432100),),
        )
    )
    for ref in (INFERRED_KEPT, INFERRED_CHANGED):
        store.declare_account(
            AccountRecord(
                ref=AccountRef(ref),
                label=ref.title(),
                opened=date(2020, 1, 1),
                closed=date(2031, 6, 1),
                date_basis="inferred from the first and last movement in the feed",
            )
        )
    store.declare_account(
        AccountRecord(
            ref=AccountRef(DATED),
            label="Dated",
            opened=date(2019, 5, 1),
            closed=date(2031, 2, 1),
        )
    )
    window = (RateWindow("promotional", ahead(-100), ahead(200), 0.0),)
    for ref in (DIFFERS, SAME):
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=ref.title(), rates=window))
    for ref, label, ends, percent, began in (
        (ENDING, "Ending", 20, 5.5, -100),
        (FAR, "Far", 90, 5.5, -100),
        (QUIET, "Quiet", 200, 8.8, -100),
        (OLD_DIFFERS, "Old Differs", 200, 8.8, -400),
        (BUSY, "Busy", 10, 5.5, -100),
    ):
        store.declare_account(
            AccountRecord(
                ref=AccountRef(ref),
                label=label,
                rates=(RateWindow("purchases", ahead(began), ahead(ends), percent),),
            )
        )
    statement(store, DIFFERS, ahead(-30), {"purchases": 19.9})
    statement(store, SAME, ahead(-30), {"purchases": 0.0})
    statement(store, BARE_RATE, ahead(-30), {"cash": 27.4}, label="CARD 1234")
    statement(store, QUIET, ahead(-10), {"purchases": 8.8})
    statement(store, OLD_DIFFERS, ahead(-300), {"purchases": 6.1})
    for days in (-60, -40, -10):
        statement(store, OLD_DIFFERS, ahead(days), {"purchases": 8.8})
    statement(
        store,
        ISSUER_NAMED,
        ahead(-30),
        {},
        label="EVERYDAY SAVER 4455",
        lines=("Santander UK plc. Registered Office: 2 Triton Square",),
    )


def serve(root: Path) -> AbstractContextManager[str]:
    """The household, served: each test module holds one server for all its tests, and sets the
    environment the application reads on every request (`environment_for`)."""
    return served_store(root, build, bound=[MAIN])


def set_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in environment_for(root).items():
        monkeypatch.setenv(name, value)


def masked(base: str, ref: str) -> str:
    response = httpx.get(f"{base}/ledger", params={"ref": ref}, timeout=120)
    assert response.status_code == 200
    return response.text


def shown(base: str, ref: str) -> str:
    response = httpx.post(f"{base}/ledger", data={"ref": ref, "month": ""}, timeout=120)
    assert response.status_code == 200
    return response.text


def fold_of(page: str) -> Node:
    return next(
        fold
        for fold in elements(parse(page), "details")
        if any(
            isinstance(child, Node)
            and child.tag == "summary"
            and child.text() == "About this account"
            for child in fold.children
        )
    )


def lines_of(page: str) -> list[str]:
    """What the fold says, line by line: every paragraph, term, definition, and list item that
    holds no other of them."""
    return [
        node.text()
        for node in fold_of(page).descendants()
        if node.tag in ("p", "dt", "dd", "li")
        and not any(
            isinstance(child, Node) and child.tag in ("li", "p") for child in node.children
        )
    ]


def words(page: str) -> str:
    return " ".join(lines_of(page))


def windows_of(page: str) -> list[str]:
    return [n.text() for n in elements(fold_of(page), "li") if "window" in n.classes]


def stated_of(page: str) -> list[str]:
    return [n.text() for n in elements(fold_of(page), "li") if "stated" in n.classes]
