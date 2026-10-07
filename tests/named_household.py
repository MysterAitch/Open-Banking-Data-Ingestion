"""An invented household in which an account's three names are three different strings.

Every page is walked over it by the tests that hold the classes "an account is named by the name
he gave it", "an identifier is set as code", and "a good result is said quietly". A household
where the declared label, the provider's label, and the reference are all the same string could
not tell a page that asks for the right one from a page that asks for any one, which is how the
hooks that asked only for the provider's name went unnoticed.

KNOWN ANSWERS, decided before the first run (`label` is what a page must show beside `ref`):

    ref                 declared label       provider label         state
    starling:uid-main   Joint current        Spend (starling)       two known balances disagree
    starling:uid-pots   (none)               Bills pot (starling)   one row, one known balance
    pocket-money        Kids' pocket money   (none)                 in agreement
    cash                Wallet               (none)                 in agreement; the reference
                                                                    is an ordinary English word
    tin-savings         Biscuit tin          (none)                 balance-only, two known
                                                                    balances, no rows
    old-card            Old card             (none)                 archived, one row
    plain-ref-7         (none)               (none)                 one row, no known balance

`SOURCES` are the names of the sources that fed the rows, identifiers in their own right.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.providers import starling
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from section_harness import environment, serve_config
from test_ledger import land, txn

D = date
TODAY = D(2026, 10, 5)


@dataclass(frozen=True)
class Named:
    ref: str
    #: What a page must call the account: the declared label, else the provider's, else nothing.
    label: str


MAIN = Named("starling:uid-main", "Joint current")
POTS = Named("starling:uid-pots", "Bills pot (starling)")
POCKET = Named("pocket-money", "Kids' pocket money")
CASH = Named("cash", "Wallet")
TIN = Named("tin-savings", "Biscuit tin")
OLD = Named("old-card", "Old card")
PLAIN = Named("plain-ref-7", "")
ACCOUNTS = (MAIN, POTS, POCKET, CASH, TIN, OLD, PLAIN)
LABELLED = tuple(account for account in ACCOUNTS if account.label)

#: The provider's names for the two accounts it knows, which differ from every declared label.
PROVIDER_LABELS = {"starling:uid-main": "Spend", "starling:uid-pots": "Bills pot"}
SOURCES = ("monzo-csv", "halifax-export")


def build(db: Path) -> None:
    with Store(db) as store:
        for account, kind, closed in (
            (MAIN, "", None),
            (POCKET, "", None),
            (CASH, "", None),
            (TIN, BALANCE_ONLY_KIND, None),
            (OLD, "", D(2026, 9, 30)),
        ):
            store.declare_account(
                AccountRecord(
                    ref=AccountRef(account.ref), kind=kind, label=account.label, closed=closed
                )
            )
        rows = (
            (MAIN, SOURCES[0]),
            (POTS, SOURCES[0]),
            (POCKET, SOURCES[1]),
            (CASH, SOURCES[1]),
            (OLD, SOURCES[1]),
            (PLAIN, SOURCES[1]),
        )
        for n, (account, source) in enumerate(rows):
            row = txn(account.ref, source, f"r-{n}", D(2026, 9, 14), -1000, "KETTLE")
            land(store, f"d-{n}", row)
        for account, day, amount in (
            (MAIN, "2026-09-10", "100.00"),
            (MAIN, "2026-09-20", "70.00"),
            (POTS, "2026-09-20", "55.00"),
            (POCKET, "2026-09-10", "100.00"),
            (POCKET, "2026-09-20", "90.00"),
            (CASH, "2026-09-10", "100.00"),
            (CASH, "2026-09-20", "90.00"),
            (TIN, "2026-09-10", "300.00"),
            (TIN, "2026-09-20", "300.00"),
        ):
            record_stated_anchor(store, account.ref, day, amount, today=TODAY)
        body = json.dumps(
            {
                "accounts": [
                    {
                        "accountUid": "uid-main",
                        "name": PROVIDER_LABELS["starling:uid-main"],
                        "defaultCategory": "cat-main",
                    },
                    {
                        "accountUid": "uid-pots",
                        "name": PROVIDER_LABELS["starling:uid-pots"],
                        "defaultCategory": "cat-pots",
                    },
                ]
            }
        ).encode()
        store.land_artefact(starling.artefact_for(body, account_id="uid-main", kind="accounts"))


@pytest.fixture(scope="module")
def household(tmp_path_factory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("named-household")
    db = root / "store.sqlite3"
    build(db)
    (root / "accounts.json").write_text(json.dumps({"actual": []}), encoding="utf-8")
    return db, root


@pytest.fixture(scope="module")
def household_served(household):
    from obdi.cli import build_web_config

    db, root = household
    mp = pytest.MonkeyPatch()
    environment(mp, root)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    yield base
    stop()
    mp.undo()
