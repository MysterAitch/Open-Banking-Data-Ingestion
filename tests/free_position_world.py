"""An invented household for the Position page's four figures, worked out by hand.

It is read on the real clock, so every date is made from today. KNOWN ANSWERS, decided before the
first run (pounds; "ahead(n)" is the first day on or after today plus n whose day of the month is
28 or less, so a monthly day never clamps and never repeats inside the next four weeks):

  current-main  a current account; 1,000.00 stated by the owner two days ago, and nothing since.
  card-visa     a credit card; 250.00 owed, stated two days ago. No limit is declared anywhere.
  old-pot       an account holding rows and no stated balance: its balance is not known.

  salary        3,000.00 monthly into current-main on ahead(12)'s day of the month.
  gas           120.00 monthly out of current-main on ahead(3)'s day: BEFORE the salary.
  gym           45.00 monthly out of current-main on ahead(24)'s day: AFTER the salary.
  streaming     9.99 monthly out of card-visa on ahead(5)'s day.

So, with the salary confirmed: current-main holds 1,000.00, owes nothing, has 120.00 committed
before the salary (gas alone), and 880.00 free. The card owes 250.00, has no limit declared.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from landing import import_file
from obdi.cli import build_web_config
from obdi.core.page_times import local_day
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.commitment_records import WindowTerms
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor
from recurring_press_support import months_back
from section_harness import environment, serve_config

CURRENT = "current-main"
CARD = "card-visa"
POT = "old-pot"
SALARY = "Brindlewick Payroll"
GAS = "Hartsholme Gas"
GYM = "Cedarwick Fernside Gym"
STREAMING = "Zephyrine Quokka Subscriptions"


def today() -> date:
    return local_day(datetime.now(UTC))


def ahead(n: int) -> date:
    """The first day on or after today plus `n` whose day of the month is 28 or less."""
    day = today() + timedelta(days=n)
    while day.day > 28:
        day += timedelta(days=1)
    return day


def write_export(path: Path, rows: list[tuple[date, str, str]]) -> None:
    """A bank export: (day, counter party, amount as printed, negative for money out)."""
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)"]
    lines += [f"{d:%d/%m/%Y},{payee},,FASTER PAYMENT,{amount},0" for d, payee, amount in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    *, ended: date | None = None,
) -> None:
    store.declare_commitment(
        name,
        kind="pulled",
        account=account,
        direction=direction,
        entity_id=None,
        name_key=name.casefold(),
        from_day=today() - timedelta(days=200),
        to_day=ended,
        terms=terms(minor, day),
    )


def populate(
    store: Store,
    tmp_path: Path,
    *,
    confirm_salary: bool = True,
    salary_rows: bool = False,
    commitments: bool = True,
    pot: bool = True,
) -> None:
    for ref, kind, label in ((CURRENT, "current", "Everyday"), (CARD, "credit card", "Visa")):
        store.declare_account(AccountRecord(ref=AccountRef(ref), kind=kind, label=label))
    if salary_rows:
        csv = tmp_path / "salary.csv"
        write_export(csv, [(d, SALARY, "3000.00") for d in months_back(today(), 6, ahead(12).day)])
        import_file(store, csv, account_id=CURRENT)
    record_stated_anchor(store, CURRENT, (today() - timedelta(days=2)).isoformat(), "1000.00")
    record_stated_anchor(store, CARD, (today() - timedelta(days=2)).isoformat(), "-250.00")
    if pot:
        csv = tmp_path / "pot.csv"
        write_export(csv, [(today() - timedelta(days=40), "Corner Bakery", "-3.20")])
        import_file(store, csv, account_id=POT)
    if commitments:
        commit(store, GAS, CURRENT, "out", 12000, ahead(3).day)
        commit(store, GYM, CURRENT, "out", 4500, ahead(24).day)
        commit(store, STREAMING, CARD, "out", 999, ahead(5).day)
    if confirm_salary:
        commit(store, SALARY, CURRENT, "in", 300000, ahead(12).day)


@contextmanager
def served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **options: bool
) -> Iterator[tuple[str, Path]]:
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
