"""A household whose coverage is drawn by hand here, before any run, and built through the doors.

TODAY is 2026-10-05 (London summer time throughout, one hour ahead of UTC). Every figure is
invented. A row is named by its letter; "feed" is the bank's own feed, "agg" the aggregator,
"csv" the export file. Each row is listed by exactly the captures named.

ACCOUNT `main`

  Rows (amount out, in pounds)         date    listed by
    R1  10.00 Alpha                    07-02   feed1, agg1, csv1
    R2  11.00 Bravo                    07-15   feed1, agg1, csv1
    R3  12.00 Charlie                  07-30   feed1, agg1            (NOT in csv1)
    R3b 13.00 Charlie Two              07-30   feed1, agg1, csv1
    R4  14.00 Delta                    07-31   feed1, agg1, csv2
    R5  15.00 Echo                     08-04   feed1, agg1, csv2, csv3
    R6  16.00 Foxtrot                  08-10   feed2, agg1, csv3
    R7  17.00 Golf                     08-18   feed2, csv3
    R8  18.00 Hotel                    08-18   feed2, csv3
    R9  19.00 India                    08-19   feed2, csv4
    R10 20.00 Juliet                   08-25   csv4          (agg2 covers the day, lacks it)
    R11 22.00 Kilo                     09-02   agg2
    M1  21.00 Typed                    09-02   typed entry
    R12 23.00 Lima                     09-12   csv5
    R13 24.00 Mike                     09-20   csv5
    R14 25.00 November                 09-21   csv6
    R15 26.00 Oscar                    09-28   csv6

  Captures (what each asked, when; London time is UTC + 1)
    feed1  08-04 10:00Z (11:00)  changesSince 07-01 00:00Z  covers 07-01 to 08-04, partial 11/24
    feed2  08-20 08:00Z (09:00)  changesSince 08-04 10:00Z  covers 08-04 to 08-20, partial 9/24
    agg1   08-10 12:00Z (13:00)  from 07-01 to 08-10        covers 07-01 to 08-10, partial 13/24
    agg2   09-10 12:00Z (13:00)  from 08-20 to 09-10        covers 08-20 to 09-10, partial 13/24
    csv1 07-02 to 07-30    csv2 07-31 to 08-04    csv3 08-04 to 08-18
    csv4 08-19 to 08-25    csv5 09-12 to 09-20    csv6 09-21 to 09-28
    (a file states no span and no export time: its span is its first and last row, observed, and
    its last day is possibly partial)

  Seams, as drawn on each source's lane (the day is the capture's last day)
    feed 08-04   feed1 -> feed2   feed2 starts on 08-04: OVERLAPPED, quiet
    agg 08-10    agg1 -> agg2     gapped; day 08-10 listed by feed2 (R6) and by agg1 too: CLEAN
    csv 07-30    csv1 -> csv2     abutting; feed lists R3 that no export lists: RED, 1 row
    csv 08-04    csv2 -> csv3     csv3 starts on 08-04: OVERLAPPED, quiet
    csv 08-18    csv3 -> csv4     abutting; feed lists R7 and R8, csv3 lists both: CLEAN
    csv 09-20    csv5 -> csv6     abutting; no other source covers 09-20: UNCHECKED, amber
    (csv4 -> csv5 is a gap of 08-26 to 09-11, not a seam: the day csv4 ended is 08-25 and the
    seam rule only meets the NEXT capture, which starts 09-12; the verdict for 08-25 is by
    arithmetic: agg2 covers 08-25 and lists nothing there, so nothing is missing: CLEAN)
    Seams that need a look: 1 red (csv 07-30) and 1 amber (csv 09-20).

  Gaps: agg 08-11 to 08-19 (asked, never answered: within unattended reach); csv 08-26 to 09-11.
  Rows one covering source lists and another covering source does not: R10 on the aggregator's
  lane at 08-25 (R3 on the export lane at 07-30 is the red seam's own finding, not repeated).

  Known balances (end of day): 07-01 1000.00, 08-04 925.00, 09-02 792.00, 09-20 745.00, and
  09-28 700.00 where the rows say 694.00. In agreement through 09-20, held back from 09-28.

ACCOUNT `card`: statements only (Santander layout; closing dates are the stated dates). What each
statement covers is `statement_span`'s to say; the answers below are worked out from its rules.
    S1 closes 06-11, rows 05-15 and 06-11                       first statement: start observed
    S2 closes 07-11, rows 06-14 and 07-09, opens on S1's closing
                                                                balances meet a period on: starts
                                                                06-12, known only as "balances meet"
    S3 closes 08-11: not held
    S4 closes 09-11, rows 08-14 and 09-05, opens on S3's closing, not S2's: unequal balances prove
       a statement is missing; the hole is 07-12 to 08-11 (the closing day it would have had,
       inferred) and S4 is taken to begin 08-12, inferred
  Covered: 05-15 to 06-11, 06-12 to 07-11, 08-12 to 09-11. Hole: 07-12 to 08-11.
  Cadence 30 days (three statements, the lower median of 30 and 62), the last closing 09-11, so the
  next is expected to close 10-11: nothing is due on 10-05.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path

from obdi.balance_anchors import record_stated_anchor
from obdi.core.models import RawArtefact, SourceTier, Transaction
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.providers import starling
from obdi.ingest.statement_terms import keep_statement_readings
from obdi.ingest.store import Store
from obdi.ingest.typed_transactions import record_typed_transaction
from test_period_reconciliation import _statement

TODAY = date(2026, 10, 5)
MAIN = "main"
CARD = "card"
CONNECTION = "starling-api"


@dataclass(frozen=True)
class Row:
    key: str
    day: str
    pounds: str
    payee: str


ROWS = {
    row.key: row
    for row in (
        Row("R1", "2026-07-02", "10.00", "Alpha"),
        Row("R2", "2026-07-15", "11.00", "Bravo"),
        Row("R3", "2026-07-30", "12.00", "Charlie"),
        Row("R3b", "2026-07-30", "13.00", "Charlie Two"),
        Row("R4", "2026-07-31", "14.00", "Delta"),
        Row("R5", "2026-08-04", "15.00", "Echo"),
        Row("R6", "2026-08-10", "16.00", "Foxtrot"),
        Row("R7", "2026-08-18", "17.00", "Golf"),
        Row("R8", "2026-08-18", "18.00", "Hotel"),
        Row("R9", "2026-08-19", "19.00", "India"),
        Row("R10", "2026-08-25", "20.00", "Juliet"),
        Row("R11", "2026-09-02", "22.00", "Kilo"),
        Row("M1", "2026-09-02", "21.00", "Quince Lunch"),
        Row("R12", "2026-09-12", "23.00", "Lima"),
        Row("R13", "2026-09-20", "24.00", "Mike"),
        Row("R14", "2026-09-21", "25.00", "November"),
        Row("R15", "2026-09-28", "26.00", "Oscar"),
    )
}

#: Which rows each capture lists, and when it was taken (UTC) and what it asked.
FEED = (
    ("2026-08-04T10:00:00+00:00", "changesSince=2026-07-01T00:00:00+00:00",
     ("R1", "R2", "R3", "R3b", "R4", "R5")),
    ("2026-08-20T08:00:00+00:00", "changesSince=2026-08-04T10:00:00+00:00",
     ("R6", "R7", "R8", "R9")),
)
AGGREGATOR = (
    ("2026-08-10T12:00:00+00:00", "from=2026-07-01&to=2026-08-10",
     ("R1", "R2", "R3", "R3b", "R4", "R5", "R6")),
    ("2026-09-10T12:00:00+00:00", "from=2026-08-20&to=2026-09-10", ("R11",)),
)
EXPORTS = (
    ("R1", "R2", "R3b"),
    ("R4", "R5"),
    ("R5", "R6", "R7", "R8"),
    ("R9", "R10"),
    ("R12", "R13"),
    ("R14", "R15"),
)
BALANCES = (
    ("2026-07-01", "1000.00"),
    ("2026-08-04", "925.00"),
    ("2026-09-02", "792.00"),
    ("2026-09-20", "745.00"),
    ("2026-09-28", "700.00"),
)


def _transaction(key: str, *, source: str, source_id: str | None, tier: SourceTier) -> Transaction:
    row = ROWS[key]
    day = date.fromisoformat(row.day)
    minor = -round(float(row.pounds) * 100)
    return Transaction(
        account_id=MAIN,
        amount_minor=minor,
        currency="GBP",
        value_date=day,
        booking_date=day,
        description=row.payee,
        source=source,
        source_id=source_id,
        tier=tier,
        content_key=content_key(amount_minor=minor, value_date=day, description=row.payee),
    )


def _land(store: Store, *, source: str, body: bytes, account: str = MAIN) -> RawArtefact:
    artefact = starling.artefact_for(body, account_id=account, kind="feed")
    artefact = replace(artefact, source=source, fetched_at=datetime.now(UTC))
    store.land_artefact(artefact)
    return artefact


def _api_capture(
    store: Store, *, source: str, ask_source: str, tier: SourceTier, taken: str, asked: str,
    keys: tuple[str, ...], stem: str,
) -> None:
    body = json.dumps({"taken": taken, "asked": asked, "keys": keys}).encode()
    artefact = _land(store, source=ask_source, body=body)
    store.record_attempt(
        source=ask_source,
        connection_id=CONNECTION,
        account_ref=MAIN,
        asked=asked,
        request_meta="{}",
        outcome="landed",
        http_status=200,
        artefact_digest=artefact.digest,
        now=datetime.fromisoformat(taken),
    )
    reconcile_batch(
        store,
        [_transaction(k, source=source, source_id=f"{stem}-{k}", tier=tier) for k in keys],
        digest=artefact.digest,
    )


def build_main(
    root: Path,
    store: Store,
    *,
    with_balances: bool = True,
    exports: tuple[tuple[str, ...], ...] = EXPORTS,
    aggregator: tuple[tuple[str, str, tuple[str, ...]], ...] = AGGREGATOR,
) -> None:
    """The household current account; `exports` and `aggregator` are replaced to build the
    opposite of a scenario (the same seam overlapped, the same gap filled)."""
    store.declare_account(AccountRecord(ref=AccountRef(MAIN), label="Household current"))
    for taken, asked, keys in FEED:
        _api_capture(
            store, source="starling", ask_source="starling-feed", tier=SourceTier.AUTHORITATIVE,
            taken=taken, asked=asked, keys=keys, stem="f",
        )
    for taken, asked, keys in aggregator:
        _api_capture(
            store, source="truelayer", ask_source="truelayer-booked",
            tier=SourceTier.AUTHORITATIVE, taken=taken, asked=asked, keys=keys, stem="a",
        )
    for number, keys in enumerate(exports, start=1):
        lines = "".join(
            f"{ROWS[k].day[8:]}/{ROWS[k].day[5:7]}/{ROWS[k].day[:4]},{ROWS[k].payee},"
            f"{ROWS[k].payee},FASTER PAYMENT,-{ROWS[k].pounds}\n"
            for k in keys
        )
        path = root / f"export-{number}.csv"
        path.write_text(
            "Date,Counter Party,Reference,Type,Amount (GBP)\n" + lines, encoding="utf-8"
        )
        import_file(store, path, account_id=MAIN)
    record_typed_transaction(
        store, MAIN, ROWS["M1"].day, "out", ROWS["M1"].pounds, ROWS["M1"].payee,
        today=TODAY,
    )
    if with_balances:
        for day, pounds in BALANCES:
            record_stated_anchor(store, MAIN, day, pounds, today=TODAY)


CARD_STATEMENTS = (
    ("11th Jun 2026", [("15th May", "Alpha Grocer", 1200), ("11th Jun", "Bravo Fuel", 800)], True),
    ("11th Jul 2026", [("14th Jun", "Charlie Cafe", 500), ("9th Jul", "Delta Books", 700)], True),
    ("11th Aug 2026", [("15th Jul", "Echo Rail", 300)], False),
    ("11th Sep 2026", [("14th Aug", "Foxtrot Gym", 400), ("5th Sep", "Golf Shop", 600)], True),
)


#: The same card where the statement that is not held (S3) paid money out and took it back
#: within the period, so its movements net to nil: S4 opens on the balance S2 closed on, though a
#: whole statement lies between them.
CARD_NET_NIL_STATEMENTS = (
    *CARD_STATEMENTS[:2],
    (
        "11th Aug 2026",
        [("15th Jul", "Echo Rail", 300), ("20th Jul", "Echo Refund", -300)],
        False,
    ),
    CARD_STATEMENTS[3],
)


def build_card(
    root: Path,
    store: Store,
    *,
    skip: tuple[int, ...] = (),
    statements: tuple[tuple[str, list[tuple[str, str, int]], bool], ...] = CARD_STATEMENTS,
) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(CARD), label="Household card"))
    owed = 10000
    for position, (day, rows, held) in enumerate(statements):
        payload, owed = _statement(day, owed, rows)
        if held and position not in skip:
            path = root / f"statement-{position}.pdf"
            path.write_bytes(payload)
            import_file(store, path, account_id=CARD)


LONG = "long"


def build_long(root: Path) -> Path:
    """One account over nearly eight years in which, for most of it, nothing changes.

    A single export file lists one row of 10.00 on the 15th of every month from 2019-01 to
    2026-09 (93 rows), and known balances are stated on 2019-01-01 (1000.00), 2022-06-15 (580.00)
    and 2026-09-15 (70.00), each of which the rows reproduce. So the verification lane agrees
    from 2019-01-01 to 2026-09-15 with nothing else to see: the export covers 2019-01-15 to
    2026-09-15 and then stops, 20 days short of today (2026-10-05). Everything between the
    margins of those events (2019-01-26 to 2026-09-04) is one quiet stretch.
    """
    db = root / "long.sqlite3"
    with Store(db) as store:
        land_long(root, store)
    return db


def land_long(root: Path, store: Store) -> None:
    """The long account's rows and balances, landed into `store` (`build_long` describes them)."""
    store.declare_account(AccountRecord(ref=AccountRef(LONG), label="Long account"))
    lines = "".join(
        f"15/{month:02d}/{year},Standing order,Rent,STANDING ORDER,-10.00\n"
        for year in range(2019, 2027)
        for month in range(1, 13)
        if (year, month) <= (2026, 9)
    )
    path = root / "long.csv"
    path.write_text("Date,Counter Party,Reference,Type,Amount (GBP)\n" + lines, encoding="utf-8")
    import_file(store, path, account_id=LONG)
    for day, pounds in (
        ("2019-01-01", "1000.00"),
        ("2022-06-15", "580.00"),
        ("2026-09-15", "70.00"),
    ):
        record_stated_anchor(store, LONG, day, pounds, today=TODAY)


def build_household(
    root: Path,
    *,
    exports: tuple[tuple[str, ...], ...] = EXPORTS,
    aggregator: tuple[tuple[str, str, tuple[str, ...]], ...] = AGGREGATOR,
    card_statements: tuple[tuple[str, list[tuple[str, str, int]], bool], ...] = CARD_STATEMENTS,
) -> Path:
    """Land both accounts at `root/store.sqlite3`.

    No rebuild runs: the feed and aggregator payloads here carry only what names the capture,
    and a rebuild would re-derive the rows from them. The statements' readings are kept as the
    pass that folds same money keeps them.
    """
    db = root / "store.sqlite3"
    with Store(db) as store:
        build_main(root, store, exports=exports, aggregator=aggregator)
        build_card(root, store, statements=card_statements)
        keep_statement_readings(store)
    return db
