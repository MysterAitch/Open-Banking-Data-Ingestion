"""The listing rule over thousands of whole stores built through the statement door, with the
movement report laid on, and a "held as printed" oracle.

Each account is a card with one to three consecutive statements whose printed figures are
consistent by construction, so the TRUTH about it is known before the rule reads it: the store
holds exactly what the statements print, unless the account was TAMPERED with (a listed
transaction forgotten, held with another amount, or one forgotten and another enlarged by the
same sum so the errors cancel), carries an EXTRA transaction a feed holds that no statement
lists, or a typed balance that is true or 5.00 out. Decided before the first run:

  1  No account that adds up without the statements' own listings stops adding up with them,
     unless it was tampered with and a statement is named a fault.
  2  `through` is always the day of a tested known balance.
  3  Every account that NEWLY adds up is supported by the printed arithmetic: it was not
     tampered with and no typed balance is wrong. (An extra unlisted transaction may not stand
     inside a newly tested statement's days; where it does the account is counted as a
     disagreement and reported by kind.)
  4  A tampered account is never said to add up through a tampered statement's closing by the
     statement's own test.

The counts are printed by `-s`.
"""

from __future__ import annotations

import json
import os
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from listing_rule_reading import app_reading, movement_of, shown_balances_are_stated_or_named
from obdi.ingest.family_anchors import Families
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import AnchorRefused, record_stated_anchor
from obdi.verify.standing_data import ADDS_UP
from statement_span_world import Spend, feed, statement

D = date
NO_SPACES = Families({}, {}, {})
ACCOUNTS = int(os.environ.get("LISTING_RULE_ACCOUNTS", "300"))
CLOSINGS = [D(2026, 1, 10), D(2026, 2, 10), D(2026, 3, 10)]
OPENING = 10000


@dataclass
class Truth:
    ref: str
    tampered: bool = False
    extra: bool = False
    wrong_typed: bool = False
    #: (statement position, amount) of a purchase dated its closing day that nobody lists.
    same_day: tuple[int, int] | None = None
    merged: bool = False
    #: How many transactions the statements print between them.
    printed: int = 0
    statements: int = 0
    notes: list[str] = field(default_factory=list)


def forget(store: Store, ref: str, payee: str) -> None:
    store.connection.execute(
        "DELETE FROM transaction_sources WHERE entity_id IN (SELECT entity_id FROM transactions "
        "WHERE account_id = ? AND description = ?)",
        (ref, payee),
    )
    store.connection.execute(
        "DELETE FROM transactions WHERE account_id = ? AND description = ?", (ref, payee)
    )


def build_one(store: Store, root: Path, rng: random.Random, number: int) -> Truth:
    ref = f"rs-{number:04d}"
    truth = Truth(ref)
    count = rng.choice([1, 1, 2, 3])
    truth.statements = count
    owed = OPENING
    listed: list[list[Spend]] = []
    for position in range(count):
        closing = CLOSINGS[position]
        spends = [
            Spend(
                date(closing.year, closing.month, rng.randint(1, 9)),
                f"P{number}-{position}-{k}",
                rng.randint(100, 9000),
            )
            for k in range(rng.randint(1, 3))
        ]
        listed.append(spends)
        previous = CLOSINGS[position - 1] if position else D(2025, 12, 10)
        owed = statement(
            store, root, ref, closing, owed, spends, received=closing, previous_close=previous
        )
    truth.printed = sum(len(s) for s in listed)
    roll = rng.random()
    if roll < 0.25:
        position = rng.randrange(count)
        spends = listed[position]
        kind = rng.choice(["forget", "amount", "cancel"])
        if kind == "forget":
            forget(store, ref, spends[0].payee)
        elif kind == "amount":
            store.connection.execute(
                "UPDATE transactions SET amount_minor = amount_minor - 100 "
                "WHERE account_id = ? AND description = ?",
                (ref, spends[0].payee),
            )
        elif len(spends) >= 2:
            forget(store, ref, spends[0].payee)
            store.connection.execute(
                "UPDATE transactions SET amount_minor = amount_minor - ? "
                "WHERE account_id = ? AND description = ?",
                (spends[0].minor, ref, spends[1].payee),
            )
        else:
            forget(store, ref, spends[0].payee)
        truth.tampered = True
        truth.notes.append(kind)
    elif roll < 0.4:
        position = rng.randrange(count)
        day = date(CLOSINGS[position].year, CLOSINGS[position].month, 12 if position else 5)
        feed(store, ref, [Spend(day, f"X{number}", rng.randint(100, 900))], digest=f"x{number}")
        truth.extra = True
    elif roll < 0.55:
        # A purchase dated the closing day that no statement lists, and a typed balance for the
        # end of that day: the statement closed before it (the one hypothesis).
        position = rng.randrange(count)
        late = Spend(CLOSINGS[position], f"S{number}", rng.randint(100, 900))
        feed(store, ref, [late], digest=f"s{number}")
        truth.same_day = (position, late.minor)
    elif roll < 0.65:
        # A feed transaction with a listed purchase's date and amount under other words, which
        # the identity matcher may merge into the statement's line.
        position = rng.randrange(count)
        twin = listed[position][0]
        feed(
            store, ref, [Spend(twin.day, f"TWIN {number}", twin.minor)], digest=f"twin{number}"
        )
        truth.merged = True
    if rng.random() < 0.25 or truth.same_day is not None:
        position = rng.randrange(count) if truth.same_day is None else truth.same_day[0]
        closing = CLOSINGS[position]
        later = sum(s.minor for k in range(position + 1) for s in listed[k])
        true_owed = OPENING + later + (truth.same_day[1] if truth.same_day is not None else 0)
        off = 500 if truth.same_day is None and rng.random() < 0.3 else 0
        try:
            record_stated_anchor(
                store, ref, closing.isoformat(), f"{-(true_owed + off) / 100:.2f}",
                today=D(2026, 9, 1),
            )
        except AnchorRefused:
            # Every transaction of the account was forgotten, so the store has not heard of it.
            return truth
        truth.wrong_typed = bool(off)
    return truth


#: One store per seed. Reading an account costs a walk of the store, so a store of thousands is
#: quadratic; several stores of a few hundred give the thousands of accounts in linear time
#: (`LISTING_RULE_STORES=8` over the default 300 is 2,400 accounts).
@pytest.mark.parametrize("seed", range(int(os.environ.get("LISTING_RULE_STORES", "1"))))
def test_Rule_OverWholeStoresBuiltThroughTheStatementDoor_HoldsAgainstTheOracle(
    tmp_path, capsys, seed
):
    rng = random.Random(20260801 + seed)  # noqa: S311 - invented figures, not security material
    truths: list[Truth] = []
    with Store(tmp_path / "s.sqlite3") as store:
        for number in range(ACCOUNTS):
            truths.append(build_one(store, tmp_path, rng, number))
        store.connection.commit()
        movement_of(store)
        counts: Counter[str] = Counter()
        disagreements: list[tuple[str, str]] = []
        for truth in truths:
            _, before_verdict = app_reading(store, truth.ref, NO_SPACES, rule=False)
            now, verdict = app_reading(store, truth.ref, NO_SPACES)
            counts["accounts"] += 1
            if now.own.through is not None:
                assert now.own.through in now.own.tested, truth
            if before_verdict == ADDS_UP:
                counts["added up before"] += 1
                if verdict != ADDS_UP:
                    assert truth.tampered and now.own.statement_faults, (truth, now.own.state)
                    counts["lost to a named fault"] += 1
            if before_verdict != ADDS_UP and verdict == ADDS_UP:
                counts["newly adds up"] += 1
                bad = truth.tampered or truth.wrong_typed
                if bad:
                    disagreements.append((truth.ref, "/".join(truth.notes) or "typed"))
                elif truth.extra:
                    counts["newly adds up with an extra transaction beside"] += 1
            if truth.tampered and now.own.through is not None and not truth.extra:
                counts["tampered, adding up through something"] += 1
            if truth.same_day is not None:
                counts["same-day purchase"] += 1
                counts["same-day purchase explained"] += bool(now.own.closed_before)
            # The balances the page shows are the ones stated, for every day taken as closed
            # before some transactions and in every account the rule says adds up.
            checked = shown_balances_are_stated_or_named(store, truth.ref, NO_SPACES, now)
            counts["known balances checked against the position"] += checked
            if truth.merged:
                counts["twin merged: " + before_verdict + " -> " + verdict] += 1
                counts["twin merged: transactions held beyond the printed"] += max(
                    0, len(store.transactions_for_account(truth.ref)) - truth.printed
                )
        report = {"disagreements": disagreements, "counts": dict(sorted(counts.items()))}
        target = os.environ.get("LISTING_RULE_COUNTS")
        if target:
            Path(f"{target}.{seed}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        with capsys.disabled():
            print("\nstore-level randomised check:", dict(counts))
            print("disagreements:", len(disagreements))
    assert not disagreements
