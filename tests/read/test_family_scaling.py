"""The ledger of a main account with Spaces costs in proportion to what it holds.

The household is invented and large: a main account whose own rows come from
Starling's feed, Space payments the feed files under their Spaces while the
aggregator reports them under main (and the fold settles), transfer legs on
both sides, and a CSV export blind to the Spaces whose balance column moves
with every row it lists. Reading what that export's balances MEAN walked the
whole sightings table once per row, and deriving the opening summed every row
once per stated balance; on the deployed store that was minutes.

The answers are pinned apart from the cost: `derive_opening` is compared with
the plain definition of a balance at a date, and the export is read as the
whole account's, which is what its invented arithmetic is.
"""

from __future__ import annotations

import pathlib
import random
import time
from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.accounts import AccountBinding, AccountMap, AccountRecord, AccountRef
from obdi.ingest.family_anchors import families_of
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.space_attribution import fold_space_copies
from obdi.ingest.store import Store
from obdi.read.ledger import build_ledger
from obdi.verify.balance_anchors import (
    EXPORT,
    FAMILY,
    STATED,
    STATEMENT,
    Anchor,
    derive_opening,
    effective_opening,
)
from obdi.verify.balance_meaning import WHOLE

MAIN = "starling-personal"
FEED, AGGREGATOR = "starling", "truelayer"
OPENING = 1_000_000


def _row(account, source, ident, minor, when, description, internal=False):
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=ident,
        content_key=content_key(amount_minor=minor, value_date=when, description=description),
        tier=SourceTier.AUTHORITATIVE,
        status=TransactionStatus.BOOKED,
        is_internal_transfer=internal,
    )


def build_family(directory: pathlib.Path, *, days: int, spaces: int = 5):
    """A store of `days` days of activity, and the account map that names its Spaces."""
    rng = random.Random(days)  # noqa: S311 - invented amounts, not security material
    refs = [f"starling-space-{n}" for n in range(spaces)]
    account_map = AccountMap(
        [
            AccountBinding(MAIN, "starling", "acc-main"),
            AccountBinding(MAIN, "truelayer", "tl-main"),
            *(AccountBinding(ref, "starling", f"cat-{n}") for n, ref in enumerate(refs)),
        ],
        records=[
            AccountRecord(ref=AccountRef(ref), kind="starling-space", parent=AccountRef(MAIN))
            for ref in refs
        ],
    )
    store = Store(directory / "family.sqlite3")
    feed: list[Transaction] = []
    aggregator: list[Transaction] = []
    listed: list[tuple[date, int, str]] = []
    for n in range(days):
        when = date(2019, 1, 1) + timedelta(days=int(n * 2500 / days))
        for k in range(1 + (n % 3 == 0) + (n % 5 == 0)):
            minor = -rng.randrange(100, 9000)
            description = f"Shop {n}-{k}"
            feed.append(_row(MAIN, FEED, f"f{n}-{k}", minor, when, description))
            listed.append((when, minor, description))
        if n % 2 == 0:
            minor = -rng.randrange(100, 9000)
            description = f"Bill {n}"
            feed.append(_row(refs[n % spaces], FEED, f"s{n}", minor, when, description))
            aggregator.append(_row(MAIN, AGGREGATOR, f"a{n}", minor, when, description))
            listed.append((when, minor, description))
        if n % 7 == 0:
            minor = rng.randrange(500, 20000)
            feed.append(_row(MAIN, FEED, f"to{n}", -minor, when, f"To space {n}", True))
            feed.append(_row(refs[n % spaces], FEED, f"in{n}", minor, when, f"From main {n}", True))
    for start in range(0, len(feed), 400):
        reconcile_batch(store, feed[start : start + 400], digest=f"feed-{start}")
    for start in range(0, len(aggregator), 400):
        reconcile_batch(store, aggregator[start : start + 400], digest=f"aggregator-{start}")
    fold_space_copies(store, account_map)
    balance = OPENING
    lines = ["Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Notes"]
    for when, minor, description in listed:
        balance += minor
        lines.append(
            f"{when:%d/%m/%Y},{description},{description},FASTER PAYMENT,"
            f"{minor / 100:.2f},{balance / 100:.2f},"
        )
    export = directory / "export.csv"
    export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    import_file(store, export, account_id=MAIN, account_map=account_map)
    return store, account_map


class TestTheExportBlindToSpacesIsReadAsTheWholeAccount:
    def test_Export_WhenItsBalanceMovesWithEveryListedRow_IsReadAsTheWholeAccount(self, tmp_path):
        store, account_map = build_family(tmp_path, days=120)
        with store:
            opening = effective_opening(store, MAIN, families=families_of(store, account_map))

        (meaning,) = opening.meanings
        assert (meaning.source, meaning.verdict) == ("starling-csv", WHOLE)
        assert meaning.steps > 0
        assert meaning.whole == meaning.steps
        assert meaning.main < meaning.steps

    def test_Walk_WhenTheExportIsTheWholeAccounts_EveryAnchorAgreesAndTheOpeningIsKnown(
        self, tmp_path
    ):
        store, account_map = build_family(tmp_path, days=120)
        with store:
            opening = effective_opening(store, MAIN, families=families_of(store, account_map))

        assert opening.readings
        assert not opening.differing
        assert {reading.anchor.basis for reading in opening.readings} == {FAMILY}


class TestDerivingTheOpeningFromManyAnchors:
    """`derive_opening` against the plain definition: an anchor's `through` is the
    sum of every row counted toward its basis that falls on or before its day."""

    @staticmethod
    def rows() -> list[Transaction]:
        statuses = [
            TransactionStatus.BOOKED,
            TransactionStatus.PENDING,
            TransactionStatus.VOID,
            TransactionStatus.FOLDED,
            TransactionStatus.BOOKED,
        ]
        return [
            replace(
                _row(
                    "acc",
                    "src",
                    f"r{n}",
                    (n + 1) * 100 * (-1) ** n,
                    date(2026, 1, 1 + n % 28),
                    f"R{n}",
                ),
                status=statuses[n % len(statuses)],
            )
            for n in range(60)
        ]

    @staticmethod
    def reference(anchor: Anchor, rows: list[Transaction], placed: dict[str, date]) -> int:
        total = 0
        for row in rows:
            if row.status.is_history:
                continue
            if anchor.basis != STATED and row.status is TransactionStatus.PENDING:
                continue
            day = (
                placed.get(row.entity_id, row.value_date)
                if anchor.basis == STATEMENT
                else row.value_date
            )
            if day <= anchor.day:
                total += row.amount_minor
        return total

    @pytest.mark.parametrize("use_placed", [False, True], ids=["by-stored-date", "by-statement"])
    def test_Anchors_WhenOfEveryBasisAndRowsOfEveryStatus_AreJudgedByThePlainDefinition(
        self, use_placed
    ):
        rows = self.rows()
        # A statement that files every third row a day later than its stored date.
        placed = (
            {row.entity_id: row.value_date + timedelta(days=1) for row in rows[::3]}
            if use_placed
            else {}
        )
        anchors = [
            Anchor(date(2026, 1, day), 50_000 + day * 7, basis)
            for day in (3, 9, 14, 20, 27)
            for basis in (STATED, STATEMENT, EXPORT)
        ]

        opening = derive_opening("acc", anchors, rows, placed=placed)

        first = opening.readings[0].anchor
        assert opening.opening_minor == first.balance_minor - self.reference(first, rows, placed)
        for reading in opening.readings[1:]:
            expected = opening.opening_minor + self.reference(reading.anchor, rows, placed)
            assert reading.expected_minor == expected
            assert reading.difference_minor == reading.anchor.balance_minor - expected


class TestTheLedgerOfAFamilyDoesNotSlowDownFasterThanItsSize:
    def test_LedgerView_WhenTheAccountHoldsFourTimesAsMuch_CostsAboutFourTimesAsMuch(
        self, tmp_path
    ):
        """Stated as a ratio, over the minimum of three runs, as in test_matching.

        Four times the rows is four times the work when the walk is linear and
        sixteen times when each row or stated balance re-reads the account. The
        bound sits between the two with room for a noisy machine.
        """

        def timed(directory: pathlib.Path, days: int) -> float:
            directory.mkdir()
            store, account_map = build_family(directory, days=days)
            with store:
                found = families_of(store, account_map)
                # The export's bytes are parsed once per process, not per request.
                build_ledger(store, MAIN, None, bound=False, families=found)
                runs = []
                for _ in range(3):
                    start = time.perf_counter()
                    build_ledger(store, MAIN, None, bound=False, families=found)
                    runs.append(time.perf_counter() - start)
                return min(runs)

        small = timed(tmp_path / "small", 300)
        large = timed(tmp_path / "large", 1200)

        assert large < small * 9, (
            f"four times the days made the ledger {large / small:.1f}x slower - "
            "the cost is scaling faster than the account"
        )
