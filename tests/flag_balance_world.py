"""A household of card accounts whose flagged pairs are decided by balances, worked out first.

Each account is a card whose statement-like export lists its payments, so one source lists both
rows of a pair and the matcher raises a flag. Known balances are stated through the ledger
page's door. All figures are invented and worked out by hand, in pounds, before any run.

The standard rows are 10.00 out on 2026-09-03, TWO payments of 20.00 out on 2026-09-14 (the
pair), and 5.00 out on 2026-09-20. A balance of 100.00 at the end of 09-10 therefore means the
account opened at 110.00, and with both of the pair counted it stands at

    09-12  100.00     09-14  60.00     09-20  55.00

Were the pair one payment it would stand at 80.00 on 09-14 and 75.00 on 09-20.

Each account below is the standard rows with the known balances (end of day) and answer given.

    between         09-10 100.00, 09-20 55.00      settled
    closes-on-day   09-10 100.00, 09-14 60.00      settled
    opens-on-day    09-14 60.00, 09-20 55.00       open; no known balance before 09-14
    unreproduced    09-10 100.00, 09-20 75.00      open; the later balance is the figure with
                                                   one of the pair counted, so nothing is missing
    first           also 6.00 out on 09-22;        open; no known balance before 09-14
                    09-20 55.00, 09-30 49.00
    last            09-10 100.00, 09-12 100.00     open; no known balance after 09-14
    single          09-20 55.00                    open; one known balance, 09-20
    middle-unmet    09-10 100.00, 09-16 90.00      open; the rows say 60.00 on 09-16, which
                    (not reproduced), 09-20 55.00  breaks the span
    triple          a third 20.00 on 09-14;        both flags settled; the rows between are
                    09-10 100.00, 09-20 35.00      3 x 20.00 + 5.00
    triple-short    the same rows;                 both flags open; 55.00 is two of the three
                    09-10 100.00, 09-20 55.00
    apart           20.00 on 09-12 and 09-15       settled; 09-14 80.00 lies between the rows
                    instead of the pair;           and is only a check inside the span
                    09-10 100.00, 09-14 80.00,
                    09-20 55.00
    apart-early     the same rows;                 open; no known balance after 09-15, because
                    09-10 100.00, 09-14 80.00      09-14 lies before the second row
    two-responses   every row from the aggregator, open; no source lists both rows, however
                    the pair in two responses;     the balances look
                    09-10 100.00, 09-20 55.00
    typed-only      tracked by its stated          open; its balances are followed, not tested
                    balances alone;
                    09-10 100.00, 09-20 55.00
    empty           none                           open; no known balance before 09-14
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from landing import import_file, rebuild_from_raw
from obdi.ingest.accounts import BALANCE_ONLY_KIND, AccountRecord, AccountRef
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import record_stated_anchor

#: The day nothing may be stated after, so no test reads the clock.
TODAY = date(2026, 10, 1)

PAYEE = "Brass Kettle Ltd"
PAIR_FIGURE = "20.00"

Rows = tuple[tuple[int, str], ...]
Balances = tuple[tuple[str, str], ...]

STANDARD: Rows = ((3, "10.00"), (14, PAIR_FIGURE), (14, PAIR_FIGURE), (20, "5.00"))
TRIPLE: Rows = ((3, "10.00"), (14, PAIR_FIGURE), (14, PAIR_FIGURE), (14, PAIR_FIGURE), (20, "5.00"))
APART: Rows = ((3, "10.00"), (12, PAIR_FIGURE), (15, PAIR_FIGURE), (20, "5.00"))

#: account -> (rows, known balances). Each balance is (day text, amount text).
EXPORTED: dict[str, tuple[Rows, Balances]] = {
    "between": (STANDARD, (("2026-09-10", "100.00"), ("2026-09-20", "55.00"))),
    "closes-on-day": (STANDARD, (("2026-09-10", "100.00"), ("2026-09-14", "60.00"))),
    "opens-on-day": (STANDARD, (("2026-09-14", "60.00"), ("2026-09-20", "55.00"))),
    "unreproduced": (STANDARD, (("2026-09-10", "100.00"), ("2026-09-20", "75.00"))),
    "first": (
        (*STANDARD, (22, "6.00")),
        (("2026-09-20", "55.00"), ("2026-09-30", "49.00")),
    ),
    "last": (STANDARD, (("2026-09-10", "100.00"), ("2026-09-12", "100.00"))),
    "single": (STANDARD, (("2026-09-20", "55.00"),)),
    "middle-unmet": (
        STANDARD,
        (("2026-09-10", "100.00"), ("2026-09-16", "90.00"), ("2026-09-20", "55.00")),
    ),
    "triple": (TRIPLE, (("2026-09-10", "100.00"), ("2026-09-20", "35.00"))),
    "triple-short": (TRIPLE, (("2026-09-10", "100.00"), ("2026-09-20", "55.00"))),
    "apart": (
        APART,
        (("2026-09-10", "100.00"), ("2026-09-14", "80.00"), ("2026-09-20", "55.00")),
    ),
    "apart-early": (APART, (("2026-09-10", "100.00"), ("2026-09-14", "80.00"))),
    "typed-only": (STANDARD, (("2026-09-10", "100.00"), ("2026-09-20", "55.00"))),
    "empty": (STANDARD, ()),
}

#: The account fed by the aggregator alone, whose pair arrives in two responses.
AGGREGATED = "two-responses"
AGGREGATED_BALANCES: Balances = (("2026-09-10", "100.00"), ("2026-09-20", "55.00"))

TYPED_ONLY = "typed-only"


def label_of(ref: str) -> str:
    return f"Card {ref}"


def _qif(root: Path, ref: str, rows: Rows) -> Path:
    """A QIF file: it carries no ids, and lists each payment as a line of its own."""
    path = root / f"{ref}.qif"
    lines = "".join(f"D{day:02}/09/2026\nT-{figure}\nP{PAYEE}\n^\n" for day, figure in rows)
    path.write_text("!Type:CCard\n" + lines, encoding="utf-8")
    return path


def _truelayer(store: Store, ref: str, records: list[dict], cycle: int) -> None:
    """One response, landed and then resolved by the same door a replay uses."""
    artefact = truelayer.artefact_for(
        json.dumps({"results": records, "status": "Succeeded"}).encode(),
        account_id=ref,
        kind="booked",
        requested=f"c={cycle}",
        account_ref=ref,
    )
    store.land_artefact(artefact)
    reconcile_batch(
        store,
        parse_artefact_transactions("truelayer-booked", artefact.payload, ref, artefact.digest),
        digest=artefact.digest,
    )


def _record(tid: str, day: int, figure: str) -> dict:
    return {
        "transaction_id": tid,
        "normalised_provider_transaction_id": tid,
        "timestamp": f"2026-09-{day:02}T09:15:00Z",
        "amount": -float(figure),
        "currency": "GBP",
        "description": PAYEE,
    }


def state_balances(store: Store, ref: str, balances: Balances) -> None:
    for day, amount in balances:
        record_stated_anchor(store, ref, day, amount, today=TODAY)


def build_balance_world(
    root: Path, accounts: Iterable[str] | None = None, *, rebuild: bool = True
) -> Path:
    """Land the household at `root/store.sqlite3` and return its path.

    Every balance is stated AFTER every row has come in, because each import settles the flags
    the evidence already answers: with `rebuild=False` the flags stand, so the proof can be
    watched closing them, and with it the rebuild a deployment runs closes them.
    """
    wanted = set(accounts) if accounts is not None else {*EXPORTED, AGGREGATED}
    db = root / "store.sqlite3"
    with Store(db) as store:
        for ref in sorted(wanted):
            store.declare_account(
                AccountRecord(
                    ref=AccountRef(ref),
                    label=label_of(ref),
                    kind=BALANCE_ONLY_KIND if ref == TYPED_ONLY else "",
                )
            )
        for ref, (rows, _) in EXPORTED.items():
            if ref in wanted:
                import_file(store, _qif(root, ref, rows), account_id=ref)
        if AGGREGATED in wanted:
            _truelayer(
                store,
                AGGREGATED,
                [_record("ag-1", 3, "10.00"), _record("ag-2", 14, PAIR_FIGURE)],
                0,
            )
            _truelayer(
                store,
                AGGREGATED,
                [_record("ag-3", 14, PAIR_FIGURE), _record("ag-4", 20, "5.00")],
                1,
            )
            state_balances(store, AGGREGATED, AGGREGATED_BALANCES)
        for ref, (_, balances) in EXPORTED.items():
            if ref in wanted:
                state_balances(store, ref, balances)
        if rebuild:
            rebuild_from_raw(store)
    return db
