"""Why a file is wanted, in full: the sentences a gap carries, as plain text with dates only.

Bring in says each file in a few words beside its days. The full reasoning - what the statements
held prove, what is inferred from their rhythm and from what, and which end of a hole is a guess -
is evidence, kept for the fold that opens on demand (`web_bring_in`) and for the tests that hold
each sentence to the data that produced it (`fetch_gaps` decides; this only says). No amount, no
description: dates, counts, and source names.
"""

from __future__ import annotations

from datetime import date, timedelta

from .core.plural import agree, plural
from .fetch_gaps import Basis, FetchGap, GapKind
from .verify.statement_span import HoleReason


def _closings(days: tuple[date, ...]) -> str:
    named = [day.isoformat() for day in days]
    if len(named) <= 2:
        return " and ".join(named)
    return f"{', '.join(named[:-1])}, and {named[-1]}"


def _inferred(gap: FetchGap) -> str:
    """The sentence that says a count of statements is a guess, and from what."""
    if not gap.probably:
        return ""
    count = plural(gap.probably, "statement")
    return (
        f"Probably {count} {agree(gap.probably, 'is')} missing here, closing about "
        f"{_closings(gap.closings)}. That is inferred from how regularly the statements held "
        "arrive."
    )


def gap_lines(gap: FetchGap) -> tuple[str, str, str]:
    """(the dates to type, what to do, why), each a sentence or phrase of plain text."""
    first, last = gap.first_day.isoformat(), gap.last_day.isoformat()
    span = f"{first} to {last}"
    before = (gap.first_day - timedelta(days=1)).isoformat()
    kind = gap.kind
    if kind is GapKind.NEWER_STATEMENT:
        held_to = gap.rows_to.isoformat() if gap.rows_to else last
        why = f"Transactions are held to {held_to} with no known balance since {before}."
        if gap.probably:
            why += " " + _inferred(gap).replace("missing here", "waiting")
        return span, f"Fetch every statement after {before}.", why
    if kind is GapKind.HOLE_BETWEEN:
        after = (gap.last_day + timedelta(days=1)).isoformat()
        earlier = (gap.earlier_closing or gap.first_day - timedelta(days=1)).isoformat()
        later = (gap.later_closing or gap.last_day + timedelta(days=1)).isoformat()
        apart = (
            (gap.later_closing - gap.earlier_closing).days
            if gap.later_closing and gap.earlier_closing
            else (gap.last_day - gap.first_day).days + 2
        )
        between = f"Fetch the statement closing between {earlier} and {later}."
        end = (
            f" Where the gap ends is inferred: the missing statement is expected to have "
            f"closed about {gap.last_day.isoformat()}."
            if gap.last_day_inferred
            else ""
        )
        if gap.reason is HoleReason.BALANCES_DIFFER:
            spacing = (
                f" The two statements close {apart} days apart and the statements held "
                f"usually close about a month apart, which agrees. {_inferred(gap)}{end}"
                if gap.probably
                else ""
            )
            return (
                span,
                between if gap.probably else f"Fetch the statement covering {span}.",
                f"The statement closing {later} opens on a balance that is not the one the "
                f"statement closing {earlier} ended on, so something lies between them that "
                f"neither lists, starting {first}.{spacing}",
            )
        if gap.reason is HoleReason.UNLISTED_ROWS:
            return (
                span,
                f"Fetch the statement covering {span}.",
                f"Another source holds {plural(gap.unlisted_rows, 'payment')} dated in these "
                f"days that no statement held lists. {_inferred(gap)}{end}".rstrip(),
            )
        if gap.reason is HoleReason.BALANCES_MEET_NET_NIL:
            return (
                span,
                between,
                f"The later statement opens on the balance the earlier one closed on, but they "
                f"close {apart} days apart and the statements held usually close about a month "
                "apart. A statement between them would have to net to nil, which cannot be "
                f"ruled out from the balances. {_inferred(gap)}{end}".rstrip(),
            )
        if gap.basis is Basis.STATED:
            return (
                span,
                f"Fetch the statement covering {span}.",
                f"The statement after it says its period begins on {after}, and the one before "
                f"closed on {before}, so no statement held covers the days between.",
            )
        return (
            span,
            between,
            f"Those two statements close {apart} days apart, "
            f"and the statements held usually close about a month apart. {_inferred(gap)}{end}",
        )
    if kind is GapKind.EXPORT_STOPS:
        held = (
            f" other sources hold transactions to {gap.rows_to.isoformat()}."
            if gap.rows_to
            else ""
        )
        return (
            span,
            f"Export from {first} to today.",
            f"The export last covers {before};"
            f"{held or ' other sources hold later transactions.'}",
        )
    if kind is GapKind.EXPORT_MONTHS:
        return (
            span,
            f"Export {span}.",
            "Another source holds transactions in these months and the export holds none.",
        )
    if kind is GapKind.NO_BALANCE:
        return (
            span,
            f"Fetch a statement covering {span}, or state a balance.",
            "No known balance, so there is nothing to check the transactions against.",
        )
    if kind is GapKind.AUTOMATIC_ONLY:
        return (
            span,
            f"Fetch a statement covering {span}, or state a balance.",
            "Only the bank's feed and the aggregator supply this account and no known balance "
            "is held, so there is nothing to check the transactions against.",
        )
    if kind is GapKind.ONE_BALANCE:
        return (
            span,
            f"Fetch the statement before these rows, closing on or before {before}, "
            "or the one after them.",
            "Only one known balance is held, so there is nothing yet to check the "
            "transactions against.",
        )
    if kind is GapKind.NOTHING_BEFORE:
        return (
            span,
            f"Fetch an earlier statement, closing on or before {before}.",
            f"Transactions from {first} to {last} have no known balance before them, so they "
            "cannot be tested.",
        )
    return (
        gap.first_day.isoformat(),
        "Fetch a statement that settles the review flag.",
        gap.why + " The flag is on the review flags page.",
    )
