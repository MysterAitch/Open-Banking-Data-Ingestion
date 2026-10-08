"""An invented household for the This month page, worked out by hand with the clock pinned.

THE CLOCK IS PINNED to `TODAY` (Tuesday 2026-09-15) by replacing `datetime` in `obdi.cli`, the
module every hook reads the day from, as the account-page tests do for the ledger. A calendar page
is about which day of the month it is, and a world made from the real clock could not put a
payment on the 3rd, one on the 20th, and one overdue since the 5th on the same run.

KNOWN ANSWERS, decided before the first run (pounds; tolerance four days on every window, so a
monthly payment is on time until four days after its day):

  current-main  "Everyday": 1,000.00 stated by the owner on the 13th. Rows on it:
    Hartsholme Gas           120.00 monthly on the 3rd, paid on Sep 3:      PAID on the 3rd
    Cedarwick Fernside Gym    45.00 monthly on the 20th, last paid Aug 20:  DUE on the 20th
    Northfold Insurance       30.00 monthly on the 5th, last paid Aug 5:    OVERDUE since the 5th
    Brindlewick Payroll    3,000.00 monthly on the 25th into it, last paid
                                    Aug 25:                                 EXPECTED on the 25th
    and Corner Bakery 3.20 on the 12th. The insurance's deadline is the 9th (the 5th plus four
    days), and the account's rows run to the 12th, so its absence is proved.

  So the headline counts 3 commitments: 1 paid, 1 due, 1 overdue. Before the salary on the 25th
  only the gym (45.00) leaves current-main; the insurance's missed payment is not counted by
  Position, which counts what is due from today. 1,000.00 less 45.00 is 955.00 free: funded.

  bills-pot  "Bills" (with `short_account`): 50.00 stated on the 13th; Pennywhistle Water 80.00
    monthly on the 18th out of it, and Brindlewick Payroll Top-up 500.00 monthly on the 25th into
    it. 80.00 leaves on the 18th, before the 25th, against 50.00 held: short by 30.00 before the
    25th.

With `insurance_paid`, the insurance has a row on Sep 5 too and nothing is overdue.
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
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from section_harness import environment, serve_config

TODAY = date(2026, 9, 15)
CURRENT = "current-main"
BILLS = "bills-pot"
GAS = "Hartsholme Gas"
GYM = "Cedarwick Fernside Gym"
INSURANCE = "Northfold Insurance"
SALARY = "Brindlewick Payroll"
TOP_UP = "Brindlewick Payroll Top-up"
WATER = "Pennywhistle Water"


class Pinned(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return datetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=UTC)


def pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "datetime", Pinned)


def on_day(month: int, day: int) -> date:
    return date(2026, month, day)


def write_export(path: Path, rows: list[tuple[date, str, str]]) -> None:
    """A bank export: (day, counter party, amount as printed, negative for money out)."""
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{d:%d/%m/%Y},{payee},,FASTER PAYMENT,{amount},0" for d, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def monthly(
    payee: str, amount: str, day: int, first: int, last: int
) -> list[tuple[date, str, str]]:
    """The payee's payment on `day` of each month from `first` to `last` (2026)."""
    return [(on_day(month, day), payee, amount) for month in range(first, last + 1)]


def terms(minor: int, day: int) -> WindowTerms:
    return WindowTerms(
        amount_minor=minor,
        currency="GBP",
        cadence="monthly",
        usual_day=day,
        usual_month=0,
        tolerance_days=4,
        basis="invented for a test",
    )


def commit(
    store: Store, name: str, account: str, direction: str, minor: int, day: int,
    *, ended: date | None = None, since: date = date(2026, 1, 1),
) -> None:
    store.declare_commitment(
        name,
        kind="pulled",
        account=account,
        direction=direction,
        entity_id=None,
        name_key=name.casefold(),
        from_day=since,
        to_day=ended,
        terms=terms(minor, day),
    )


def populate(
    store: Store,
    tmp_path: Path,
    *,
    short_account: bool = False,
    insurance_paid: bool = False,
    reaches_the_12th: bool = True,
    commitments: bool = True,
) -> None:
    """`reaches_the_12th` False leaves out the bakery row, so the account's rows stop on the 3rd,
    before the insurance's deadline on the 9th. `commitments` False confirms nothing."""
    store.declare_account(AccountRecord(ref=AccountRef(CURRENT), kind="current", label="Everyday"))
    rows = [
        *monthly(GAS, "-120.00", 3, 4, 9),
        *monthly(GYM, "-45.00", 20, 3, 8),
        *monthly(INSURANCE, "-30.00", 5, 3, 9 if insurance_paid else 8),
        *monthly(SALARY, "3000.00", 25, 3, 8),
    ]
    if reaches_the_12th:
        rows.append((on_day(9, 12), "Corner Bakery", "-3.20"))
    csv = tmp_path / "everyday.csv"
    write_export(csv, sorted(rows))
    import_file(store, csv, account_id=CURRENT)
    record_stated_anchor(store, CURRENT, "2026-09-13", "1000.00")
    if commitments:
        commit(store, GAS, CURRENT, "out", 12000, 3)
        commit(store, GYM, CURRENT, "out", 4500, 20)
        commit(store, INSURANCE, CURRENT, "out", 3000, 5)
        commit(store, SALARY, CURRENT, "in", 300000, 25)
    if short_account:
        store.declare_account(AccountRecord(ref=AccountRef(BILLS), kind="current", label="Bills"))
        pot = tmp_path / "bills.csv"
        write_export(
            pot,
            sorted(
                [
                    *monthly(WATER, "-80.00", 18, 3, 8),
                    *monthly(TOP_UP, "500.00", 25, 3, 8),
                ]
            ),
        )
        import_file(store, pot, account_id=BILLS)
        record_stated_anchor(store, BILLS, "2026-09-13", "50.00")
        if commitments:
            commit(store, WATER, BILLS, "out", 8000, 18)
            commit(store, TOP_UP, BILLS, "in", 50000, 25)


@contextmanager
def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: bool
) -> Iterator[tuple[str, Path]]:
    pin_clock(monkeypatch)
    db = tmp_path / "store.sqlite3"
    with Store(db) as store:
        populate(store, tmp_path, **options)
    environment(monkeypatch, tmp_path)
    config = build_web_config(db)
    assert config is not None
    base, stop = serve_config(config)
    try:
        yield base, db
    finally:
        stop()
