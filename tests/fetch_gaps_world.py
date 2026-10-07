"""A household whose every gap is decided here, in dates, before anything is built.

`TODAY` is 2026-10-05; nothing reads a clock. Statements and exports come in through the doors a
person's files use (`import_file`), the bank's feed through `reconcile_batch`, and balances
through `record_stated_anchor`. A statement is one row dated five days before its closing day,
chained so that each opens on the balance the one before closed on.

The accounts, and the answer each is decided to have (gaps are quoted as
KIND first..last, basis, source, probably):

  card-behind     Santander statements closing 04-10, 05-10, 06-10, 07-10; feed rows 08-02,
                  09-20, 10-05 and no statement since.
                  NEWER_STATEMENT 2026-07-11..2026-10-05, stated, santander-cc-pdf,
                  probably 2 closing 2026-08-10 and 2026-09-10 (10-10 has not come yet).
  card-late-one   ONE statement closing 06-10, a feed row on 08-01.
                  NEWER_STATEMENT 2026-06-11..2026-08-01, stated, probably None: one statement
                  is no cadence.
  card-hole       Santander statements closing 01-10, 02-10, 03-10, 05-10, 06-10, 07-10, 08-10,
                  09-10: April's is missing (and held no money, so the chain still joins).
                  HOLE_BETWEEN 2026-03-11..2026-04-10, INFERRED, probably 1 closing 2026-04-10.
                  (First decided as ending 05-09, the day before the later statement closed;
                  the missing statement's own period ends at its expected closing, 04-10.)
  card-virgin     Virgin Money statements stating their periods: 04-05..05-04, 05-05..06-04,
                  07-05..08-04, 08-05..09-04. The third says it begins on 07-05, a month after
                  the second closed.
                  HOLE_BETWEEN 2026-06-05..2026-07-04, STATED, virgin-money-cc-pdf.
  card-early      Feed rows on 06-01 and 06-20; Santander statements closing 08-10 and 09-10, the
                  first listing a row on 08-05, so its opening is the end of 08-04.
                  NOTHING_BEFORE 2026-06-01..2026-08-04, stated.
  card-quiet      Santander statements closing 06-10, 07-10, 08-10, 09-10 and nothing else.
                  No gap. Twenty-five days since the last: the next is expected 2026-10-10.
  card-single     One statement closing 09-10 that lists every row the account holds. No gap: the
                  statement tests itself by what it lists (`agreement`, R1). (It was decided as
                  ONE_BALANCE 2026-09-05..2026-09-10 while a lone statement was one known balance
                  that only set the opening and nothing tested.)
  card-feed-only  Feed rows 08-02 and 09-15, no file, no balance.
                  AUTOMATIC_ONLY 2026-08-02..2026-09-15, stated.
  card-qif        A QIF export with rows 08-03 and 08-20, no balance.
                  NO_BALANCE 2026-08-03..2026-08-20, stated, qif.
  main            A Starling CSV export with rows 01-12, 02-12, 04-12, 05-12, 06-12, 07-12 and
                  08-04, and the aggregator's rows 03-15, 08-20, 09-15, 10-01; balances stated on
                  01-11 and 10-01 that the rows reproduce.
                  EXPORT_STOPS 2026-08-05..2026-10-05, stated, starling-csv (62 days);
                  EXPORT_MONTHS 2026-03-01..2026-03-31, stated, starling-csv.
  savings-hand    A balance-only account with two stated balances. No gap, and not a file.
  card-old        Archived on 2026-03-01 with a feed row and nothing else. Not listed at all.
  opens-on-day    From the flag world: rows from 09-03, balances on 09-14 and 09-20, and an open
                  flag the earlier balance would settle.
                  NOTHING_BEFORE 2026-09-03..2026-09-14, FLAG_SETTLE 2026-09-14 ("before").
  between         From the flag world: rows from 09-03, balances 09-10 and 09-20, its flag
                  settled. NOTHING_BEFORE 2026-09-03..2026-09-10 and no FLAG_SETTLE.

So 13 things to fetch for 11 accounts; card-quiet and savings-hand need nothing.
(The first hand count said 12 for 10 accounts with card-single needing nothing; and that every
card would have "rows before the first known balance" was NOT decided: the first run showed the
first statement's own rows counted as untested, which the rule now excludes because the statement
itself lists them.)
`repaired=True` fills each hole the others name: card-behind's two statements (the first listing
the feed row of 08-02), card-hole's April, card-virgin's middle period, card-early's earlier
statement (closing 06-30 and listing the two feed rows), and the export that covers every month
to 10-01. Then card-behind, card-hole, card-virgin, card-early and main have no gap, and
card-behind, card-hole, card-early and card-quiet expect their next statement on 2026-10-10 and
card-virgin on 2026-11-04.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from flag_balance_world import build_balance_world
from obdi.balance_anchors import record_stated_anchor
from obdi.core.models import SourceTier, Transaction
from obdi.fetch_gaps import (
    AccountOutlook,
    FetchEvidence,
    FetchGap,
    fetch_report,
    gather_evidence,
)
from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.store import Store
from obdi.ingest.synthetic_pdf import build_pdf
from obdi.standing_data import AccountStanding, standings_for

TODAY = date(2026, 10, 5)
D = date
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def ordinal(day: date) -> str:
    endings = {1: "st", 2: "nd", 3: "rd"}
    suffix = "th" if 10 <= day.day % 100 <= 20 else endings.get(day.day % 10, "th")
    return f"{day.day}{suffix}"


@dataclass
class Household:
    """What was planted, so a page can be searched for it."""

    figures: list[str] = field(default_factory=list)
    payees: list[str] = field(default_factory=list)
    _count: int = 0

    def next_row(self, payee_stem: str) -> tuple[str, int]:
        """A distinctive payee and an amount in minor units no other row shares."""
        self._count += 1
        payee = f"{payee_stem} Zeppelin {self._count}"
        minor = 1337 + 211 * self._count
        self.payees.append(payee)
        self.figures.append(f"{minor // 100}.{minor % 100:02}")
        return payee, minor


def _pounds(minor: int) -> str:
    return f"{minor / 100:,.2f}"


def santander_statements(
    store: Store,
    root: Path,
    ref: str,
    closings: list[date],
    house: Household,
    *,
    start_owed: int = 10000,
    rows_for: dict[date, list[tuple[date, str, int]]] | None = None,
) -> int:
    """One statement per closing day, each with one row five days before it unless `rows_for`
    names its rows, chained from `start_owed`. Returns the balance owed after the last, so a
    later call can carry on the chain."""
    owed = start_owed
    for closing in closings:
        if rows_for is not None and closing in rows_for:
            rows = rows_for[closing]
        else:
            payee, minor = house.next_row(ref)
            rows = [(date.fromordinal(closing.toordinal() - 5), payee, minor)]
        total = sum(minor for _, _, minor in rows)
        lines = [
            "Santander UK plc. Registered Office: 2 Triton Square",
            f"Statement Date: {ordinal(closing)} {MONTHS[closing.month - 1]} {closing.year}"
            "      Page No: 1 / 1",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {_pounds(owed)}",
            *(
                f"{ordinal(day)} {MONTHS[day.month - 1]} {payee}   {_pounds(minor)}"
                for day, payee, minor in rows
            ),
            f"Your new balance:                                        {_pounds(owed + total)}",
        ]
        owed += total
        path = root / f"{ref}-{closing.isoformat()}.pdf"
        path.write_bytes(build_pdf(lines))
        import_file(store, path, account_id=ref)
    return owed


def virgin_statements(
    store: Store,
    root: Path,
    ref: str,
    periods: list[tuple[date, date]],
    house: Household,
) -> None:
    """Virgin Money statements that state their own periods, one spend in each."""
    owed = 10000
    for opens, closes in periods:
        payee, minor = house.next_row(ref)
        when = date.fromordinal(opens.toordinal() + 3)
        lines = [
            f"Statement  period: {opens:%d/%m/%Y} - {closes:%d/%m/%Y}",
            "Your credit card account is a Virgin Money account (Your credit limit: £4,000)",
            f"Balance  from your  previous statement                    £{_pounds(owed)}",
            "Transaction  date    Post date            Description                  Amount",
            f"{when.day:02} {MONTHS[when.month - 1]} {when:%y}   {when.day:02} "
            f"{MONTHS[when.month - 1]} {when:%y}   {payee}   £{_pounds(minor)}",
            f"Your new  balance                                         £{_pounds(owed + minor)}",
        ]
        owed += minor
        path = root / f"{ref}-{closes.isoformat()}.pdf"
        path.write_bytes(build_pdf(lines))
        import_file(store, path, account_id=ref)


def feed(store: Store, ref: str, rows: list[tuple[date, int, str]], *, digest: str) -> None:
    reconcile_batch(
        store,
        [
            Transaction(
                account_id=ref,
                amount_minor=minor,
                currency="GBP",
                value_date=when,
                booking_date=when,
                description=description,
                source="truelayer-booked",
                source_id=f"{ref}-{when.isoformat()}-{minor}",
                tier=SourceTier.AUTHORITATIVE,
                content_key=content_key(
                    amount_minor=minor, value_date=when, description=description
                ),
            )
            for when, minor, description in rows
        ],
        digest=digest,
    )


def _declare(store: Store, ref: str, label: str, **fields: object) -> None:
    store.declare_account(AccountRecord(ref=AccountRef(ref), label=label, **fields))  # type: ignore[arg-type]


LABELS = {
    "card-behind": "Behind card",
    "card-late-one": "Late-one card",
    "card-hole": "Hole card",
    "card-virgin": "Virgin card",
    "card-early": "Early card",
    "card-quiet": "Quiet card",
    "card-single": "Single card",
    "card-feed-only": "Feed-only card",
    "card-qif": "Qif card",
    "main": "Main account",
    "savings-hand": "Hand savings",
    "card-old": "Old card",
}

#: The main account's rows: (day, minor, payee). Held by the export unless marked feed.
MAIN_EXPORT = [
    (D(2026, 1, 12), -1111, "Plant Pots Lorry"),
    (D(2026, 2, 12), -2222, "Garden Gate Lorry"),
    (D(2026, 4, 12), -3333, "Hedge Trimmer Lorry"),
    (D(2026, 5, 12), -4444, "Lawn Seed Lorry"),
    (D(2026, 6, 12), -5555, "Wheelbarrow Lorry"),
    (D(2026, 7, 12), -6666, "Compost Bin Lorry"),
    (D(2026, 8, 4), -7777, "Watering Can Lorry"),
]
MAIN_FEED = [
    (D(2026, 3, 15), -8888, "Fence Panel Lorry"),
    (D(2026, 8, 20), -9999, "Shed Roof Lorry"),
    (D(2026, 9, 15), -1221, "Trellis Lorry"),
    (D(2026, 10, 1), -1331, "Bird Table Lorry"),
]


def _csv(root: Path, name: str, rows: list[tuple[date, int, str]]) -> Path:
    path = root / f"{name}.csv"
    lines = "".join(
        f"{day:%d/%m/%Y},{payee},{payee} ref,CARD,{minor / 100:.2f},0\n"
        for day, minor, payee in sorted(rows)
    )
    path.write_text(
        "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP)\n" + lines, encoding="utf-8"
    )
    return path


def build_household(root: Path, *, repaired: bool = False) -> tuple[Path, Household]:
    """Land the household at `root/store.sqlite3`; return it and what was planted."""
    db = build_balance_world(root, {"opens-on-day", "between"})
    house = Household()
    with Store(db) as store:
        for ref, label in LABELS.items():
            kind = BALANCE_ONLY_KIND if ref == "savings-hand" else ""
            closed = D(2026, 3, 1) if ref == "card-old" else None
            _declare(store, ref, label, kind=kind, closed=closed)

        behind = [D(2026, 4, 10), D(2026, 5, 10), D(2026, 6, 10), D(2026, 7, 10)]
        if repaired:
            behind += [D(2026, 8, 10), D(2026, 9, 10)]
        listed = {D(2026, 8, 10): [(D(2026, 8, 2), "Feed Only Zeppelin A", 1500)]}
        santander_statements(store, root, "card-behind", behind, house, rows_for=listed)
        feed(
            store,
            "card-behind",
            [
                (D(2026, 8, 2), -1500, "Feed Only Zeppelin A"),
                (D(2026, 9, 20), -1600, "Feed Only Zeppelin B"),
                (D(2026, 10, 5), -1700, "Feed Only Zeppelin C"),
            ],
            digest="behind",
        )

        santander_statements(store, root, "card-late-one", [D(2026, 6, 10)], house)
        feed(store, "card-late-one", [(D(2026, 8, 1), -1800, "Late One Zeppelin")], digest="late")

        hole = [D(2026, m, 10) for m in (1, 2, 3, 5, 6, 7, 8, 9)]
        if repaired:
            hole.insert(3, D(2026, 4, 10))
        santander_statements(store, root, "card-hole", hole, house)

        virgin = [
            (D(2026, 4, 5), D(2026, 5, 4)),
            (D(2026, 5, 5), D(2026, 6, 4)),
            (D(2026, 7, 5), D(2026, 8, 4)),
            (D(2026, 8, 5), D(2026, 9, 4)),
        ]
        if repaired:
            virgin.insert(2, (D(2026, 6, 5), D(2026, 7, 4)))
        virgin_statements(store, root, "card-virgin", virgin, house)

        early_rows = [
            (D(2026, 6, 1), -1900, "Early Zeppelin A"),
            (D(2026, 6, 20), -2000, "Early Zeppelin B"),
        ]
        feed(store, "card-early", early_rows, digest="early")
        house.payees += [payee for _, _, payee in early_rows]
        house.figures += ["19.00", "20.00"]
        owed = 10000
        if repaired:
            owed = santander_statements(
                store,
                root,
                "card-early",
                [D(2026, 6, 30)],
                house,
                rows_for={D(2026, 6, 30): [(d, p, -m) for d, m, p in early_rows]},
            )
        santander_statements(
            store, root, "card-early", [D(2026, 8, 10), D(2026, 9, 10)], house, start_owed=owed
        )

        santander_statements(
            store, root, "card-quiet", [D(2026, m, 10) for m in (6, 7, 8, 9)], house
        )
        santander_statements(store, root, "card-single", [D(2026, 9, 10)], house)

        feed(
            store,
            "card-feed-only",
            [
                (D(2026, 8, 2), -2100, "Only Feed Zeppelin A"),
                (D(2026, 9, 15), -2200, "Only Feed Zeppelin B"),
            ],
            digest="feed-only",
        )

        qif = root / "card-qif.qif"
        qif.write_text(
            "!Type:CCard\nD03/08/2026\nT-23.00\nPQif Zeppelin A\n^\nD20/08/2026\nT-24.00\n"
            "PQif Zeppelin B\n^\n",
            encoding="utf-8",
        )
        import_file(store, qif, account_id="card-qif")

        exported = [*MAIN_EXPORT, *MAIN_FEED] if repaired else MAIN_EXPORT
        import_file(store, _csv(root, "main", exported), account_id="main")
        feed(store, "main", MAIN_FEED, digest="main-feed")
        record_stated_anchor(store, "main", "2026-01-11", "1011.11", today=TODAY)
        record_stated_anchor(store, "main", "2026-10-01", "485.64", today=TODAY)

        record_stated_anchor(store, "savings-hand", "2026-08-01", "1500.00", today=TODAY)
        record_stated_anchor(store, "savings-hand", "2026-09-01", "1600.00", today=TODAY)

        feed(store, "card-old", [(D(2026, 1, 5), -2500, "Old Zeppelin")], digest="old")
    house.payees += [payee for _, _, payee in [*MAIN_EXPORT, *MAIN_FEED]]
    house.figures += ["11.11", "22.22", "33.33", "44.44", "55.55", "66.66", "77.77", "88.88"]
    return db, house


class Loaded:
    """The household, its standings and evidence, and the report for `TODAY`."""

    def __init__(
        self,
        db: Path,
        house: Household,
        standings: Mapping[str, AccountStanding],
        evidence: FetchEvidence,
    ) -> None:
        self.db, self.house, self.standings, self.evidence = db, house, standings, evidence
        self.report = fetch_report(evidence, standings, TODAY)

    def gaps(self, ref: str) -> list[FetchGap]:
        return [g for o in self.report.accounts if o.account == ref for g in o.gaps]

    def outlook(self, ref: str) -> AccountOutlook | None:
        return next((o for o in self.report.accounts if o.account == ref), None)


def load_household(root: Path, *, repaired: bool = False) -> Loaded:
    db, house = build_household(root, repaired=repaired)
    with Store(db) as store:
        refs = [
            str(row[0])
            for row in store.connection.execute("SELECT DISTINCT account_id FROM transactions")
        ]
        standings = standings_for(store, refs, families=None, movement=None)
        return Loaded(db, house, standings, gather_evidence(store))
