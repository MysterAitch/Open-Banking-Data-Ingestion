"""The markup of the "About this account" fold and of the list of windows the edit page repeats.

A quiet definition list in the style of a transaction's fold (`t-facts`), then the windows. A rate
or a limit is a figure, so it is a sealed slot on a page served on a GET and plain only in the
answer to a deliberate POST; kinds, names, and dates are shown either way.
"""

from __future__ import annotations

import html
from datetime import date

from ..core.addresses import account_address
from ..core.masking import MASKED_TOTAL
from ..core.money import format_amount
from ..core.plural import plural
from ..ingest.accounts import AccountRecord, LimitWindow
from ..read.account_about import (
    DIFFERS,
    AccountAbout,
    RateCheck,
    StatedText,
    Window,
    covers,
    date_bases,
    days_to_end,
    rate_checks,
    windows_in_order,
)
from ..verify.statement_sections import masked


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


def window_line_html(window: Window, today: date, *, unmasked: bool) -> str:
    """One window said in a line: what it is, its figure, the days it holds, and whether it is
    current or ending. The edit page sets its fields under the same line."""
    said = [span_words(window)]
    marks = _marks(window, today)
    if marks:
        said.append(marks)
    return (
        f"{_esc(_what(window))} "
        f"{window_figure_html(window, unmasked=unmasked)} - {' - '.join(_esc(s) for s in said)}"
    )


def windows_html(record: AccountRecord, today: date, *, unmasked: bool) -> str:
    """The account's limit and rate windows in date order, one line each, or nothing where it
    declares none."""
    ordered = windows_in_order(record)
    if not ordered:
        return ""
    items = [
        f'<li class="window">{window_line_html(window, today, unmasked=unmasked)}</li>'
        for window in ordered
    ]
    return f'<ul class="windows">{"".join(items)}</ul>'


def _rate_kind(kind: str) -> str:
    return f"{_capitalised(kind.strip())} rate" if kind.strip() else "Rate"


def _earlier(count: int) -> str:
    return f" (and {count} earlier)" if count else ""


def _source(source: str) -> str:
    return f"<code>{_esc(source)}</code>"


def _check_html(check: RateCheck, *, unmasked: bool) -> str:
    """One stated rate: what kind, how it stands against what was declared, and which statement
    from which source said it."""
    rate = check.rate
    figure = figure_html(percent_words(rate.percent), unmasked=unmasked)
    day = _esc(rate.day.isoformat())
    kind = _esc(_rate_kind(rate.kind))
    if check.outcome == DIFFERS:
        return (
            f'<li class="stated">{kind} - differs: the statement of {day} prints {figure}'
            f"{_earlier(check.earlier)} - {_source(rate.source)}</li>"
        )
    return (
        f'<li class="stated">{kind} {figure} - {_esc(check.outcome)} - {_source(rate.source)}, '
        f"statement of {day}{_earlier(check.earlier)}</li>"
    )


def _text_html(text: StatedText, *, unmasked: bool) -> str:
    value = text.value if unmasked or not text.private else masked(text.value)
    when = f", {_esc(text.day_words)} {_esc(text.day.isoformat())}" if text.day is not None else ""
    return (
        f'<li class="stated">{_esc(text.what)} {_esc(value)} - {_source(text.source)}{when}</li>'
    )


def stated_html(about: AccountAbout, *, unmasked: bool) -> str:
    """What the sources state: each rate a kept statement prints against the declared window its
    day falls in, and the names a statement or the provider gives. Empty where nothing is stated
    and nothing could not be read."""
    facts = about.facts
    items = "".join(
        _check_html(check, unmasked=unmasked) for check in rate_checks(facts.rates, about.record)
    ) + "".join(_text_html(text, unmasked=unmasked) for text in facts.texts)
    warnings = "".join(f'<p class="warn">{_esc(line)}</p>' for line in facts.unread)
    return (f'<ul class="stated">{items}</ul>' if items else "") + warnings


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
