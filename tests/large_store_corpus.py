"""An invented store of the shape that is slow, built through the application's own doors.

THE SHAPE, taken from the cost model of the pages and not from any real figure: one main
account of about 5,300 rows over six years, fed by four sources (the bank's own feed for the
last four and a half years, a CSV export for all six, an aggregator for the last two, and
monthly PDF statements for the last year that print the WHOLE family's balance and cannot see
the Spaces); five Spaces with a few hundred payments each, round-ups into one of them and
transfers between each and the main account; known balances stated on 570 days of the main
account; and ten other accounts (cards with monthly statements, savings accounts whose
statements overlap, one with a single known balance, one with none).

EVERY FIGURE IS INVENTED. Rows are written by `reconcile_batch`, the door a pull and an
import both use, the PDF statements and the other accounts by `import_file`, so the
sightings, folds, pairings and review flags are the ones the application writes.

KNOWN ANSWERS, recorded in `LargeStore`: each count below is decided by the construction and
checked by `tests/test_large_store_corpus.py`, so a builder that drifts from the shape it
claims disagrees with its own record rather than quietly measuring something smaller.

Run as a script it builds a store, an account map, and the empty directories a server wants:

    python tests/large_store_corpus.py DIR

after which `OBDI_DB_PATH=DIR/store.sqlite3 OBDI_ACCOUNT_MAP=DIR/accounts.json obdi serve`
shows the pages over it (the isolation `scripts/dev_corpus_ui.py` describes still applies:
point every path at DIR).
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

from obdi.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.balance_anchors import record_stated_anchor
from obdi.identity import content_key
from obdi.ingest import import_file, pair_transfers_across_store, reconcile_batch
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.space_attribution import fold_space_copies
from obdi.store import Store
from obdi.synthetic import build_world, write_corpus

MAIN = "starling-personal"
SPACES = (
    "starling-space-bills",
    "starling-space-holiday",
    "starling-space-rainy-day",
    "starling-space-car",
    "starling-space-gifts",
)
ROUND_UP_SPACE = "starling-space-rainy-day"
FEED, AGGREGATOR, EXPORT = "starling", "truelayer", "starling-csv"

#: The last day the store holds, and how far back each source reaches from it.
END = date(2026, 9, 30)
START = date(2020, 10, 1)
FEED_FROM = date(2022, 4, 1)
AGGREGATOR_FROM = date(2024, 10, 1)
SPACES_FROM = date(2023, 1, 1)
STATEMENTS_FROM = date(2025, 10, 1)

#: Money the whole account held before its first row.
OPENING_MINOR = 80000

#: How many days of the main account carry a stated balance.
STATED_DAYS = 570

MERCHANTS = (
    "Tesco", "Sainsburys", "Lidl", "Costa", "Pret", "Greggs", "Shell", "Amazon", "Netflix",
    "Spotify", "TfL", "Trainline", "Boots", "Argos", "Waitrose", "Co-op", "Aldi", "Deliveroo",
    "Uber", "Octopus Energy", "Thames Water", "Vodafone", "Gym", "Pharmacy", "Bakery",
)


@dataclass(frozen=True)
class LargeStore:
    """Where the store is, and what its construction decided."""

    directory: Path
    path: Path
    account_map: AccountMap
    main_rows_distinct: int
    space_rows_distinct: int
    statements: int
    stated_days: int
    other_accounts: tuple[str, ...]

    def write_account_map(self, directory: Path | None = None) -> Path:
        """The map as the file the application reads, beside the store unless told where."""
        target = (directory or self.directory) / "accounts.json"
        target.write_text(json.dumps(_map_document()), encoding="utf-8")
        return target


def _map_document() -> dict[str, object]:
    return {
        "bindings": [
            {"canonical_id": MAIN, "source": "starling", "provider_account_id": "acc-main"},
            {"canonical_id": MAIN, "source": "truelayer", "provider_account_id": "tl-main"},
            *(
                {
                    "canonical_id": space,
                    "source": "starling",
                    "provider_account_id": f"cat-{space.rsplit('-', 1)[-1]}",
                }
                for space in SPACES
            ),
        ],
        "accounts": [
            {"id": space, "kind": "starling-space", "parent": MAIN} for space in SPACES
        ],
    }


def household_map() -> AccountMap:
    bindings = [
        AccountBinding(MAIN, "starling", "acc-main"),
        AccountBinding(MAIN, "truelayer", "tl-main"),
        *(
            AccountBinding(space, "starling", f"cat-{space.rsplit('-', 1)[-1]}")
            for space in SPACES
        ),
    ]
    records = [
        AccountRecord(ref=AccountRef(space), kind="starling-space", parent=AccountRef(MAIN))
        for space in SPACES
    ]
    return AccountMap(bindings, records=records)


def _row(
    account: str,
    source: str,
    source_id: str | None,
    minor: int,
    day: date,
    description: str,
    *,
    internal: bool = False,
    raw: dict[str, str] | None = None,
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=day,
        booking_date=day,
        description=description,
        source=source,
        source_id=source_id,
        content_key=content_key(amount_minor=minor, value_date=day, description=description),
        tier=SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC,
        status=TransactionStatus.BOOKED,
        is_internal_transfer=internal,
        raw=raw or {},
    )


@dataclass(frozen=True)
class _Payment:
    account: str
    day: date
    minor: int
    description: str
    key: str


def _payments(rng: random.Random) -> list[_Payment]:
    """Every external payment, one per real-world payment, with the pot it left."""
    made: list[_Payment] = []
    seen: set[tuple[date, int]] = set()
    counter = 0

    def add(account: str, day: date, minor: int, merchant: str) -> None:
        nonlocal counter
        while (day, minor) in seen:
            minor -= 1
        seen.add((day, minor))
        counter += 1
        made.append(_Payment(account, day, minor, f"{merchant} {counter}", f"p{counter}"))

    day = START
    while day <= END:
        # About 2.2 ordinary payments a day from the main account, and a salary monthly.
        for _ in range(rng.choice((0, 1, 2, 2, 3, 3, 4))):
            add(MAIN, day, -rng.randint(150, 9000), rng.choice(MERCHANTS))
        if day.day == 28:
            add(MAIN, day, rng.randint(180000, 260000), "Employer")
        day += timedelta(days=1)
    for space in SPACES:
        day = SPACES_FROM
        while day <= END:
            # Roughly 300 payments over the 45 months each Space is open.
            if rng.random() < 0.22 and rng.random() < 0.9:
                add(space, day, -rng.randint(300, 15000), rng.choice(MERCHANTS))
            day += timedelta(days=3 if rng.random() < 0.5 else 5)
    return made


def _legs(rng: random.Random) -> list[tuple[date, str, int, bool]]:
    """Transfers between main and a Space (both legs) and weekly round-ups."""
    moves: list[tuple[date, str, int, bool]] = []
    for space in SPACES:
        day = SPACES_FROM + timedelta(days=rng.randint(0, 20))
        while day <= END:
            moves.append((day, space, rng.randint(2000, 40000), False))
            day += timedelta(days=rng.randint(20, 45))
    day = SPACES_FROM
    while day <= END:
        moves.append((day, ROUND_UP_SPACE, rng.randint(40, 600), True))
        day += timedelta(days=7)
    return moves


def _feed_raw(day: date, uid: str, minor: int, name: str) -> dict[str, object]:
    """A feed item as the bank states it: the coded kinds, the instants, the nested amounts."""
    return {
        "feedItemUid": uid,
        "categoryUid": "cat-main",
        "amount": {"currency": "GBP", "minorUnits": abs(minor)},
        "sourceAmount": {"currency": "GBP", "minorUnits": abs(minor)},
        "direction": "IN" if minor > 0 else "OUT",
        "updatedAt": f"{day.isoformat()}T10:15:09Z",
        "transactionTime": f"{day.isoformat()}T10:15:00Z",
        "settlementTime": f"{day.isoformat()}T10:15:03Z",
        "source": "FASTER_PAYMENTS_IN" if minor > 0 else "MASTER_CARD",
        "sourceSubType": "CONTACTLESS",
        "status": "SETTLED",
        "transactingApplicationUserUid": "user-1",
        "counterPartyType": "MERCHANT",
        "counterPartyUid": f"merchant-{abs(hash(name)) % 997}",
        "counterPartyName": name,
        "counterPartySubEntityUid": "sub-1",
        "reference": name.upper(),
        "country": "GB",
        "spendingCategory": "GROCERIES",
        "hasAttachment": False,
        "hasReceipt": False,
    }


def _aggregator_raw(day: date, uid: str, minor: int, name: str, feed_uid: str) -> dict[str, object]:
    """An aggregator item. Its provider id IS the bank feed's uid for the same payment
    (`payment_links`), and an id naming a feed item that is a different payment refuses the
    join, so the id given here is always the true one."""
    return {
        "transaction_id": uid,
        "timestamp": f"{day.isoformat()}T00:00:00",
        "description": name.upper(),
        "transaction_type": "CREDIT" if minor > 0 else "DEBIT",
        "transaction_category": "PURCHASE",
        "transaction_classification": ["Shopping", "General"],
        "amount": minor / 100,
        "currency": "GBP",
        "meta": {"provider_transaction_category": "PURCHASE", "provider_id": feed_uid},
    }


def _pulls(
    rows: list[Transaction], first: date, window_days: int, step_days: int, label: str
) -> list[tuple[str, list[Transaction]]]:
    """What a periodic pull lands: each pull re-reads the last `window_days`, so a row is
    sighted by every pull whose window holds it, each under an artefact of its own."""
    pulls: list[tuple[str, list[Transaction]]] = []
    when = first + timedelta(days=window_days)
    while when - timedelta(days=step_days) <= END:
        low = when - timedelta(days=window_days)
        held = [r for r in rows if r.value_date is not None and low < r.value_date <= when]
        if held:
            pulls.append((f"{label}-{when.isoformat()}", held))
        when += timedelta(days=step_days)
    return pulls


def _pdf_lines(
    family_rows: list[_Payment], opening: int, first: date, last: date
) -> tuple[list[str], int]:
    """One month of the whole account's statement, in the row language the fixture draws."""
    rows = sorted(family_rows, key=lambda p: (p.day, p.key))
    running = opening
    income = outgoing = 0
    body: list[str] = []
    for payment in rows:
        running += payment.minor
        kind = "DIRECT CREDIT" if payment.minor > 0 else "FASTER PAYMENT"
        amount = f"{abs(payment.minor) // 100}.{abs(payment.minor) % 100:02}"
        figure = f"{running // 100}.{running % 100:02}" if running >= 0 else _negative(running)
        if payment.minor > 0:
            income += payment.minor
            body.append(
                f"ROW|{payment.day:%d/%m/%Y}|{kind}|{payment.description}|{amount}||{figure}"
            )
        else:
            outgoing -= payment.minor
            body.append(
                f"ROW|{payment.day:%d/%m/%Y}|{kind}|{payment.description}||{amount}|{figure}"
            )
    head = (
        f"SUMMARY|{first:%d/%m/%Y} - {last:%d/%m/%Y}|{_pounds(opening)}|{_pounds(income)}|"
        f"{_pounds(outgoing)}|{_pounds(running)}"
    )
    return [head, "HEAD", f"OPENING|{_pounds(opening)}", *body, "END"], running


def _pounds(minor: int) -> str:
    return f"{minor // 100}.{minor % 100:02}" if minor >= 0 else _negative(minor)


def _negative(minor: int) -> str:
    return f"-{abs(minor) // 100}.{abs(minor) % 100:02}"


def build_large_store(directory: Path | None = None, *, seed: int = 20261005) -> LargeStore:
    """Build the store in `directory` (a new temporary one when none is given)."""
    from test_starling_statement import build_starling_pdf

    directory = directory or Path(tempfile.mkdtemp(prefix="obdi-large-store-"))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "store.sqlite3"
    rng = random.Random(seed)  # noqa: S311 - an invented store, nothing here is a secret
    account_map = household_map()
    payments = _payments(rng)
    legs = _legs(rng)

    with Store(path) as store:
        # The feed: each payment under the pot it left, with the transfer legs between them.
        feed_rows: list[Transaction] = []
        for payment in payments:
            if payment.day >= FEED_FROM:
                feed_rows.append(
                    _row(
                        payment.account, FEED, f"uid-{payment.key}", payment.minor,
                        payment.day, payment.description,
                        raw=_feed_raw(
                            payment.day, payment.key, payment.minor, payment.description
                        ),
                    )
                )
        for number, (day, space, minor, round_up) in enumerate(legs):
            if day < FEED_FROM:
                continue
            name = "Round-up" if round_up else "To a Space"
            for account, signed, direction in ((MAIN, -minor, "out"), (space, minor, "in")):
                feed_rows.append(
                    replace(
                        _row(
                            account, FEED, f"leg-{number}-{direction}", signed, day, name,
                            internal=True,
                        ),
                        raw={
                            "counterPartyType": "CATEGORY",
                            "counterPartyUid": "cat-"
                            + (space if account == MAIN else MAIN).rsplit("-", 1)[-1],
                            "counterPartyName": "Space",
                            "transactionTime": f"{day.isoformat()}T09:00:00Z",
                        },
                    )
                )
        # The export and the aggregator see every payment as the MAIN account's, Space
        # payments included, and neither sees a transfer between a Space and main.
        export_rows = [
            _row(MAIN, EXPORT, None, p.minor, p.day, p.description) for p in payments
        ]
        aggregator_rows = [
            _row(
                MAIN, AGGREGATOR, f"tl-{p.key}", p.minor, p.day, p.description,
                raw=_aggregator_raw(
                    p.day, f"tl-{p.key}", p.minor, p.description, f"uid-{p.key}"
                ),
            )
            for p in payments
            if p.day >= AGGREGATOR_FROM
        ]
        # The feed is pulled weekly over the last three weeks, the aggregator weekly over the
        # last eight, and the export is delivered whole and again for its last year, so a
        # payment is sighted by many artefacts, which is what the sighting tables grow with.
        landings = [
            *_pulls(feed_rows, FEED_FROM, 21, 7, "feed"),
            *_pulls(aggregator_rows, AGGREGATOR_FROM, 56, 7, "aggregator"),
            ("export-whole", export_rows),
            (
                "export-last-year",
                [r for r in export_rows if r.value_date >= END - timedelta(days=365)],
            ),
        ]
        for digest, rows in landings:
            reconcile_batch(store, rows, digest=f"large-{digest}")
        fold_space_copies(store, account_map)
        pair_transfers_across_store(store, account_map)

        # The PDF statements: the whole account's balance, a month at a time.
        statements = 0
        family = list(payments)
        family_before = OPENING_MINOR + sum(p.minor for p in family if p.day < STATEMENTS_FROM)
        month = STATEMENTS_FROM
        opening = family_before
        while month <= END:
            following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            last = min(following - timedelta(days=1), END)
            within = [p for p in family if month <= p.day <= last]
            lines, opening = _pdf_lines(within, opening, month, last)
            target = directory / f"statement-{month:%Y-%m}.pdf"
            target.write_bytes(build_starling_pdf(lines))
            import_file(store, target, account_id=MAIN, account_map=account_map)
            statements += 1
            month = following
        fold_space_copies(store, account_map)

        # Known balances on days of the main account's own rows.
        by_day: dict[date, int] = defaultdict(int)
        for payment in payments:
            if payment.account == MAIN:
                by_day[payment.day] += payment.minor
        for day, _space, minor, _round_up in legs:
            by_day[day] -= minor
        running = OPENING_MINOR
        cumulative: dict[date, int] = {}
        for day in sorted(by_day):
            running += by_day[day]
            cumulative[day] = running
        chosen = sorted(rng.sample([d for d in cumulative if d >= date(2023, 3, 1)], STATED_DAYS))
        for day in chosen:
            record_stated_anchor(store, MAIN, day.isoformat(), _pounds(cumulative[day]))

        others = _other_accounts(store, directory, seed)

    large = LargeStore(
        directory=directory,
        path=path,
        account_map=account_map,
        main_rows_distinct=sum(1 for p in payments if p.account == MAIN) + len(legs) * 1,
        space_rows_distinct=sum(1 for p in payments if p.account != MAIN) + len(legs),
        statements=statements,
        stated_days=len(chosen),
        other_accounts=others,
    )
    large.write_account_map()
    return large


def _other_accounts(store: Store, directory: Path, seed: int) -> tuple[str, ...]:
    """Ten more accounts through `import_file`: cards by monthly statement, savings by
    overlapping statements, one with a single known balance and one with none."""
    made: list[str] = []
    for number in range(1, 4):
        world = build_world(seed + number, months=12)
        corpus = directory / f"corpus-{number}"
        manifest = write_corpus(world, corpus)
        card, current, savings = f"card-{number}", f"current-{number}", f"savings-{number}"
        for statement in manifest["statements"]:  # type: ignore[attr-defined]
            import_file(store, corpus / statement["name"], account_id=card)
        import_file(store, corpus / "synthetic-current.csv", account_id=current)
        import_file(store, corpus / "synthetic-savings.csv", account_id=savings)
        for delivery in manifest["deliveries"]:  # type: ignore[attr-defined]
            if delivery["fault"] == "" and delivery["belongs_to"] == delivery["deliver_as"]:
                import_file(
                    store,
                    corpus / delivery["name"],
                    account_id=savings if "savings" in delivery["belongs_to"] else current,
                )
        made += [card, current, savings]
    # A tenth account that has no known balance at all (the others each have statements, a
    # running balance, or one stated figure).
    import_file(store, directory / "corpus-1" / "synthetic-savings.csv", account_id="savings-4")
    made.append("savings-4")
    record_stated_anchor(store, "card-1", "2026-06-30", "120.00")
    record_stated_anchor(store, "current-2", "2026-06-30", "310.50")
    return tuple(made)


def cached_large_store(*, waited: float = 1500.0) -> LargeStore:
    """The large store, built once per machine and shared by every test process that asks.

    Building takes minutes, and a suite runs across processes, so the first to ask builds into
    a directory named for the builder's own text and the schema, and the others wait for its
    `ready` file. Read-only: a test that writes takes a copy (`copy_of`).
    """
    import hashlib
    import time

    from obdi.store import SCHEMA_VERSION

    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    root = Path(tempfile.gettempdir()) / f"obdi-large-store-{digest}-s{SCHEMA_VERSION}"
    ready = root / "ready"
    claim = Path(str(root) + ".building")

    def held() -> LargeStore:
        facts = json.loads(ready.read_text(encoding="utf-8"))
        facts["other_accounts"] = tuple(facts["other_accounts"])
        return LargeStore(
            directory=root, path=root / "store.sqlite3", account_map=household_map(), **facts
        )

    if ready.exists():
        return held()
    try:
        claim.mkdir()
    except FileExistsError:
        deadline = time.monotonic() + waited
        while not ready.exists():
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"{ready} did not appear: remove {claim} if its builder died"
                ) from None
            time.sleep(2)
        return held()
    try:
        built = build_large_store(root)
        facts = {
            "main_rows_distinct": built.main_rows_distinct,
            "space_rows_distinct": built.space_rows_distinct,
            "statements": built.statements,
            "stated_days": built.stated_days,
            "other_accounts": list(built.other_accounts),
        }
        ready.write_text(json.dumps(facts), encoding="utf-8")
    finally:
        claim.rmdir()
    return held()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    built = build_large_store(target)
    print(built.directory)
