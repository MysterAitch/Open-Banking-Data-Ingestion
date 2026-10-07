"""What reading a statement in did for what the owner wanted, in the order he asks it.

An upload is made for a reason: a period of an account is wanted, and a statement is fetched to
fill it. The answer says that first - which wanted period the statement covered and whether
anything more is wanted for the account - then how many of its transactions were new, then how many
still need a decision. The importer's own counts (`ingest`) are not an answer to any of those and
are kept apart, folded.

THE DECISIONS are the open flags read AFTER the import. The importer's summary counts the flags it
raised while it ran, and the same import then settles the ones the evidence answers, so that count
once said one transaction needed a decision on a page whose review report showed none open.

Nothing here reads a figure: dates, counts, and account names only.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ..core.page_times import range_text
from ..core.plural import plural
from .account_names import AccountsShown
from .bring_in import WantedFile

_esc = html.escape

#: The importer's own count of transactions it had not held, in its summary line (`ingest`).
_NEW = re.compile(r"\bnew (\d+)\b")


@dataclass(frozen=True)
class Coverage:
    """What a statement did to the statements wanted for its account."""

    #: What was wanted for the account before it was read in.
    wanted: int
    #: The wanted periods it covered whole, and those it covered only some of.
    whole: tuple[tuple[date, date], ...]
    partly: tuple[tuple[date, date], ...]
    #: How many statements are still wanted for the account.
    still: int


def coverage(
    before: Sequence[WantedFile],
    after: Sequence[WantedFile],
    account: str,
    listed: tuple[date, date] | None,
) -> Coverage:
    """The wanted periods of `account` the statement covered, from the wanted statements before
    and after it was read in.

    A wanted period is covered whole where nothing of it is wanted afterwards, and in part where
    some of it is. Where the days the statement lists are known, only a wanted period they touch
    can have been covered by it: another statement of the same press may have covered the rest.
    """
    was = [item for item in before if item.account == account]
    now = [item for item in after if item.account == account]
    whole: list[tuple[date, date]] = []
    partly: list[tuple[date, date]] = []
    for item in was:
        if listed is not None and not (item.first <= listed[1] and item.last >= listed[0]):
            continue
        left = [rest for rest in now if rest.first <= item.last and rest.last >= item.first]
        if not left:
            whole.append((item.first, item.last))
        elif (min(r.first for r in left), max(r.last for r in left)) != (item.first, item.last):
            partly.append((item.first, item.last))
    return Coverage(len(was), tuple(whole), tuple(partly), len(now))


def new_transactions(counted: str) -> int | None:
    """How many transactions the importer says were new, or None where its line does not say."""
    found = _NEW.search(counted)
    return int(found.group(1)) if found else None


def _days(spans: Sequence[tuple[date, date]]) -> str:
    return ", ".join(range_text(first, last) for first, last in spans)


def sentences(
    covered: Coverage | None,
    new: int | None,
    undecided: int | None,
    *,
    account: str,
    names: AccountsShown,
) -> list[str]:
    """The sentences of one statement's outcome, as markup: what it covered, how many of its
    transactions are new, and how many need a decision.

    `covered` is None where what was wanted could not be worked out, and `undecided` None where
    the open flags could not be read: each is then said, because "cannot say" is not "none".
    """
    whose = names.of(account).as_name()
    found: list[str] = []
    if covered is None:
        found.append("What was wanted could not be worked out, so what it covered is not said.")
    elif not covered.wanted:
        found.append("Nothing was wanted for it.")
    elif covered.whole or covered.partly:
        what = (
            f"Covers the wanted {_esc(_days(covered.whole))}"
            if covered.whole
            else f"Covers part of the wanted {_esc(_days(covered.partly))}"
        )
        more = (
            f"nothing more is wanted for {whose}"
            if not covered.still
            else f"{plural(covered.still, 'statement')} "
            f"{'is' if covered.still == 1 else 'are'} still wanted for {whose}"
        )
        found.append(f"{what}; {more}.")
    else:
        found.append(f"It covers none of what is wanted for {whose}.")
    if new is not None:
        found.append(
            "None of its transactions is new."
            if not new
            else f"{plural(new, 'transaction')} {'is' if new == 1 else 'are'} new."
        )
    if undecided is None:
        found.append("Whether any transaction needs a decision could not be read just now.")
    elif undecided:
        found.append(
            f"{plural(undecided, 'transaction')} {'needs' if undecided == 1 else 'need'} a "
            'decision. <a class="tap" href="/review-flags">Decide</a>'
        )
    return found


def open_flags_by_account(cards: Sequence[object]) -> Mapping[str, int]:
    """How many open flags each account has, from the queue's cards (each names its `ref`)."""
    counts: dict[str, int] = {}
    for card in cards:
        ref = str(getattr(card, "ref", ""))
        counts[ref] = counts.get(ref, 0) + 1
    return counts


__all__ = ["Coverage", "coverage", "new_transactions", "open_flags_by_account", "sentences"]
