"""What the net figure rests on: its trust line, the accounts that rest on nothing checked, and the
counted accounts listed with the trust bar.

A FIGURE IS ONLY AS TRUSTWORTHY AS THE LEAST-TRUSTED ACCOUNT IN IT. The trust of each account is
`trust.trust_of`, read from the Overview's account, the same reading Today draws; this module adds
nothing to what is concluded and only says it beside the figure. The oldest date any counted
account adds up to is where the figure's checking begins to fail, and an account with no such date
is named, since a sum that includes it rests on a balance nobody has tested.

NO FIGURE ON A GET. Dates, names, and counts only; a balance appears beside an account's row only
where the caller says the page is unmasked, and arrives already formatted by the page.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from .account_names import AccountShown
from .core.page_times import date_with_age
from .overview import AccountOverview
from .standing_data import ADDS_UP, NOTHING_TO_CHECK_AGAINST
from .trust import Trust
from .trust_bar import axis_html, key_html
from .web_overview import _row_html, _trust_of, arrange, row_reading, serial

_esc = html.escape

#: `row_reading`'s groups from this one up are fine (adds up, quiet, empty): 0 does not add up
#: and 1 has nothing to check against, and those stay open.
_FINE_FROM = 2

#: Said once when the Overview could not be read at all, and never per account.
UNREAD = (
    '<p class="muted pos-unread">The trust of each account could not be read just now, so the '
    "accounts are listed without it.</p>"
)


@dataclass(frozen=True)
class Reading:
    """The counted accounts' trust, worked out once for the figure's two lines and the list."""

    held: Sequence[AccountOverview]
    trusts: Mapping[str, Trust]
    unread: tuple[str, ...]


def read(
    counted: Sequence[tuple[str, str]],
    accounts: Sequence[AccountOverview],
    today: date,
) -> Reading:
    """Each counted `(ref, label)` against the Overview's accounts; a counted account the
    Overview does not hold is `unread`, which is its own answer and never "nothing to check"."""
    by_ref = {account.ref: account for account in accounts}
    held = [by_ref[ref] for ref, _ in counted if ref in by_ref]
    unread = tuple(label for ref, label in counted if ref not in by_ref)
    return Reading(held, {a.ref: _trust_of(a, {}, today) for a in held}, unread)


def _unchecked(reading: Reading) -> list[AccountOverview]:
    """Counted accounts whose balance has no day it adds up to. A Space with a main account on
    the page is tested with that account, so it is not among them."""
    refs = {a.ref for a in reading.held}
    return [
        a
        for a in reading.held
        if reading.trusts[a.ref].adds_up_to is None
        and not (a.parent is not None and a.parent in refs)
    ]


def figure_lines(reading: Reading, today: date, shown: Callable[[str], AccountShown]) -> str:
    """The figure's own trust line and the accounts that rest on nothing checked."""
    dates = [t.adds_up_to for t in reading.trusts.values() if t.adds_up_to is not None]
    if dates:
        rests = (
            f"The oldest of them {ADDS_UP} to the known balances to "
            f"{_esc(date_with_age(min(dates), today))}."
        )
    else:
        rests = f"None of them {ADDS_UP} to a known balance, so nothing here has been tested."
    out = f'<p class="pos-rests">{rests}</p>'
    names = [shown(a.ref).as_name() for a in _unchecked(reading)]
    unread = [_esc(label) for label in reading.unread]
    said = []
    if names:
        one = len(names) == 1
        said.append(
            f"{serial(names)} rest{'s' if one else ''} on nothing checked: there is "
            f"{NOTHING_TO_CHECK_AGAINST} for {'it' if one else 'them'}."
        )
    if unread:
        said.append(f"{serial(unread)} {'has' if len(unread) == 1 else 'have'} no trust reading.")
    if said:
        out += f'<p class="pos-unchecked">{" ".join(said)}</p>'
    return out


def list_html(
    reading: Reading,
    today: date,
    shown: Callable[[str], AccountShown],
    balances: Mapping[str, str],
    archived: frozenset[str],
) -> str:
    """The counted accounts, worst-off first, each as Today draws it, with the account's balance
    after its trust sentence where the page is unmasked. An archived account is counted like any
    other and kept in its own fold."""
    by_ref = {a.ref: a for a in reading.held}

    def rows(accounts: Sequence[AccountOverview]) -> str:
        out = []
        for parent, spaces in arrange(accounts):
            for account in (parent, *spaces):
                row = _row_html(
                    account, shown, {}, {}, today, space=account is not parent, by_ref=by_ref
                )
                figure = balances.get(account.ref)
                if figure:
                    # The row is Today's, drawn by one implementation; the balance is the one
                    # thing this page adds to it, as the last child of its link.
                    fig = f'<span class="pos-fig">{figure}</span>'
                    row = row.replace("</a></li>", f"{fig}</a></li>")
                out.append(row)
        return "".join(out)

    live = [a for a in reading.held if a.ref not in archived]
    old = [a for a in reading.held if a.ref in archived]
    # What is fine is one line, as on Today's silence: the accounts that add up fold behind a
    # count, so the open list is the accounts the figure is least sure of however many there are.
    asking = [a for a in live if row_reading(a).group < _FINE_FROM]
    fine = [a for a in live if row_reading(a).group >= _FINE_FROM]
    out = ""
    if asking:
        out += f'{axis_html(today)}<ul class="alist">{rows(asking)}</ul>'
    if fine:
        out += (
            f"<details><summary>{len(fine)} add up to the known balances</summary>"
            f'{axis_html(today)}<ul class="alist">{rows(fine)}</ul></details>'
        )
    if old:
        out += (
            f"<details><summary>{len(old)} archived, counted like any other</summary>"
            f'<ul class="alist">{rows(old)}</ul></details>'
        )
    return out + f"<details><summary>What the bars show</summary>{key_html()}</details>"
