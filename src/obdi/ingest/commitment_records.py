"""What the store returns and raises where the owner's commitments are concerned.

A commitment is one recurring payment (or receipt) the owner confirmed: what it is for, who it is
paid to, who starts it, the account it usually leaves, and its TERMS as dated windows - the amount,
the cadence, the usual day - so that a price rise is a new window beside the old one and the
history stays (`docs/design/2026-10-commitments/plan.md` section 3). The store keeps the rows and
hands them back as these records, so they sit here, below the store, and the analysis imports
them rather than the store importing the analysis. Nothing here decides whether a series is a
commitment's: `analysis.commitments` holds that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..core.errors import DataError

#: Who starts a commitment's payments, as the detector says it (`analysis.recurring.PULLED`,
#: `SCHEDULED`, `HABIT`, which a test holds equal to these). Said here as well because the store
#: refuses a kind outside them and sits below the analysis.
COMMITMENT_KINDS = ("pulled", "scheduled", "habit")

#: Which way the money moves, as `Series.direction` says it.
DIRECTIONS = ("out", "in")


class CommitmentRefused(DataError):
    """A change to the commitments that was not made, said in the words the page shows."""


@dataclass(frozen=True)
class Window:
    """The terms of a commitment between two days, the end included; `to_day` None while open.

    `amount_minor` is the usual amount as a magnitude (the direction is the commitment's).
    `usual_day` is the day of the month, or the weekday (Monday is 0) for a cadence counted in
    days; `usual_month` is the month of a yearly cadence and 0 otherwise. `basis` says where the
    terms came from, in words, as they were written ("confirmed from the series on D", "edited")."""

    id: int
    commitment_id: int
    from_day: date
    to_day: date | None
    amount_minor: int
    currency: str
    cadence: str
    usual_day: int
    usual_month: int
    tolerance_days: int
    basis: str


@dataclass(frozen=True)
class WindowTerms:
    """The terms a window is opened with (everything but its identity and its days)."""

    amount_minor: int
    currency: str
    cadence: str
    usual_day: int
    usual_month: int
    tolerance_days: int
    basis: str


@dataclass(frozen=True)
class Commitment:
    """One commitment not removed, with all its windows, oldest first.

    `entity_id` is the entity its payee was gathered under when it was confirmed, or None; and
    `name_key` is the name the detector called the payee by then (`Series.name_keys`). Either
    finds the commitment's series on a later read, so gathering the name into an entity afterwards
    changes nothing. `name` is what it is for, the owner's to edit."""

    id: int
    name: str
    entity_id: int | None
    name_key: str
    kind: str
    account: str
    direction: str
    created_at: str
    windows: tuple[Window, ...]

    @property
    def current(self) -> Window | None:
        """The window in force now: the open one, else the one that began last."""
        for window in self.windows:
            if window.to_day is None:
                return window
        return self.windows[-1] if self.windows else None

    @property
    def ended(self) -> date | None:
        """The day the commitment ended, or None while a window is open."""
        current = self.current
        return None if current is None else current.to_day
