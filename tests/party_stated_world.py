"""A household whose party coverage is decided here, before any run. Every figure is invented.

TODAY is 2026-10-05. Three accounts, each with an answer:

  STATED   the bank's feed lists nine payments, one a week from 2026-08-03, each carrying the
           merchant's name. Every day is stated; nothing is said; nothing is wanted.
  MIXED    the feed lists 17 payments with a merchant name, one a week from 2026-01-05 to
           2026-04-27, and the statements alone list 18 payments, one every third day from
           2026-05-04 to 2026-06-24 (ten in May, eight in June), with only a printed
           description. So the hollow stretch is May and June, holding 18 described
           transactions between 2026-05-04 and 2026-06-24; an export file for those months is
           wanted.
  PDFONLY  statements alone list eleven payments, weekly from 2026-07-06 to 2026-09-14, with a
           printed description each. The account has no other way in, so the words are "this
           source states no party" and no export is wanted.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from obdi.core.models import SourceTier, Transaction
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import starling
from obdi.ingest.store import Store

TODAY = date(2026, 10, 5)
STATED = "stated"
MIXED = "mixed"
PDFONLY = "pdfonly"
STATEMENT_SOURCE = "halifax-statement-pdf"

MIXED_STATED_ROWS = 17
MIXED_DESCRIBED_ROWS = 18
MIXED_DESCRIBED_FIRST = date(2026, 5, 4)
MIXED_DESCRIBED_LAST = date(2026, 6, 24)
PDFONLY_ROWS = 11


def _weekly(first: date, last: date) -> list[date]:
    days = []
    day = first
    while day <= last:
        days.append(day)
        day += timedelta(days=7)
    return days


def _transaction(
    account: str, day: date, number: int, *, source: str, counterparty: str, description: str
) -> Transaction:
    minor = -(1000 + number)
    return Transaction(
        account_id=account,
        amount_minor=minor,
        currency="GBP",
        value_date=day,
        booking_date=day,
        description=description,
        counterparty=counterparty,
        source=source,
        source_id=f"{source}-{account}-{number}",
        tier=SourceTier.AUTHORITATIVE,
        content_key=content_key(amount_minor=minor, value_date=day, description=description),
    )


def _land(store: Store, account: str, source: str, rows: list[Transaction]) -> None:
    body = json.dumps({"account": account, "source": source, "rows": len(rows)}).encode()
    artefact = starling.artefact_for(body, account_id=account, kind="feed")
    artefact = replace(artefact, source=source, fetched_at=datetime.now(UTC))
    store.land_artefact(artefact)
    reconcile_batch(store, rows, digest=artefact.digest)


LINKED = "linked"


def build_linked(store: Store) -> None:
    """A feed that states `Acme Coffee` once, and statements whose descriptions are that name with
    a country code on four other days: the ladder takes the statement rows to the stated party
    (`MATCHED_NAME`), so the account has no described transaction at all.

    The same description WITHOUT the code names the party already, so the ladder leaves it a
    `DESCRIPTION` row with the stated party's name: measured, and the same as the Entities
    page's "from the description" count, so the same here."""
    store.declare_account(AccountRecord(ref=AccountRef(LINKED), label="Linked account"))
    _land(store, LINKED, "starling", [
        _transaction(LINKED, date(2026, 1, 5), 300, source="starling",
                     counterparty="Acme Coffee", description="CARD PAYMENT TO ACME COFFEE"),
    ])
    _land(store, LINKED, STATEMENT_SOURCE, [
        _transaction(LINKED, date(2026, 2, 2 + 7 * n), 310 + n, source=STATEMENT_SOURCE,
                     counterparty="", description="Acme Coffee GB")
        for n in range(4)
    ])


def build_party_household(store: Store) -> None:
    for ref, label in ((STATED, "Stated account"), (MIXED, "Mixed account"),
                       (PDFONLY, "Statement only account")):
        store.declare_account(AccountRecord(ref=AccountRef(ref), label=label))
    _land(store, STATED, "starling", [
        _transaction(STATED, day, number, source="starling", counterparty=f"Merchant {number}",
                     description=f"CARD PAYMENT TO MERCHANT {number}")
        for number, day in enumerate(_weekly(date(2026, 8, 3), date(2026, 9, 28)))
    ])
    _land(store, MIXED, "starling", [
        _transaction(MIXED, day, number, source="starling", counterparty=f"Shop {number}",
                     description=f"CARD PAYMENT TO SHOP {number}")
        for number, day in enumerate(_weekly(date(2026, 1, 5), date(2026, 4, 27)))
    ])
    _land(store, MIXED, STATEMENT_SOURCE, [
        _transaction(MIXED, day, 100 + number, source=STATEMENT_SOURCE, counterparty="",
                     description=f"DD LOCAL SERVICE {number} REF {number:04d}")
        for number, day in enumerate(
            [date(2026, 5, 4) + timedelta(days=3 * n) for n in range(MIXED_DESCRIBED_ROWS)]
        )
    ])
    _land(store, PDFONLY, STATEMENT_SOURCE, [
        _transaction(PDFONLY, day, 200 + number, source=STATEMENT_SOURCE, counterparty="",
                     description=f"POS UNIQUE VENDOR {number} REF {number:04d}")
        for number, day in enumerate(_weekly(date(2026, 7, 6), date(2026, 9, 14)))
    ])
