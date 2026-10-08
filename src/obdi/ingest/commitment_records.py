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
class Dismissal:
    """A series the owner said is not a commitment, found again by who is paid and where the
    money leaves, as a commitment is (`Commitment.entity_id` and `name_key`). It carries the
    cadence too, so a payee who also takes a different rhythm of payment is a different question.
    The count of dismissals over the series found is the detector's measured precision."""

    id: int
    entity_id: int | None
    name_key: str
    account: str
    direction: str
    cadence: str
    dismissed_at: str


@dataclass(frozen=True)
class Leg:
    """One step of how a commitment's money moves: from one end to the other, an amount or a share
    of the window's amount, by a day of the month.

    An END is a held account (`*_account`, a reference the registry holds) or an entity
    (`*_entity`), or both are empty. A leg with a held account at one end only is CHECKED against
    that account's transactions, the entity at the other end being who the money was paid to or
    came from; a leg with a held account at both ends is a move between the household's own
    accounts (money stashed in a space); a leg with no held account at either end is EXTERNAL
    (a partner paying the landlord directly) and is a fact declared, never checked and never
    reported. A leg whose money arrives in a held account from somewhere else is INCOMING: a
    receivable until it arrives.

    The leg is due by `day` of the month `months_before` (0 or 1) before the one the commitment's
    occurrence falls in, and may arrive up to `tolerance_days` after it before it is missing. Its
    amount is `amount_minor` or `share_percent` of the window's, whichever is declared."""

    id: int
    commitment_id: int
    position: int
    from_account: str
    to_account: str
    from_entity: int | None
    to_entity: int | None
    amount_minor: int | None
    share_percent: int | None
    day: int
    months_before: int
    tolerance_days: int
    label: str

    @property
    def incoming(self) -> bool:
        """Money arriving in a held account from outside the household's accounts."""
        return bool(self.to_account) and not self.from_account

    @property
    def external(self) -> bool:
        """Neither end is a held account: declared, never checked."""
        return not self.from_account and not self.to_account

    @property
    def between_accounts(self) -> bool:
        """Both ends are held accounts: a move within the household (a stash in a space)."""
        return bool(self.from_account) and bool(self.to_account)

    @property
    def held_account(self) -> str:
        """The held account whose transactions show the leg: where the money leaves if it leaves
        a held account, else where it arrives. Empty for an external leg."""
        return self.from_account or self.to_account

    @property
    def party(self) -> int | None:
        """The entity at the end that is not a held account, whose payments (or receipts) the
        leg is matched by; None where there is none (a move between two held accounts)."""
        return self.from_entity if self.incoming else self.to_entity


#: The most days after its day a leg may be declared to arrive before it is missing.
LEG_TOLERANCE_MAX = 14


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
    #: How the money moves, step by step (`Leg`), in the order declared; empty for a commitment
    #: that is one payment from its account.
    legs: tuple[Leg, ...] = ()

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
