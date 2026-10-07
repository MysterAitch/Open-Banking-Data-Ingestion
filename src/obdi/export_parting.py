"""The first row of an export after which its balance and the store's running sum part.

An export states a balance after EVERY row. The day-level anchors (`cut_anchors`)
say that a window differs; this says where inside it, by walking the export's rows
in the file's own sequence and keeping the store's running sum beside the export's.

THE RULE, stated here and only here: within a window whose difference changes,
the store's running sum after row k is the sum of what the store holds for each of
rows 1 to k, plus the surplus rows placed before or at k, and the export's is the
sum of the export's own figures. The first row after which the two differ is where
they part. What the store holds for a row is its counterpart (`Counterpart`): the
counted row the export row is sighted on, or nothing.

A surplus row is a counted row of the family that the export does not list. It has
no place in the export's sequence, so it is placed by the feed's own times where
the row and its neighbours have them: before the first row of its day whose
counterpart happened later. Without a time for either, or on a tie, it goes after
the last row of its day, which is the earliest point it is certainly in.

The sequence is only as trustworthy as the file's order, which `cut_anchors`
treats with care: the day-level verdict and the counts never rest on this.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from .core.masking import Structural
from .family_anchors import ExportRow

#: What the store holds for an export row.
NOTHING = "nothing"
HELD = "held"
NOT_COUNTED = "not counted"
ELSEWHERE = "another account"


@dataclass(frozen=True)
class Counterpart:
    """What the store holds for one export row."""

    kind: str
    #: The counted amount: nil unless the store counts the row.
    amount_minor: int = 0
    #: Why the store does not count a row it holds, where it does not.
    why: str = ""
    #: The feed's time for the held row, where it gave one.
    at: datetime | None = None


@dataclass(frozen=True)
class Surplus:
    """A counted row of the family that the export does not list."""

    day: date
    amount_minor: int
    at: datetime | None = None


@dataclass(frozen=True)
class RowParting:
    """Where an export's balance and the store's running sum first part, without a figure."""

    #: The row's place in the export's own sequence, counting from one, of `total` rows.
    position: Structural[int]
    total: Structural[int]
    day: Structural[date]
    #: "in" or "out".
    direction: Structural[str]
    #: What the store holds in the row's place: `NOTHING`, `HELD`, `NOT_COUNTED`,
    #: or `ELSEWHERE`, and "another size" for a held row of a different figure.
    holds: Structural[str]
    why: Structural[str] = ""
    #: Surplus rows that fall just before this row, between it and the one before.
    surplus_before: Structural[int] = 0
    #: The balances do not part within the window's rows: the surplus rows fall after
    #: the last of them, which is the row named.
    after_last: Structural[bool] = False


ANOTHER_SIZE = "another size"


def _slot(
    surplus: Surplus, rows: Sequence[tuple[int, ExportRow]], held: Mapping[int, Counterpart]
) -> int:
    """The index of the row the surplus row falls just before; `len(rows)` for after the last."""
    for index, (position, row) in enumerate(rows):
        if row.day > surplus.day:
            return index
        if row.day == surplus.day:
            there = held.get(position)
            when = there.at if there is not None else None
            if surplus.at is not None and when is not None and when > surplus.at:
                return index
    return len(rows)


def first_parting(
    rows: Sequence[tuple[int, ExportRow]],
    held: Mapping[int, Counterpart],
    surplus: Sequence[Surplus],
    total: int,
) -> RowParting | None:
    """The first row after which the export's balance and the store's sum part, or None.

    `rows` are the window's export rows in the export's own sequence, each with its
    one-based position in the whole; `held` is the counterpart of each by position.
    """
    if not rows:
        return None
    slots: dict[int, list[Surplus]] = {}
    for found in surplus:
        slots.setdefault(_slot(found, rows, held), []).append(found)
    difference = 0
    for index, (position, row) in enumerate(rows):
        before = slots.get(index, [])
        there = held.get(position, Counterpart(NOTHING))
        difference -= sum(s.amount_minor for s in before)
        difference += row.amount_minor - there.amount_minor
        if difference == 0:
            continue
        if there.kind == HELD:
            holds = HELD if there.amount_minor == row.amount_minor else ANOTHER_SIZE
        else:
            holds = there.kind
        return RowParting(
            position,
            total,
            row.day,
            "in" if row.amount_minor >= 0 else "out",
            holds,
            there.why,
            len(before),
        )
    trailing = slots.get(len(rows), [])
    if trailing and difference - sum(s.amount_minor for s in trailing) != 0:
        position, row = rows[-1]
        return RowParting(
            position,
            total,
            row.day,
            "in" if row.amount_minor >= 0 else "out",
            HELD,
            surplus_before=len(trailing),
            after_last=True,
        )
    return None


__all__ = [
    "ANOTHER_SIZE",
    "ELSEWHERE",
    "HELD",
    "NOTHING",
    "NOT_COUNTED",
    "Counterpart",
    "RowParting",
    "Surplus",
    "first_parting",
]
