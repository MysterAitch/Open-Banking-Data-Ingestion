"""A household the size of the real one, built through the doors, for the Coverage by source page.

Every figure is invented, and the answers were worked out here before the first run. Dates are
counted back from today because the page reads the clock; a row is `ago` days old.

KNOWN ANSWERS

  19 accounts, 28 (account, source) pairs, 4 sources, 4 archived Spaces.
  Source kinds: `starling` a bank feed, `truelayer` an aggregator, `halifax-csv` an export file,
  `santander-pdf` a statement.

  Account            Sources, as source first-last (days ago)
  main  Joint        starling 400-1, truelayer 300-2, halifax-csv 700-130, santander-pdf 800-35
  savings Rainy day  truelayer 500-3, halifax-csv 500-200
  card  Blue card    truelayer 300-1, santander-pdf 300-20
  everyday           starling 200-0, truelayer 200-1
  main-bills, main-holiday, main-trip: Spaces of main, starling 100-2 / 100-3 / 100-4
  main-old-a .. d    archived Spaces of main, starling 300-120 (a), 300-107 (b), 300-106 (c),
                     300-105 (d)
  old-card           truelayer 1200-800, halifax-csv 1200-800      quiet (over a year)
  isa-old            halifax-csv 900-500                           quiet
  pension            halifax-csv 400-20
  loan               santander-pdf 400-45
  wallet             starling 100-1
  bills-card         truelayer 100-1
  joint-savings      truelayer 300-2, halifax-csv 300-150
  holiday-fund       starling 200-1, truelayer 200-2

  A source is "behind" where its last day is more than 60 days before the account's newest:
  main (halifax-csv, 129 days), savings (halifax-csv, 197), joint-savings (halifax-csv, 148).
  So 3 accounts have a source that has fallen behind. santander-pdf on main is 34 days behind: not.
  One transfer out of main, 110 days ago, has no other side held. It falls after main-old-a's last
  transaction (120 days ago), so that Space's final-movement count is 1, and before the last
  transaction of b, c, and d (107 to 105 days ago), so theirs is 0.
  Bindings: main is fed by one starling id and one truelayer id (normal: two providers, one real
  account); everyday by two starling ids (the fault: two accounts of one provider merged).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from obdi.accounts import AccountRecord, AccountRef
from obdi.ingest import pair_transfers_across_store
from obdi.store import Store
from page_dom import INVISIBLE, Node
from test_ledger import land, txn

FEED = "starling"
AGGREGATOR = "truelayer"
EXPORT = "halifax-csv"
STATEMENT = "santander-pdf"
SOURCES = (FEED, AGGREGATOR, EXPORT, STATEMENT)

BEHIND_DAYS = 60


#: Elements that start a new line of what a reader sees.
_BREAKS = frozenset(
    {
        "address", "article", "aside", "br", "caption", "details", "div", "dd", "dl", "dt",
        "fieldset", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "nav",
        "ol", "p", "section", "summary", "table", "td", "th", "tr", "ul",
    }
)


def rendered_lines(root: Node) -> list[str]:
    """The lines a reader sees: text broken where a block element or a `<br>` starts, with inline
    elements (a link, a code span, a bold word) kept inside the line they sit in."""
    lines: list[str] = [""]

    def walk(node: Node) -> None:
        if node.tag in INVISIBLE:
            return
        broke = node.tag in _BREAKS
        if broke:
            lines.append("")
        for child in node.children:
            if isinstance(child, str):
                lines[-1] += " " + child
            else:
                walk(child)
        if broke:
            lines.append("")

    walk(root)
    cleaned = (" ".join(line.split()) for line in lines)
    return [line for line in cleaned if line]


def repeated_lines(root: Node, *, words: int = 3, more_than: int = 2) -> dict[str, int]:
    """Each line of `words` words or more that appears more than `more_than` times, with how many
    times: the sentence a page says once for every account."""
    counted = Counter(line for line in rendered_lines(root) if len(line.split()) >= words)
    return {line: n for line, n in counted.items() if n > more_than}


def today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True)
class Feed:
    source: str
    first_ago: int
    last_ago: int
    count: int = 5


@dataclass(frozen=True)
class Acct:
    ref: str
    label: str
    feeds: tuple[Feed, ...]
    parent: str | None = None
    closed_ago: int | None = None


def _f(source: str, first: int, last: int, count: int = 5) -> Feed:
    return Feed(source, first, last, count)


MAIN = "main"
ACCOUNTS = (
    Acct(MAIN, "Joint current", (
        _f(FEED, 400, 1, 14), _f(AGGREGATOR, 300, 2, 9), _f(EXPORT, 700, 130, 6),
        _f(STATEMENT, 800, 35, 4),
    )),
    Acct("savings", "Rainy day", (_f(AGGREGATOR, 500, 3), _f(EXPORT, 500, 200))),
    Acct("card", "Blue card", (_f(AGGREGATOR, 300, 1), _f(STATEMENT, 300, 20))),
    Acct("everyday", "Everyday", (_f(FEED, 200, 0), _f(AGGREGATOR, 200, 1))),
    Acct("main-bills", "Bills", (_f(FEED, 100, 2),), parent=MAIN),
    Acct("main-holiday", "Holiday", (_f(FEED, 100, 3),), parent=MAIN),
    Acct("main-trip", "Trip", (_f(FEED, 100, 4),), parent=MAIN),
    Acct("main-old-a", "Old A", (_f(FEED, 300, 120),), parent=MAIN, closed_ago=100),
    Acct("main-old-b", "Old B", (_f(FEED, 300, 107),), parent=MAIN, closed_ago=100),
    Acct("main-old-c", "Old C", (_f(FEED, 300, 106),), parent=MAIN, closed_ago=100),
    Acct("main-old-d", "Old D", (_f(FEED, 300, 105),), parent=MAIN, closed_ago=100),
    Acct("old-card", "Old card", (_f(AGGREGATOR, 1200, 800), _f(EXPORT, 1200, 800))),
    Acct("isa-old", "Old ISA", (_f(EXPORT, 900, 500),)),
    Acct("pension", "Pension", (_f(EXPORT, 400, 20),)),
    Acct("loan", "Car loan", (_f(STATEMENT, 400, 45),)),
    Acct("wallet", "Wallet", (_f(FEED, 100, 1),)),
    Acct("bills-card", "Bills card", (_f(AGGREGATOR, 100, 1),)),
    Acct("joint-savings", "Joint savings", (_f(AGGREGATOR, 300, 2), _f(EXPORT, 300, 150))),
    Acct("holiday-fund", "Holiday fund", (_f(FEED, 200, 1), _f(AGGREGATOR, 200, 2))),
)

PAIRS = sum(len(a.feeds) for a in ACCOUNTS)
ARCHIVED = tuple(a for a in ACCOUNTS if a.closed_ago is not None)
LIVE = tuple(a for a in ACCOUNTS if a.closed_ago is None)
BEHIND = ("main", "savings", "joint-savings")
QUIET = ("old-card", "isa-old")

#: Bound provider accounts: (canonical account, source, provider's id).
BINDINGS = (
    (MAIN, FEED, "uid-main"),
    (MAIN, AGGREGATOR, "tl-main"),
    ("everyday", FEED, "uid-everyday-one"),
    ("everyday", FEED, "uid-everyday-two"),
    ("savings", AGGREGATOR, "tl-savings"),
)
FAULTY_BINDING = "everyday"


def _days(feed: Feed, now: date) -> list[date]:
    first, last = now - timedelta(days=feed.first_ago), now - timedelta(days=feed.last_ago)
    if feed.count <= 2:
        return [first, last][: feed.count]
    span = (last - first).days
    return [first + timedelta(days=span * n // (feed.count - 1)) for n in range(feed.count)]


def build(db: Path, *, accounts: tuple[Acct, ...] = ACCOUNTS) -> None:
    now = today()
    with Store(db) as store:
        for account in accounts:
            store.declare_account(
                AccountRecord(
                    ref=AccountRef(account.ref),
                    label=account.label,
                    kind="starling-space" if account.parent else "",
                    parent=AccountRef(account.parent) if account.parent else None,
                    closed=(
                        now - timedelta(days=account.closed_ago)
                        if account.closed_ago is not None
                        else None
                    ),
                )
            )
        for account in accounts:
            rows = []
            for feed in account.feeds:
                for number, day in enumerate(_days(feed, now)):
                    rows.append(
                        txn(
                            account.ref, feed.source, f"{account.ref}-{feed.source}-{number}",
                            day, -(100 + number), f"{feed.source} {account.ref} {number}",
                        )
                    )
            land(store, f"digest-{account.ref}", *rows)
        # One transfer out of `main` after main-old-a's last transaction, with no other side held.
        late = now - timedelta(days=110)
        land(
            store, "digest-late-leg",
            txn(MAIN, FEED, "main-late-leg", late, -777, "late leg", internal=True),
        )
        pair_transfers_across_store(store)


def write_map(root: Path) -> None:
    (root / "accounts.json").write_text(
        json.dumps(
            {
                "bindings": [
                    {"canonical_id": canonical, "source": source, "provider_account_id": ref}
                    for canonical, source, ref in BINDINGS
                ],
                "actual": [],
            }
        ),
        encoding="utf-8",
    )
