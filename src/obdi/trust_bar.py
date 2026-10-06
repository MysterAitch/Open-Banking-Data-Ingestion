"""The trust bar's markup: dates in the `trust` module's own terms, percentages and classes out.

A bar holds nothing but where things are on the shared scale, so it reads the same masked or not.
It names no colour: each class (`b-*`) takes its look from `stylesheet_today`, so light and dark
are a change of tokens, and the bar is `aria-hidden` because the sentence beside it says it in
words (a bar of fill says nothing to a screen reader or in greyscale).

The classes, one rung more solid than the one before, so the ladder reads without colour:
`b-held` a hatch, `b-adds` an outline, `b-lock` a solid. `b-bad` is the red mark, `b-want` the
dashed block for a file wanted, and `b-edge` the tick at the left edge for history older than the
twelve months drawn.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date

from .standing_data import ADDS_UP
from .trust import MarkKind, Rung, Trust, month_marks, place

_STRETCH_CLASS = {Rung.HELD: "b-held", Rung.ADDS_UP: "b-adds", Rung.LOCKED: "b-lock"}
_MARK_CLASS = {
    MarkKind.NOT_ADDING_UP: "b-bad",
    MarkKind.LOCK_CHANGED: "b-bad",
    MarkKind.FILE_WANTED: "b-want",
}


def _cell(css: str, left: float, width: float) -> str:
    return f'<i class="{css}" style="left:{left:.2f}%;width:{width:.2f}%"></i>'


def bar_html(trust: Trust, today: date, span: tuple[date, date] | None = None) -> str:
    """One account's bar on the shared scale, or on the `span` it is given. On a span nothing lies
    earlier than the bar's left edge, so no tick says so."""
    cells: list[str] = []
    if trust.earlier and span is None:
        cells.append('<i class="b-edge"></i>')
    for stretch in trust.stretches:
        placed = place(stretch.start, stretch.end, today, span)
        if placed is not None:
            cells.append(_cell(_STRETCH_CLASS[stretch.rung], placed.left, placed.width))
    # Marks last, so each is drawn over the stretch beneath it.
    for mark in trust.marks:
        placed = place(mark.start, mark.end, today, span)
        if placed is not None:
            cells.append(_cell(_MARK_CLASS[mark.kind], placed.left, placed.width))
    return f'<span class="bar" aria-hidden="true">{"".join(cells)}</span>'


def source_lane_html(
    held: Sequence[tuple[date, date]],
    wanted: Sequence[tuple[date, date]],
    today: date,
    span: tuple[date, date] | None = None,
) -> str:
    """One source's lane on the shared scale (or the `span` given): the days it holds (`b-src`),
    and the days a file from it is wanted for (`b-want`, dashed). A bare line where it holds
    none."""
    cells = [
        _cell("b-src", placed.left, placed.width)
        for start, end in held
        if (placed := place(start, end, today, span)) is not None
    ]
    cells += [
        _cell("b-want", placed.left, placed.width)
        for start, end in wanted
        if (placed := place(start, end, today, span)) is not None
    ]
    return f'<span class="bar" aria-hidden="true">{"".join(cells)}</span>'


def axis_html(today: date) -> str:
    """The month names, printed once above a list of bars."""
    names = "".join(
        f'<span style="left:{left:.2f}%">{html.escape(name)}</span>'
        for name, left in month_marks(today)
    )
    return f'<div class="axis-row"><span class="axis" aria-hidden="true">{names}</span></div>'


def key_html() -> str:
    """What each fill and mark is, who did it, and against what. "Who did it" is the point of the
    key: nothing is vouched for by a person except a locked stretch."""
    adds = ADDS_UP.capitalize()
    entries = (
        (
            "b-lock",
            "Locked in",
            "You accepted it, after it was shown to add up. A later change to it is reported.",
        ),
        (
            "b-adds",
            adds,
            "obdi added the transactions up, and they reach the bank's own known balances. "
            "No person has vouched for it.",
        ),
        ("b-held", "Held", "The transactions are held and nothing has yet added them up."),
        ("k-line", "Nothing held", "Before the account existed, or after its sources stopped."),
        (
            "b-bad",
            "A red mark",
            "The transactions stop adding up here, or a locked stretch changed.",
        ),
        ("b-want", "A dashed block", "A file is wanted for these days."),
        ("b-edge", "A tick at the left edge", "History from before these twelve months is held."),
    )
    items = "".join(
        f'<li><span class="key {css}"></span><b>{html.escape(name)}</b>: {html.escape(means)}</li>'
        for css, name, means in entries
    )
    return (
        f'<ul class="keylist">{items}</ul>'
        "<p>Every bar runs over the same twelve months and ends today.</p>"
    )
