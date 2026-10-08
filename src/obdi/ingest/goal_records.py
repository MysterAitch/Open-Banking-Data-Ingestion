"""What the store returns and raises where the owner's goals are concerned.

A goal is something the owner wants to happen to the money in an account, as opposed to a
commitment, which is a payment he has already agreed to (`docs/design/2026-10-commitments/plan.md`
section 3, "Goal"). There are three kinds, in his words ("credit cards etc. to repay and rainy day
funds to build and planned renovation activities/holidays to save for"):

  CLEAR  a debt to clear: an account whose owed balance should reach nil.
  BUILD  a fund to build: an account whose held balance should reach an amount.
  SAVE   a saving for a thing: an amount, by a date, drawn from the money an account holds.

The record is a declaration and is kept across the rebuild from raw; the progress against it is
derived from the account's balance and is not kept (`analysis.goals`). The store keeps the rows and
hands them back as these records, below the analysis, as `commitment_records` does for commitments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..core.errors import DataError

CLEAR = "clear"
BUILD = "build"
SAVE = "save"

#: The kinds of goal, in the order a page offers them.
GOAL_KINDS = (CLEAR, BUILD, SAVE)


class GoalRefused(DataError):
    """A change to the goals that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class Goal:
    """One goal not removed.

    `account` is the canonical name of the account it is about. `target_minor` is the amount to
    reach (a CLEAR goal's is nil: the debt is gone). `target_date` is the day by which, or None
    where the owner set none; a SAVE goal always has one. `declared_on` is the day the goal was
    declared, which is where its straight line starts, and `start_minor` the balance it started
    from, as a magnitude (the amount owed for CLEAR, the amount held for BUILD), or None where
    that balance was not known then (so the line cannot be drawn, and the page says so). `basis`
    says where the goal came from, in words."""

    id: int
    name: str
    kind: str
    account: str
    target_minor: int
    target_date: date | None
    declared_on: date
    start_minor: int | None
    created_at: str
    basis: str
