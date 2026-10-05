"""A randomised comparison of the agreement rule with and without the listing rule, over
thousands of constructed accounts, each checked against a plain re-statement of the rule.

An earlier attempt at this area put `through` on days where nothing had been tested, in 1,762 of
19,999 random accounts. The properties here are the ones that would have caught it, decided
before the first run:

  1  No account that adds up without the listing checks stops adding up with them, unless a
     statement is a fault (R3). Counted over every generated account.
  2  `through` is always the day of a known balance that was tested, and no later than the
     first hold; never a day nothing tested.
  3  Every honest closed-before claim (the figures bear it out) is applied, and every dishonest
     one (a difference the figures do not show) changes nothing at all.
  4  The result equals a naive restatement of the rule, written below from its text and sharing
     no code with `agreement`.
  5  Where there are no checks, or the checks are all "cannot say", the answer is today's, exactly.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

from obdi.agreement import (
    DEFINES,
    HELD_STATEMENT,
    MET,
    UNMET,
    Agreement,
    Known,
    derive_agreement,
)
from obdi.balance_anchors import BANK, STATED, STATEMENT
from obdi.statement_checks import (
    NOT_HELD,
    ClosedBefore,
    StatementCheck,
    StatementChecks,
)

START = date(2026, 1, 1)
ACCOUNTS = 6000


def day(n: int) -> date:
    return START + timedelta(days=n)


def generate(rng: random.Random) -> tuple[list[Known], list[StatementCheck]]:
    """Known balances in the shape `known_of_opening` gives them: the earliest defines the opening
    (distance 0) and every other has a distance from it, nil where the transactions reproduce it."""
    days = sorted(rng.sample(range(0, 40), rng.randint(1, 6)))
    known: list[Known] = []
    checks: list[StatementCheck] = []
    figure_base = 1000
    for position, n in enumerate(days):
        for _ in range(rng.choice([1, 1, 1, 2])):
            basis = rng.choice([STATEMENT, STATEMENT, BANK, STATED])
            distance = 0 if rng.random() < 0.7 else rng.choice([-500, -7, 3, 500])
            if position == 0 and not known:
                distance = 0
            defines = position == 0 and not known
            figure = figure_base + n * 10 + (0 if rng.random() < 0.6 else rng.choice([-500, 500]))
            known.append(
                Known(
                    day(n),
                    f"{basis}-{len(known)}",
                    DEFINES if defines else MET if distance == 0 else UNMET,
                    figure,
                    instant=rng.random() < 0.08,
                    premised=False,
                    distance=distance,
                    basis=basis,
                )
            )
    for k in known:
        if k.basis != STATEMENT or k.instant:
            continue
        roll = rng.random()
        if roll < 0.55:
            checks.append(
                StatementCheck(
                    k.day, k.figure, rng.randint(0, 40), rng.choice([True, True, None]),
                    rng.random() < 0.7, rng.choice([0, 1, None]),
                )
            )
        elif roll < 0.62:
            checks.append(
                StatementCheck(k.day, k.figure, 3, False, False, 0, fault=NOT_HELD)
            )
        elif roll < 0.9:
            others = [o for o in known if o.day == k.day and o.figure != k.figure and not o.instant]
            honest = bool(others) and rng.random() < 0.6
            difference = (
                others[0].figure - k.figure if honest else rng.choice([-500, 500, 17])
            )
            counted = rng.choice([difference, 0])
            checks.append(
                StatementCheck(
                    k.day, k.figure, rng.randint(1, 9), True, rng.random() < 0.8, 1,
                    closed_before=ClosedBefore(
                        k.day, 1, rng.random() < 0.3, difference, counted
                    ),
                )
            )
    return known, checks


def reference(known: list[Known], checks: list[StatementCheck]) -> dict[str, object]:
    """The rule, restated naively from its text, over a plain list."""
    view = [replace(k) for k in known]
    claimed: set[int] = set()
    for check in checks:
        mine = [
            i
            for i, k in enumerate(view)
            if k.basis == STATEMENT and not k.instant and k.day == check.day
            and k.figure == check.figure
        ]
        claim = check.closed_before
        if claim is None or not mine:
            continue
        others = [
            k for k in view if not k.instant and k.day == claim.day and k.figure != check.figure
        ]
        if not others or any(k.figure - check.figure != claim.difference_minor for k in others):
            continue
        shift = 0 if claim.next_day else claim.counted_minor
        defining = view[mine[0]].verdict == DEFINES
        for i, k in enumerate(view):
            if k.verdict == DEFINES or k.distance is None:
                continue
            if (defining and i not in mine) or (not defining and i in mine):
                distance = k.distance + (-shift if defining else shift)
                view[i] = replace(k, distance=distance, verdict=MET if distance == 0 else UNMET)
        claimed.update(mine)
    tested_by_listing = set()
    for check in checks:
        if check.adds_up is True and check.days_tested:
            for i, k in enumerate(view):
                if (
                    k.basis == STATEMENT and not k.instant and k.day == check.day
                    and k.figure == check.figure and k.verdict in (DEFINES, MET)
                ):
                    tested_by_listing.add(i)
    all_days = sorted({k.day for k in view})
    earliest = all_days[0]
    fault = min((c.day for c in checks if c.fault), default=None)

    def figures_at(d: date) -> set[int]:
        return {
            k.figure for i, k in enumerate(view)
            if k.day == d and not k.instant and i not in claimed
        }

    conflict_days = [d for d in all_days if len(figures_at(d)) > 1]
    unmet_days = [
        d for d in all_days
        if d not in conflict_days and any(k.verdict == UNMET for k in view if k.day == d)
    ]
    tested_days = []
    for d in all_days:
        if d in conflict_days:
            continue
        here = [(i, k) for i, k in enumerate(view) if k.day == d]
        by_listing = any(i in tested_by_listing for i, _ in here)
        by_chain = d != earliest and any(k.verdict == MET for _, k in here)
        if by_listing or by_chain:
            tested_days.append(d)
    holds = [*conflict_days[:1], *unmet_days[:1], *([fault] if fault else [])]
    blocked = min(holds, default=None)
    through = None
    for d in all_days:
        if (blocked is None or d < blocked) and tested_days and d >= tested_days[0]:
            through = d
    return {"through": through, "tested": tuple(tested_days), "blocked": blocked}


def run(known: list[Known], checks: list[StatementCheck]) -> Agreement:
    return derive_agreement(known, [], checks=StatementChecks(tuple(checks)))


def test_Rule_OverRandomAccounts_AgreesWithAPlainRestatementAndNeverTestsNothing():
    rng = random.Random(20260713)  # noqa: S311 - invented figures, not security material
    counters = {
        "accounts": 0, "adds_up_before": 0, "kept": 0, "lost_to_fault": 0, "gained": 0,
        "claims_applied": 0, "claims_ignored": 0, "through_untested": 0, "mismatch": 0,
    }
    for _ in range(ACCOUNTS):
        known, checks = generate(rng)
        counters["accounts"] += 1
        today = derive_agreement(known, [])
        now = run(known, checks)
        expected = reference(known, checks)

        counters["adds_up_before"] += today.held is None and today.through is not None
        if today.held is None and today.through is not None:
            if now.held is None and now.through is not None:
                counters["kept"] += 1
            else:
                assert now.state == HELD_STATEMENT, (known, checks)
                counters["lost_to_fault"] += 1
        elif now.held is None and now.through is not None:
            counters["gained"] += 1
        if now.through is not None:
            assert now.through in now.tested, (known, checks)
            counters["through_untested"] += now.through not in now.tested
        if now.through != expected["through"] or now.tested != expected["tested"]:
            counters["mismatch"] += 1
        assert (now.through, now.tested) == (expected["through"], expected["tested"]), (
            known, checks, now, expected,
        )
        counters["claims_applied"] += len(now.closed_before)
        counters["claims_ignored"] += sum(1 for c in checks if c.closed_before) - len(
            now.closed_before
        )
    print(counters)
    assert counters["through_untested"] == 0
    assert counters["mismatch"] == 0
    assert counters["adds_up_before"] == counters["kept"] + counters["lost_to_fault"]


def test_Rule_WhenEveryCheckCannotSay_IsExactlyToday():
    rng = random.Random(7)  # noqa: S311 - invented figures, not security material
    for _ in range(2000):
        known, _ = generate(rng)
        silent = [
            StatementCheck(k.day, k.figure, 0, None, False, None)
            for k in known
            if k.basis == STATEMENT
        ]
        assert run(known, silent) == derive_agreement(known, [])


def test_Rule_WhenAClaimIsNotBornOutByTheFigures_ChangesNothingAtAll():
    rng = random.Random(11)  # noqa: S311 - invented figures, not security material
    for _ in range(2000):
        known, _ = generate(rng)
        for k in known:
            if k.basis != STATEMENT or k.instant:
                continue
            others = [o for o in known if o.day == k.day and not o.instant and o.figure != k.figure]
            if not others:
                continue
            wrong = max(abs(o.figure - k.figure) for o in others) + 1
            claim = ClosedBefore(k.day, 1, False, wrong, wrong)
            lone = [StatementCheck(k.day, k.figure, 1, None, False, None, closed_before=claim)]
            assert run(known, lone) == derive_agreement(known, [])
