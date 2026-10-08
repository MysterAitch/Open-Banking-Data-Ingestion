"""An invented household whose rent is paid through a bills space, for the pages that show a flow.

THE CLOCK IS PINNED by replacing `datetime` in `obdi.cli`, as `this_month_world` does. KNOWN
ANSWERS, decided before the first run (pounds):

  Rent 900.00 monthly on the 1st from 2026-10-01 out of "Everyday", three checked legs for the
  October payment and one external:
    1. Everyday -> Bills, half, by 2026-09-28            (seen: a paired transfer on 09-27)
    2. Casey Wintermute -> Everyday, half, by 09-30      (seen: 450.00 on 09-30, unless left out)
    3. Everyday -> Landlord Ltd, the whole, on 10-01     (seen: 900.00 on 10-01, unless left out)
    4. Casey Wintermute -> Landlord Ltd, external        (never seen, never checked)
  Electric 79.00 on the 25th and Gas 34.50 on the 27th are each stashed whole in Bills by their day.

  On 2026-09-28 Bills must hold, for October, 450.00 + 79.00 + 34.50 = 563.50 by 2026-10-01. It
  holds 450.00 (stated on the 27th) unless `bills_held` says otherwise.
  On 2026-10-02 the rent's legs are all past; the remaining draw-downs of October are electric and
  gas, 113.50 by 2026-10-25.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path

import pytest

from landing import import_file
from obdi import cli
from obdi.cli import build_web_config
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.entity_records import STATED_NAME, Identifier
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from section_harness import environment, serve_config
from this_month_world import write_export

CURRENT = "current-main"
BILLS = "bills-pot"
PARTNER = "Casey Wintermute"
LANDLORD = "Landlord Ltd"
ESTATE = "Corner Bakery"


def pin(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    class Pinned(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return datetime(today.year, today.month, today.day, 12, tzinfo=UTC)

    monkeypatch.setattr(cli, "datetime", Pinned)


def terms(minor: int, day: int) -> WindowTerms:
    return WindowTerms(minor, "GBP", "monthly", day, 0, 4, "invented for a test")


def populate(
    store: Store,
    tmp_path: Path,
    *,
    partner_paid: bool = True,
    landlord_paid: bool = True,
    bills_held: str = "450.00",
    legs: bool = True,
) -> dict[str, int]:
    """The accounts, rows, entities, commitments, and legs; returns the entities' ids by name."""
    for ref, label in ((CURRENT, "Everyday"), (BILLS, "Bills")):
        store.declare_account(AccountRecord(ref=AccountRef(ref), kind="current", label=label))
    rows = [(date(2026, 9, 27), "Transfer to Bills", "-450.00")]
    if partner_paid:
        rows.append((date(2026, 9, 30), PARTNER, "450.00"))
    if landlord_paid:
        rows.append((date(2026, 10, 1), LANDLORD, "-900.00"))
    rows.append((date(2026, 10, 2), ESTATE, "-3.20"))
    write_export(tmp_path / "everyday.csv", sorted(rows))
    write_export(tmp_path / "bills.csv", [(date(2026, 9, 27), "Transfer from Everyday", "450.00")])
    import_file(store, tmp_path / "everyday.csv", account_id=CURRENT)
    import_file(store, tmp_path / "bills.csv", account_id=BILLS)
    by_text = {t.description: t for t in store.all_transactions()}
    store.replace_transfer_pairs(
        [(by_text["Transfer to Bills"].entity_id, by_text["Transfer from Everyday"].entity_id)]
    )
    record_stated_anchor(store, CURRENT, "2026-10-02", "1500.00")
    record_stated_anchor(store, BILLS, "2026-09-27", bills_held)
    ids = {
        name: store.create_entity(name, [Identifier(STATED_NAME, name.casefold())])
        for name in (PARTNER, LANDLORD)
    }
    if not legs:
        return ids
    rent = store.declare_commitment(
        "Rent", kind="scheduled", account=CURRENT, direction="out", entity_id=ids[LANDLORD],
        name_key=LANDLORD.casefold(), from_day=date(2026, 10, 1), to_day=None,
        terms=terms(90000, 1),
    )  # fmt: skip
    store.declare_leg(
        rent, from_account=CURRENT, to_account=BILLS, share_percent=50, day=28, months_before=1
    )
    store.declare_leg(
        rent, from_entity=ids[PARTNER], to_account=CURRENT, share_percent=50, day=30,
        months_before=1,
    )  # fmt: skip
    store.declare_leg(rent, from_account=CURRENT, to_entity=ids[LANDLORD], share_percent=100, day=1)
    store.declare_leg(
        rent, from_entity=ids[PARTNER], to_entity=ids[LANDLORD], share_percent=100, day=1
    )
    for name, minor, day in (("Electric", 7900, 25), ("Gas", 3450, 27)):
        bill = store.declare_commitment(
            name, kind="pulled", account=CURRENT, direction="out", entity_id=None,
            name_key=name.casefold(), from_day=date(2026, 1, 1), to_day=None,
            terms=terms(minor, day),
        )  # fmt: skip
        store.declare_leg(
            bill, from_account=CURRENT, to_account=BILLS, share_percent=100, day=day
        )
    return ids


@contextmanager
def served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    today: date,
    *,
    partner_paid: bool = True,
    landlord_paid: bool = True,
    bills_held: str = "450.00",
    legs: bool = True,
) -> Iterator[tuple[str, Path]]:
    pin(monkeypatch, today)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        populate(
            store,
            tmp_path,
            partner_paid=partner_paid,
            landlord_paid=landlord_paid,
            bills_held=bills_held,
            legs=legs,
        )
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield base, db
    finally:
        stop()
