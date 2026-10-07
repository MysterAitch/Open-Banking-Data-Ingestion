"""Three years of one invented account, built through the doors the application writes through.

Rows land by `reconcile_batch` (the door a pull and an import both use), known balances by
`record_stated_anchor`, a protection by `press`, and the Spaces by `declare_account`. Nothing
is written into the database directly.

The accounts, and what each is built to be (decided here, before any page reads them):

- `HELD` ("starling-personal"): a parent with two Spaces. About 1,200 rows from 2023-10-01 to
  2026-09-30, the newest month holding exactly `NEWEST_MONTH_ROWS`. A balance is stated at the
  end of every month, each one the true running balance but for the balance stated at the end of
  `FAULT_DAY`, which is `FAULT_MINOR` too high: one movement missing, so the account agrees
  through the month before it and is held back from there. Protected through `PROTECTED_THROUGH`,
  which is inside the agreement.
- `AGREEING` ("starling-joint"): the same shape of history, every stated balance true, nothing
  protected. In agreement through its last stated balance.
- `UNKNOWN` ("starling-spare"): rows and no stated balance at all.
"""

from __future__ import annotations

import json
import os
import random
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.store import Store
from obdi.verify.agreement import standing_of
from obdi.verify.balance_anchors import effective_opening, record_stated_anchor
from obdi.verify.movement_completeness import MovementCompleteness
from obdi.verify.protection import press
from test_ledger import land, txn

HELD = "starling-personal"
AGREEING = "starling-joint"
UNKNOWN = "starling-spare"
SPACES = ("starling-space-bills", "starling-space-holiday")

FIRST_DAY = date(2023, 10, 1)
LAST_DAY = date(2026, 9, 30)
NEWEST_MONTH = "2026-09"
NEWEST_MONTH_ROWS = 50
FAULT_DAY = date(2025, 3, 31)
FAULT_MINOR = 1250
PROTECTED_THROUGH = date(2025, 1, 31)
OPENING_MINOR = 250_000

FEED, AGGREGATOR = "starling", "truelayer"

_SHOPS = (
    "Tesco Superstore",
    "Co-op Food",
    "Pret A Manger",
    "TfL Travel",
    "Boots",
    "Amazon Marketplace",
    "Octopus Energy",
    "Spotify",
    "Local Bakery",
    "Water Company Direct Debit",
)


def _month_ends(first: date, last: date) -> list[date]:
    ends: list[date] = []
    cursor = date(first.year, first.month, 28)
    while cursor <= last:
        following = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        ends.append(following - timedelta(days=1))
        cursor = date(following.year, following.month, 28)
    return [end for end in ends if end <= last]


def _rows_of(ref: str, seed: int) -> list[tuple[str, date, int, str]]:
    """(source id, day, minor, description) for each row, every amount distinct so the matcher
    never opens a review for two equal payments on one day."""
    rng = random.Random(seed)  # noqa: S311 - invented amounts, not security material
    rows: list[tuple[str, date, int, str]] = []
    counter = 0
    day = FIRST_DAY
    while day <= LAST_DAY:
        in_newest = day.strftime("%Y-%m") == NEWEST_MONTH
        per_day = 0
        if not in_newest:
            per_day = 1 if rng.random() < 0.8 else 0
            per_day += 1 if rng.random() < 0.3 else 0
        for _ in range(per_day):
            counter += 1
            income = day.day == 25 and rng.random() < 0.9
            minor = (
                230_000 + counter * 7
                if income
                else -(300 + rng.randrange(0, 2400) * 3 + counter % 3 + 1)
            )
            shop = "Salary, Northgate Ltd" if income else rng.choice(_SHOPS)
            rows.append((f"{ref}-{counter}", day, minor, f"{shop} {counter}"))
        day += timedelta(days=1)
    newest_days = [FIRST_DAY.replace(year=2026, month=9, day=n) for n in range(1, 31)]
    for index in range(NEWEST_MONTH_ROWS):
        counter += 1
        rows.append(
            (
                f"{ref}-{counter}",
                newest_days[index % 30],
                -(450 + counter * 11),
                f"{_SHOPS[index % len(_SHOPS)]} {counter}",
            )
        )
    return rows


def _land(store: Store, ref: str, rows: list[tuple[str, date, int, str]]) -> None:
    """Every row from the feed, and six in seven again from the aggregator, which the matcher
    joins to the feed's by what they share; the seventh is seen by the feed alone."""
    feed = [txn(ref, FEED, sid, day, minor, text) for sid, day, minor, text in rows]
    relayed = [
        txn(ref, AGGREGATOR, f"tl-{sid}", day, minor, text)
        for index, (sid, day, minor, text) in enumerate(rows)
        if index % 7 != 0
    ]
    for start in range(0, len(feed), 300):
        land(store, f"{ref}-feed-{start}", *feed[start : start + 300])
    for start in range(0, len(relayed), 300):
        land(store, f"{ref}-agg-{start}", *relayed[start : start + 300])


def _state_balances(
    store: Store, ref: str, rows: list[tuple[str, date, int, str]], *, fault: bool
) -> None:
    running = OPENING_MINOR
    previous = FIRST_DAY - timedelta(days=1)
    for end in _month_ends(FIRST_DAY, LAST_DAY):
        running += sum(minor for _, day, minor, _ in rows if previous < day <= end)
        previous = end
        stated = running + (FAULT_MINOR if fault and end >= FAULT_DAY else 0)
        sign = "-" if stated < 0 else ""
        whole, pence = divmod(abs(stated), 100)
        record_stated_anchor(
            store, ref, end.isoformat(), f"{sign}{whole}.{pence:02d}", today=LAST_DAY
        )


_ENV = (
    "OBDI_CONNECTION_STORE",
    "OBDI_ACCOUNT_MAP",
    "OBDI_INSTANCE_LABEL",
    "OBDI_INSTANCE_ROLE",
    "TRUELAYER_CLIENT_ID",
    "TRUELAYER_CLIENT_SECRET_FILE",
)


def corpus_environment(root: Path) -> dict[str, str]:
    """What the served application reads from the environment on every request.

    The suite's own fixture clears every `OBDI_` variable before each test, which would unbind the
    accounts from Actual in the middle of a module-scoped server; a test module restores these
    after it, with its own `autouse` fixture.
    """
    return {
        "OBDI_CONNECTION_STORE": str(root / "connections.json"),
        "OBDI_ACCOUNT_MAP": str(root / "accounts.json"),
        "OBDI_INSTANCE_LABEL": "obdi",
        "OBDI_INSTANCE_ROLE": "production",
    }


@contextmanager
def served_corpus(root: Path) -> Iterator[str]:
    """The real application over the corpus, in this process, on a free port; yields its address.

    The page's day is fixed at the corpus's last day, so the 30 days the account page opens on
    (2026-09-01 to 2026-09-30) are exactly its newest month of `NEWEST_MONTH_ROWS`, whenever
    the suite runs.
    """
    from obdi.cli import build_web_config
    from obdi.pages import web_ledger
    from obdi.pages.web import AuthorisationSession, ConnectionHandler

    class Fixed(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return datetime(LAST_DAY.year, LAST_DAY.month, LAST_DAY.day, 12, tzinfo=UTC)

    clock = web_ledger.datetime
    web_ledger.datetime = Fixed  # type: ignore[misc]
    saved = {name: os.environ.get(name) for name in _ENV}
    os.environ.update(corpus_environment(root))
    os.environ.pop("TRUELAYER_CLIENT_ID", None)
    os.environ.pop("TRUELAYER_CLIENT_SECRET_FILE", None)
    write_account_map(root / "accounts.json")
    db = root / "store.sqlite3"
    with Store(db) as store:
        build_corpus(store)
    config = build_web_config(db)
    assert config is not None
    handler = type("H", (ConnectionHandler,), {"config": config, "session": AuthorisationSession()})
    httpd = ConnectionHandler.make_server(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        web_ledger.datetime = clock  # type: ignore[misc]
        httpd.shutdown()  # type: ignore[attr-defined]
        httpd.server_close()  # type: ignore[attr-defined]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def write_account_map(path: Path) -> None:
    """The account map the application reads: the held and agreeing accounts are bound to Actual,
    so a row is not stamped "withheld from Actual" as it would be on an account nothing is sent
    from, which is how the owner's own accounts stand."""
    path.write_text(
        json.dumps(
            {
                "bindings": [],
                "actual": [
                    {"canonical_id": HELD, "actual_account_id": "act-personal"},
                    {"canonical_id": AGREEING, "actual_account_id": "act-joint"},
                ],
            }
        ),
        encoding="utf-8",
    )


def build_corpus(store: Store) -> None:
    held = _rows_of(HELD, 11)
    _land(store, HELD, held)
    for space in SPACES:
        store.declare_account(
            AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(HELD))
        )
        _land(store, space, _rows_of(space, 3)[:40])
    _state_balances(store, HELD, held, fault=True)
    opening = effective_opening(store, HELD)
    press(
        store,
        HELD,
        PROTECTED_THROUGH.isoformat(),
        opening=opening,
        standing=standing_of(opening, [HELD], MovementCompleteness()),
    )
    agreeing = _rows_of(AGREEING, 29)
    _land(store, AGREEING, agreeing)
    _state_balances(store, AGREEING, agreeing, fault=False)
    _land(store, UNKNOWN, _rows_of(UNKNOWN, 5))
