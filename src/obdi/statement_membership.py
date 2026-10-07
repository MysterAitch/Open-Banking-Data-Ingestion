"""Which statement a row belongs to, where a held statement says so.

A card statement lists a purchase by its transaction date and a feed by its
posting date, and when identity merges the two into one row the stored date is
the feed's. A purchase made on a statement's last day and posted the next day
then falls, by its stored date, in the NEXT statement's period: that period's
rows overshoot its movement and the earlier one's undershoot it, by the same
purchase, although every row is held exactly once. Measured on a real card
whose statement rows had all merged onto feed rows ("nothing only in the
statements, nothing only in the feed"), most periods still differed.

So which side of a statement's closing a row falls on is decided by MEMBERSHIP
where it is known: a row sighted by a held statement's artefact (a sighting
records the digest it came from, and a statement's digest identifies it) counts
in that statement's period, on or before its closing and after the previous
statement's, whatever date it now carries. A row no statement lists is placed
by its date, as before. A row two statements list (overlapping statements, or
two files of one statement) counts once, in the EARLIEST closing that lists it,
which is the period the statements' own arithmetic gives it: the later
statement's movement from the earlier closing does not include it.

This is the one place that is decided. `balance_anchors.derive_opening` uses
`Membership.placed` for its statement anchors and `period_reconciliation` uses
`Membership.placement` for its periods, so the anchor checks and the report
cannot disagree about which period a row is in. Stated, bank, and family
anchors say something about the account as at a date and are placed by date.

Only statements that supply an anchor take part: one that does not reconcile
says nothing trustworthy about which rows it lists.

REJECTED. Re-dating the merged row to the statement's date: the feed's date is
the one Actual and the ledger carry, and a statement-listed date is not always
the right one for other purposes. Choosing the LATEST closing for a shared
row: the earlier statement's own closing then lacks a row it printed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from .core.models import Transaction
from .statement_terms import StatementBalance
from .store import Store


@dataclass(frozen=True)
class ListedStatement:
    """One statement, closing on `day`, and the rows it lists.

    Two held files with the same closing and the same rows are ONE statement,
    so `digests` can hold more than one.
    """

    day: date
    balance_minor: int
    digests: frozenset[str]
    #: Entity ids of the rows the statement lists, whatever their status.
    members: frozenset[str]


@dataclass(frozen=True)
class Membership:
    #: Each distinct statement, earliest closing first.
    statements: tuple[ListedStatement, ...]
    #: Row entity id -> closing day of the earliest statement that lists it.
    placed: Mapping[str, date]

    def placement(self, row: Transaction) -> date:
        """The day this row counts on, for judging it against a statement closing."""
        return self.placed.get(row.entity_id, row.value_date)


def statement_membership(
    store: Store, account: str, statements: Iterable[StatementBalance]
) -> Membership:
    """The membership of `account`'s statements, as `statement_balances` returns them."""
    held = [s for s in statements if s.digest]
    listed = store.entities_sighted_by(account, {s.digest for s in held})
    distinct: dict[tuple[date, int, frozenset[str]], set[str]] = {}
    for statement in held:
        members = frozenset(listed.get(statement.digest, ()))
        distinct.setdefault((statement.day, statement.balance_minor, members), set()).add(
            statement.digest
        )
    ordered = tuple(
        ListedStatement(day, balance, frozenset(digests), members)
        for (day, balance, members), digests in sorted(
            distinct.items(), key=lambda item: (item[0][0], item[0][1], sorted(item[1]))
        )
    )
    placed: dict[str, date] = {}
    for listing in ordered:
        for entity in listing.members:
            placed.setdefault(entity, listing.day)
    return Membership(ordered, placed)
