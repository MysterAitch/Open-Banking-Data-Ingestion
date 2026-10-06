"""The markup of the "About this account" fold and of the list of windows the edit page repeats.

A quiet definition list in the style of a transaction's fold (`t-facts`), then the windows. A rate
or a limit is a figure, so it is a sealed slot on a page served on a GET and plain only in the
answer to a deliberate POST; kinds, names, and dates are shown either way.
"""

from __future__ import annotations

import html
from datetime import date

from .account_about import (
    AccountAbout,
    Window,
    covers,
    date_bases,
    days_to_end,
    windows_in_order,
)
from .accounts import AccountRecord, LimitWindow
from .masking import MASKED_TOTAL
from .money import format_amount
from .navigation import account_address
from .plural import plural


def _esc(text: object) -> str:
    return html.escape(str(text))


def percent_words(percent: float) -> str:
    """A rate as `22.9%`, with no trailing zeros."""
    return f"{percent:.2f}".rstrip("0").rstrip(".") + "%"


def figure_html(text: str, *, unmasked: bool) -> str:
    """A figure as the page prints it: the figure itself where values are shown, else one sealed
    slot that says nothing of its size."""
    if not unmasked:
        return f'<span class="mono nowrap fig sealed">{_esc(MASKED_TOTAL)}</span>'
    return f'<span class="mono nowrap fig">{_esc(text)}</span>'


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _what(window: Window) -> str:
    noun = "limit" if isinstance(window, LimitWindow) else "rate"
    return f"{_capitalised(window.kind)} {noun}" if window.kind.strip() else _capitalised(noun)


def span_words(window: Window) -> str:
    """The days a window holds, said so that an open end is said and not left blank."""
    first, last = window.window_from, window.window_to
    if first is not None and last is not None:
        return f"{first.isoformat()} to {last.isoformat()}"
    if first is not None:
        return f"from {first.isoformat()}, no end date"
    if last is not None:
        return f"until {last.isoformat()}"
    return "no dates given"


def _marks(window: Window, today: date) -> str:
    marks = []
    if covers(window.window_from, window.window_to, today):
        marks.append("current")
    left = days_to_end(window, today)
    if left is not None:
        marks.append("ends today" if left == 0 else f"ends in {plural(left, 'day')}")
    return ", ".join(marks)


def window_figure_html(window: Window, *, unmasked: bool) -> str:
    if isinstance(window, LimitWindow):
        return figure_html(format_amount(window.amount_minor), unmasked=unmasked)
    return figure_html(percent_words(window.annual_percent), unmasked=unmasked)


def windows_html(record: AccountRecord, today: date, *, unmasked: bool) -> str:
    """The account's limit and rate windows in date order, one line each, or nothing where it
    declares none."""
    ordered = windows_in_order(record)
    if not ordered:
        return ""
    items = []
    for window in ordered:
        said = [span_words(window)]
        marks = _marks(window, today)
        if marks:
            said.append(marks)
        items.append(
            f'<li class="window">{_esc(_what(window))} '
            f"{window_figure_html(window, unmasked=unmasked)} - {' - '.join(_esc(s) for s in said)}"
            "</li>"
        )
    return f'<ul class="windows">{"".join(items)}</ul>'


def _fact(name: str, value: str) -> str:
    return f"<dt>{_esc(name)}</dt><dd>{value}</dd>"


def _dated(day: date, basis: str) -> str:
    return f"{_esc(day.isoformat())} - {_esc(basis)}"


def declared_html(about: AccountAbout, today: date, *, unmasked: bool) -> str:
    """What the owner declared: kind, parent, the dates with how each came to be known, and the
    windows. Where nothing beyond the name is declared, one sentence that leads to the form."""
    record = about.record
    if about.unread:
        return f'<p class="warn">{_esc(about.unread)}</p>'
    if record is None:
        return (
            '<p class="sub">Nothing is declared about this account yet. '
            '<a class="tap" href="/declare-account">Declare it</a></p>'
        )
    facts = []
    if record.kind:
        facts.append(_fact("Kind", _esc(record.kind)))
    if record.parent is not None:
        shown = about.parent
        link = (
            f'<a class="tap" href="{_esc(account_address("ledger", str(record.parent)))}">'
            f"{shown.as_name() if shown is not None else _esc(record.parent)}</a>"
        )
        facts.append(_fact("Parent", link))
    opened_basis, closed_basis = date_bases(record)
    if record.opened is not None:
        facts.append(_fact("Opened", _dated(record.opened, opened_basis)))
    if record.closed is not None:
        facts.append(_fact("Closed", _dated(record.closed, closed_basis)))
    windows = windows_html(record, today, unmasked=unmasked)
    if not facts and not windows:
        target = _esc(account_address("edit", str(record.ref)))
        return (
            '<p class="sub">Nothing beyond its name is declared. '
            f'<a class="tap" href="{target}">Declare its terms</a></p>'
        )
    return (f'<dl class="t-facts">{"".join(facts)}</dl>' if facts else "") + windows
