"""An invented household for the Goals page, worked out by hand with the clock pinned.

THE CLOCK IS PINNED to `TODAY` (2026-09-15) as in `this_month_world`, because a goal is measured in
whole calendar months from the day it was declared and a world made from the real clock could not
put "four months in" on the same run twice.

KNOWN ANSWERS, decided before the first run (pounds; whole months, `amount * elapsed // total`):

  card-visa     "Visa", a credit card, 300.00 owed (stated 2026-09-13).
    CLEAR "Clear the Visa": declared 2026-07-15 with 500.00 owed, wanted by 2027-01-15.
      cleared 200.00 of 500.00, 300.00 to go. Two months of six have passed, so the line has
      50,000p * 2 // 6 = 16,666p (166.66) cleared, in pence, so 200.00 is ahead of it by 33.34.
      Four months are left, so 75.00 a month gets there. This month's step is
      50,000p * 3 // 6 - 16,666p = 83.34. (Worked in pence: a first reckoning in whole pounds
      gave 166.00, 34.00, and 84.00, and was wrong by the pence the floor division drops.)

  savings-main  "Rainy day account", 400.00 held (stated 2026-09-13).
    BUILD "Rainy day": 1,000.00, no date. 400.00 of 1,000.00 held, 600.00 to go; no line.

  savings-trip  "Holiday pot", 400.00 held (`trip_held` changes it).
    SAVE "Holiday": 1,200.00, declared 2026-05-15, wanted by 2027-05-15. Four months of twelve
      have passed, so the line is 1,200 * 4 // 12 = 400.00. Held 400.00 is ahead by 0.00 (on the
      line); held 300.00 is behind by 100.00. This month's step is 100.00 either way.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from obdi.cli import build_web_config
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.goal_records import BUILD, CLEAR, SAVE
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from section_harness import environment, serve_config
from this_month_world import pin_clock

CARD = "card-visa"
SAVINGS = "savings-main"
TRIP = "savings-trip"
STATED_ON = "2026-09-13"

CLEAR_DECLARED = date(2026, 7, 15)
CLEAR_BY = date(2027, 1, 15)
SAVE_DECLARED = date(2026, 5, 15)
SAVE_BY = date(2027, 5, 15)


def populate(
    store: Store,
    *,
    owed: str = "-300.00",
    trip_held: str = "400.00",
    fund_held: str = "400.00",
    goals: bool = True,
    card_balance_known: bool = True,
) -> None:
    for ref, kind, label in (
        (CARD, "credit card", "Visa"),
        (SAVINGS, "savings", "Rainy day account"),
        (TRIP, "savings", "Holiday pot"),
    ):
        store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=label))
    if card_balance_known:
        record_stated_anchor(store, CARD, STATED_ON, owed)
    record_stated_anchor(store, SAVINGS, STATED_ON, fund_held)
    record_stated_anchor(store, TRIP, STATED_ON, trip_held)
    if not goals:
        return
    store.declare_goal(
        "Clear the Visa",
        kind=CLEAR,
        account=CARD,
        target_minor=0,
        target_date=CLEAR_BY,
        declared_on=CLEAR_DECLARED,
        start_minor=50000,
    )
    store.declare_goal(
        "Rainy day",
        kind=BUILD,
        account=SAVINGS,
        target_minor=100000,
        target_date=None,
        declared_on=CLEAR_DECLARED,
        start_minor=0,
    )
    store.declare_goal(
        "Holiday",
        kind=SAVE,
        account=TRIP,
        target_minor=120000,
        target_date=SAVE_BY,
        declared_on=SAVE_DECLARED,
        start_minor=0,
    )


@contextmanager
def served(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owed: str = "-300.00",
    trip_held: str = "400.00",
    fund_held: str = "400.00",
    goals: bool = True,
    card_balance_known: bool = True,
) -> Iterator[tuple[str, Path]]:
    pin_clock(monkeypatch)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        populate(
            store,
            owed=owed,
            trip_held=trip_held,
            fund_held=fund_held,
            goals=goals,
            card_balance_known=card_balance_known,
        )
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield base, db
    finally:
        stop()
