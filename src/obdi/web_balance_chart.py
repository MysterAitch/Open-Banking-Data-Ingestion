"""The balance-difference timeline and its values chart, in both renderings.

It follows the ledger page's mechanism exactly. A GET renders the MASKED
timeline whatever its query string. Showing values takes a POST, answered
directly with the values chart and never redirected, so no address a person can
bookmark, paste into a note, or fetch from a script holds a value, and the
response is sent `no-store` so a browser history does not either.

THE TIMELINE HAS NO VERTICAL MAGNITUDE. A step's size is a figure, and a mark
taller for a bigger step would draw the figure. Every mark is one height and
width, rows say what KIND of change a mark is, and the only measures that vary
are the date and a count, neither of which is private. The values chart is
where sizes are drawn.

THE TIMELINE FITS THE SCREEN and never scrolls: it is drawn in `VIEW_WIDTH`
units and scaled to its container, with changes combined into bins by
`balance_chart_bins`, which states how. A table of counts per period beneath it
says where in time the changes are, and each period opens that period.

THE VALUES CHART is the one that scrolls. A full view of it is `PIXELS_PER_DAY`
wide per day, so about seven and a half years come to about ten thousand pixels,
inside a container that scrolls sideways so the page itself does not. A
`from`/`to` range is drawn so that the whole range is about `TARGET_RANGE_WIDTH`
wide, no narrower than a full view and no wider per day than
`MAX_PIXELS_PER_DAY`, so a month opens at day scale. The labels are drawn in a
column beside the scrolling chart, so the figures stay in view while the dates
scroll. It cannot scroll itself to a change, so a row of posted links opens it
around each change in range.

Charts are inline SVG with presentation attributes and no script, as the
position page's is, so nothing is added to the shared stylesheet that pages
which must show no figure are searched against.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import floor, log10
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .balance_chart import OWN, WHOLE, BalanceChart, SourceLine
from .balance_chart_bins import (
    DAY,
    MONTH,
    YEAR,
    Change,
    Period,
    bins_of,
    calendar_span,
    changes_in,
    choose_bin_unit,
    count_by_kind,
    empty_runs,
    nominal_bin_width,
    period_label,
    period_unit,
    periods_of,
)
from .callback import render_page
from .errors import DataError
from .fault_structure import (
    EXPLAINED,
    GAP_BUCKETS,
    IRREGULAR,
    MONTHLY,
    RECURRING,
    TRANSIENT,
    UNEXPLAINED,
    UNHELD,
    FaultStructure,
    StructureReport,
)
from .logs import say
from .masking import Disclosed
from .plural import agree
from .plural import plural as _plural
from .web_accounts import submit_button

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

PIXELS_PER_DAY = 3.65
TARGET_RANGE_WIDTH = 10_000
MAX_PIXELS_PER_DAY = 240.0

#: The one height of every mark on the timeline, whatever it stands for.
MARK_HEIGHT = 14
ROW_HEIGHT = 38
EDGE = 24
AXIS_WIDTH = 78

#: The timeline's own units, not pixels: the browser scales the whole drawing
#: to its container. The width is the phone's, so text is readable there and
#: never much larger elsewhere.
VIEW_WIDTH = 360
PLOT_LEFT = 80
PLOT_RIGHT = VIEW_WIDTH - 8
_MARK_WIDEST = 14
_MARK_NARROWEST = 4

#: A view of this many days or fewer is labelled by month and day; a longer one
#: by year and month, and one of more than `_YEARS_VIEW_DAYS` by year alone.
_SHORT_VIEW_DAYS = 93
_YEARS_VIEW_DAYS = 1100
#: Past this many months every third is labelled, and a short view labels every
#: day only up to this many days and every week beyond.
_MONTHS_LABELLED_ALL = 14
_EVERY_DAY_LABELLED = 19
_DIM = ' fill-opacity=".8"'

#: How many days of a long list are named before "and N more".
_NAMED_DAYS = 12

#: How many changes the values chart offers a link around, before "and N more".
_CHANGE_LINKS = 20

#: Days either side of a change that its values link opens.
_CHANGE_WINDOW_DAYS = 3

#: The most days a requested range may cover: it keeps a mistyped range from
#: asking for a page of millions of pixels.
MAX_RANGE_DAYS = 366 * 40

#: (row name, colour, what it is) per kind. Colours are the Okabe-Ito set, which
#: stays distinguishable under the common colour-vision differences; a kind is
#: also told apart by its row (timeline) and its shape (values chart).
KINDS: dict[str, tuple[str, str, str]] = {
    TRANSIENT: (
        "Timing pair",
        "#0072B2",
        "a step that a later step of exactly the opposite size undoes",
    ),
    RECURRING: (
        "Recurring size",
        "#CC79A7",
        "a step of a size that recurs three or more times",
    ),
    UNEXPLAINED: ("Unexplained", "#D55E00", "a permanent step with no explanation found"),
    UNHELD: (
        "Unheld Space",
        "#E69F00",
        "a permanent step beside a transfer to a Space whose rows are not held",
    ),
    EXPLAINED: (
        "Explained",
        "#009E73",
        "a permanent step the ledger's exact arithmetic explains",
    ),
}

_SOURCE_COLOURS = ("#1F6FB2", "#B8590B", "#5B6770")
_STATED_DASHES = ("", "14 4", "3 3")
_PREDICTED_DASHES = ("6 5", "1 6", "10 3 2 3")


def _mono(day: object) -> str:
    return f'<span class="mono nowrap">{_esc(str(day))}</span>'


def _day_list(days: Sequence[date]) -> str:
    named = ", ".join(_mono(day) for day in days[:_NAMED_DAYS])
    more = len(days) - _NAMED_DAYS
    return named + (f" and {more} more" if more > 0 else "")


def _per_cent(share: float) -> str:
    text = f"{share * 100:.1f}".removesuffix(".0")
    return f"{text} per cent"


def _oxford(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _href(ref: str, start: date | None = None, end: date | None = None) -> str:
    query = f"ref={quote(ref, safe='')}"
    if start is not None and end is not None:
        query += f"&from={start.isoformat()}&to={end.isoformat()}"
    return _esc(f"/balance-chart?{query}")


# ---------------------------------------------------------------------------
# The structure in words. Said once, here: the ledger page links to it.


def _levels_html(structure: Any) -> str:
    longest = ", ".join(
        f"{_mono(level.first_day)} until {_mono(level.until)} "
        f"({_plural(level.balances, 'stated balance')})"
        for level in structure.longest_levels
    )
    return (
        f"<p>Between the changes the difference holds {_plural(len(structure.levels), 'level')}, "
        "and within a level the day-to-day movements agree and only the level is offset. "
        f"The longest: {longest}.</p>"
    )


def _gaps_sentence(structure: Any) -> str:
    return ", ".join(
        f"{label} {count}"
        for (label, _), count in zip(GAP_BUCKETS, structure.gap_counts, strict=True)
    )


def _rhythm_words(size_class: Any) -> str:
    if size_class.rhythm == MONTHLY:
        same = ", always on the same day of the month" if size_class.same_day_of_month else ""
        return f"monthly{same}"
    if size_class.rhythm == IRREGULAR:
        return "at no regular interval"
    return "weekly"


def _structure_html(structure: Any) -> str:
    """One series' structure as sentences: counts, dates, class letters, no figure."""
    balances = structure.balances
    if not balances:
        return "<p>No stated balance is held to judge.</p>"
    if not structure.steps:
        return (
            "<p>The difference is nil at "
            + (
                "the only stated balance"
                if balances == 1
                else f"all {_plural(balances, 'stated balance')}"
            )
            + f": one level, and {_per_cent(1.0)} of the stated "
            f"{'balance moves' if balances == 1 else 'balances move'} from the one before as "
            "the rows do.</p>"
        )
    steps = len(structure.steps)
    body = (
        f"<p>{structure.agreeing} of the {_plural(balances, 'stated balance')} "
        f"({_per_cent(structure.agreeing / balances)}) "
        f"{agree(structure.agreeing, 'moves')} from the one before by exactly "
        f"what the rows move by; the other {steps} {'is' if steps == 1 else 'are'} the "
        f"{_plural(steps, 'change')} in the difference.</p>" + _levels_html(structure)
    )
    if structure.pairs:
        body += (
            f"<p>{_plural(structure.pairs, 'pair')} of changes undo each other exactly: the "
            "same money counted on different days by the two sides, so each pair is one "
            f"timing fault and not two. Gaps between the two days: {_gaps_sentence(structure)}."
            f" {structure.unpaired} {'change is' if structure.unpaired == 1 else 'changes are'}"
            " left unpaired.</p>"
        )
    else:
        body += (
            "<p>No change is undone by a later change of exactly the opposite size, so none "
            "looks like the same money counted on different days.</p>"
        )
    for size_class in structure.classes:
        body += (
            f"<p>Size class {_esc(size_class.letter)} recurs {size_class.members} times, "
            f"{_rhythm_words(size_class)}: {_day_list(size_class.days)}. Every one is exactly "
            "the same size, which is shown on the values page only.</p>"
        )
    # Read from the steps, not the record's properties: a masked view exposes fields only.
    permanent = [structure.steps[number] for number in structure.permanent]
    if permanent:
        body += (
            f"<p>With the pairs cancelled, {_plural(len(permanent), 'permanent change')} "
            f"{'remains' if len(permanent) == 1 else 'remain'} "
            f"({_day_list([step.day for step in permanent])}), and together "
            f"{'it equals' if len(permanent) == 1 else 'they equal'} the present difference "
            "exactly.</p>"
        )
        if not structure.exact:
            body += (
                '<p class="bad">They do not equal the present difference, which cannot '
                "happen: the walk's own arithmetic is wrong.</p>"
            )
        held = [step for step in permanent if step.unheld]
        if held:
            body += (
                f"<p>{len(held)} of them coincide with a transfer leg to a Space whose rows are "
                f"not held ({_day_list([step.day for step in held])}).</p>"
            )
        explained = [step for step in permanent if step.explained and not step.unheld]
        if explained:
            body += (
                f"<p>{len(explained)} of them are explained by the exact arithmetic above "
                f"({_day_list([step.day for step in explained])}).</p>"
            )
    else:
        body += "<p>With the pairs cancelled, nothing remains: the present difference is nil.</p>"
    return body


def structure_summary_html(report: Any, ref: str, *, scope: str = WHOLE) -> str:
    """The summary the ledger page shows, with the link to the timeline.

    `report` is a `StructureReport`, wrapped or not: it holds no figure the
    masked rendering would have to withhold.
    """
    whose = "whole account's" if scope == WHOLE else "account's own"
    body = (
        f"<h4>The structure of the {whose} differences</h4>"
        + _structure_html(report.whole)
        + f'<p><a class="tap" href="{_href(ref)}" target="_blank" rel="noopener">'
        "Timeline of the differences (opens in a new tab)</a></p>"
    )
    for each in report.by_source:
        body += (
            f"<details><summary>Stated by {_esc(each.source)} alone</summary>"
            + _structure_html(each.structure)
            + "</details>"
        )
    return body


# ---------------------------------------------------------------------------
# Geometry shared by the two charts.


@dataclass(frozen=True)
class Scale:
    start: date
    end: date
    per_day: float

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def width(self) -> float:
        return 2 * EDGE + self.days * self.per_day

    def x(self, day: date) -> float:
        return EDGE + (day - self.start).days * self.per_day


def choose_scale(
    first: date, last: date, start: date | None, end: date | None
) -> Scale:
    """The days to draw and the pixels each takes (see the module docstring)."""
    if start is None or end is None:
        return Scale(first, max(first, last), PIXELS_PER_DAY)
    days = (end - start).days + 1
    per_day = min(MAX_PIXELS_PER_DAY, max(PIXELS_PER_DAY, TARGET_RANGE_WIDTH / days))
    return Scale(start, end, per_day)


def _months(scale: Scale) -> list[date]:
    found = []
    current = scale.start.replace(day=1)
    while current <= scale.end:
        if current >= scale.start:
            found.append(current)
        current = (current + timedelta(days=32)).replace(day=1)
    return found


def _label(x: float, y: float, text: str, extra: str = "") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="11" fill="currentColor"{extra}>'
        f"{_esc(text)}</text>"
    )


def _axis(scale: Scale, *, month_y: Sequence[float]) -> str:
    """Month ticks, each labelled with its month AND year, so any screenful says
    the date, and day labels where the scale has the room."""
    parts: list[str] = []
    per_day = scale.per_day
    for first in _months(scale):
        x = scale.x(first)
        text = f"{first:%b} {first.year}"
        for y in month_y:
            parts.append(_label(x + 3, y, text))
    step = 1 if per_day >= 60 else 7 if per_day >= 14 else 0
    if step:
        for offset in range(scale.days):
            day = scale.start + timedelta(days=offset)
            if (day.day - 1) % step == 0 and day.day != 1:
                parts.append(
                    _label(scale.x(day) + 2, month_y[0] + 13, str(day.day),
                           ' fill-opacity=".7"')
                )
    return "".join(parts)


def _svg(width: float, height: float, title_id: str, title: str, desc: str, body: str) -> str:
    return (
        f'<svg role="img" aria-labelledby="{title_id}-t {title_id}-d" '
        f'width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        'xmlns="http://www.w3.org/2000/svg" style="display:block;max-width:none">'
        f'<title id="{title_id}-t">{_esc(title)}</title>'
        f'<desc id="{title_id}-d">{_esc(desc)}</desc>' + body + "</svg>"
    )


def _scroller(label_svg: str, chart_svg: str, name: str) -> str:
    """The fixed label column beside the sideways-scrolling chart."""
    return (
        '<div style="display:flex;align-items:flex-start;margin:.6rem 0">'
        f'<div style="flex:0 0 auto">{label_svg}</div>'
        '<div tabindex="0" role="region" '
        f'aria-label="{_esc(name)}, scrolls sideways" '
        f'style="flex:1 1 0;min-width:0;overflow-x:auto">{chart_svg}</div></div>'
    )


# ---------------------------------------------------------------------------
# The masked timeline.


@dataclass(frozen=True)
class _Row:
    name: str
    kind: str
    changes: list[Change]


def _rows(structure: FaultStructure, changes: Sequence[Change]) -> list[_Row]:
    """The rows that hold a change in range, in the order they are drawn."""
    rows: list[_Row] = []
    pairs = [c for c in changes if c.kind == TRANSIENT]
    if pairs:
        rows.append(_Row("Timing pairs", TRANSIENT, pairs))
    for size_class in structure.classes:
        members = [c for c in changes if c.size_class == size_class.letter]
        if members:
            rows.append(_Row(f"Size class {size_class.letter}", RECURRING, members))
    for kind in (UNEXPLAINED, UNHELD, EXPLAINED):
        found = [c for c in changes if c.kind == kind]
        if found:
            rows.append(_Row(KINDS[kind][0], kind, found))
    return rows


class _Plot:
    """Days to horizontal drawing units, for one range."""

    def __init__(self, start: date, end: date) -> None:
        self.start, self.end = start, end
        self.days = (end - start).days + 1
        self.width = PLOT_RIGHT - PLOT_LEFT

    def edge(self, day: date) -> float:
        """The left edge of `day`; a day after the range's last is its right edge."""
        return PLOT_LEFT + (day - self.start).days / self.days * self.width

    def centre(self, first: date, last: date) -> float:
        return (self.edge(first) + self.edge(last + timedelta(days=1))) / 2


def _mark(kind: str, x: float, y: float, width: float, *, extra: str, hollow: bool = False) -> str:
    colour = KINDS[kind][1]
    paint = (
        f'fill="none" stroke="{colour}" stroke-width="2"'
        if hollow
        else f'fill="{colour}" stroke="currentColor" stroke-width="1"'
    )
    return (
        f'<rect x="{x - width / 2:.1f}" y="{y:.1f}" width="{width:.1f}" '
        f'height="{MARK_HEIGHT}" {paint}{extra}/>'
    )


def _span_words(first: date, last: date) -> str:
    return str(first) if first == last else f"{first} to {last}"


def _levels_band(structure: FaultStructure, plot: _Plot, y: float) -> str:
    """The levels as blocks that alternate in shade, with a divider at each boundary."""
    parts: list[str] = []
    drawn = 0
    last_level = len(structure.levels) - 1
    for index, level in enumerate(structure.levels):
        stop = level.last_day + timedelta(days=1) if index == last_level else level.until
        first, after = max(level.first_day, plot.start), min(stop, plot.end + timedelta(days=1))
        if after <= first:
            continue
        left, right = plot.edge(first), plot.edge(after)
        if drawn:
            parts.append(
                f'<line class="level-divider" x1="{left:.1f}" y1="{y - 3:.1f}" '
                f'x2="{left:.1f}" y2="{y + MARK_HEIGHT + 3:.1f}" stroke="currentColor" '
                'stroke-opacity=".75" stroke-width=".7"/>'
            )
        parts.append(
            f"<g><title>Level {index + 1}: stated balances from {level.first_day} to "
            f"{level.last_day} ({level.balances}); the difference does not change across "
            "them.</title>"
            f'<rect class="level" x="{left:.1f}" y="{y:.1f}" '
            f'width="{max(right - left, .5):.1f}" height="{MARK_HEIGHT}" fill="currentColor" '
            f'fill-opacity="{".34" if drawn % 2 == 0 else ".12"}"/></g>'
        )
        drawn += 1
    return "".join(parts)


def _tick(x: float, top: float, bottom: float, opacity: str) -> str:
    return (
        f'<line x1="{x:.1f}" y1="{top:.1f}" x2="{x:.1f}" y2="{bottom:.1f}" '
        f'stroke="currentColor" stroke-opacity="{opacity}"/>'
    )


def _tick_label(x: float, y: float, text: str, extra: str = "") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="10" fill="currentColor"{extra}>'
        f"{_esc(text)}</text>"
    )


def _fitted_axis(plot: _Plot, *, bottom: float) -> str:
    """Year or month labels above, month or day labels below, and a faint line
    at each; a label is left out where its stretch is too narrow to hold it."""
    parts: list[str] = []
    long_view = plot.days > _SHORT_VIEW_DAYS
    # The upper line names the year, or for a short view the month and year.
    day = plot.start
    while day <= plot.end:
        if long_view:
            first, last = calendar_span(YEAR, day)
            text, need = str(day.year), 26
        else:
            first, last = calendar_span(MONTH, day)
            text, need = f"{day:%b %Y}", 48
        shown_first, shown_last = max(first, plot.start), min(last, plot.end)
        left = plot.edge(shown_first)
        parts.append(_tick(left, 4, bottom, ".4"))
        if ((shown_last - shown_first).days + 1) / plot.days * plot.width >= need:
            parts.append(_tick_label(left + 2, 11, text, ' font-weight="700"'))
        day = last + timedelta(days=1)
    # The lower line names months for a view of months and days for a short one;
    # a view of years has none.
    if long_view and plot.days <= _YEARS_VIEW_DAYS:
        months = _month_starts(plot)
        every = 1 if len(months) <= _MONTHS_LABELLED_ALL else 3
        for first in months:
            parts.append(_tick(plot.edge(first), 14, bottom, ".12"))
            if (first.month - 1) % every == 0:
                parts.append(_tick_label(plot.edge(first) + 2, 23, f"{first:%b}", _DIM))
    elif not long_view:
        every_day = plot.days <= _EVERY_DAY_LABELLED
        for offset in range(plot.days):
            day = plot.start + timedelta(days=offset)
            if every_day or ((day.day - 1) % 7 == 0 and day.day < 29):
                parts.append(_tick(plot.edge(day), 14, bottom, ".12"))
                parts.append(_tick_label(plot.edge(day) + 2, 23, str(day.day), _DIM))
    return "".join(parts)


def _month_starts(plot: _Plot) -> list[date]:
    return [
        first
        for first in (
            date(year, month, 1)
            for year in range(plot.start.year, plot.end.year + 1)
            for month in range(1, 13)
        )
        if plot.start <= first <= plot.end
    ]


def _kind_phrase(kind: str, count: int) -> str:
    """A count of changes of one kind, in words."""
    if kind == TRANSIENT:
        return _plural(count, "timing pair")
    return f"{count} " + {
        RECURRING: "of a recurring size",
        UNEXPLAINED: "unexplained",
        UNHELD: "beside an unheld Space",
        EXPLAINED: "explained",
    }[kind]


def _range_summary(changes: Sequence[Change], start: date, end: date) -> str:
    """What the range holds, in one line, before any chart."""
    if not changes:
        return (
            f"<p><strong>No change falls in this range</strong>, {_mono(start)} to "
            f"{_mono(end)}: the stated balances agree with the rows throughout it.</p>"
        )
    counts = count_by_kind(changes)
    held = [_kind_phrase(kind, counts[kind]) for kind in KINDS if counts[kind]]
    return (
        f"<p><strong>{_plural(len(changes), 'change')} in this range</strong>, {_mono(start)} "
        f"to {_mono(end)}: {_oxford(held)}; the first on {_mono(changes[0].day)}, the last on "
        f"{_mono(changes[-1].day)}.</p>"
    )


def _longest_level_sentence(structure: FaultStructure, start: date, end: date) -> str:
    """The longest level in view, with its span in words and not on the strip."""
    best: tuple[int, date, date] | None = None
    last_level = len(structure.levels) - 1
    for index, level in enumerate(structure.levels):
        stop = level.last_day if index == last_level else level.until
        first, until = max(level.first_day, start), min(stop, end)
        days = (until - first).days
        if days > 0 and (best is None or days > best[0]):
            best = (days, first, until)
    if best is None:
        return ""
    days, first, until = best
    return (
        f"<p>The longest level in this range runs from {_mono(first)} until {_mono(until)} "
        f"({_plural(days, 'day')}): the difference does not change across it.</p>"
    )


def _fitted_strip(
    structure: FaultStructure, changes: Sequence[Change], plot: _Plot
) -> tuple[str, str]:
    """(chart, unit of its bins). No element's geometry depends on a size: marks
    are one height and width, and joins depend only on dates."""
    unit = choose_bin_unit(plot.days, plot.width)
    mark_width = min(
        _MARK_WIDEST,
        max(_MARK_NARROWEST, nominal_bin_width(unit, plot.days, plot.width) - 2),
    )
    rows = _rows(structure, changes)
    top = 32
    height = top + ROW_HEIGHT * (len(rows) + 1) + 4
    pad = (ROW_HEIGHT - MARK_HEIGHT) / 2
    body = [_fitted_axis(plot, bottom=height - 2)]
    body.append(_label(4, top + ROW_HEIGHT / 2 + 4, "Levels"))
    body.append(_levels_band(structure, plot, top + pad))
    for number, row in enumerate(rows, start=1):
        y0 = top + number * ROW_HEIGHT
        mark_y = y0 + ROW_HEIGHT - MARK_HEIGHT - 6
        body.append(_label(4, y0 + ROW_HEIGHT / 2 + 8, row.name))
        body.append(
            f'<line x1="2" y1="{y0 + ROW_HEIGHT:.1f}" x2="{VIEW_WIDTH - 2}" '
            f'y2="{y0 + ROW_HEIGHT:.1f}" stroke="currentColor" stroke-opacity=".1"/>'
        )
        for found in bins_of([c.day for c in row.changes], unit, plot.start, plot.end):
            x = plot.centre(found.first, found.last)
            noun = KINDS[row.kind][0].lower()
            body.append(
                f"<g><title>{_span_words(found.first, found.last)}: "
                f"{_plural(found.count, 'change')}, {noun}.</title>"
                + _mark(
                    row.kind, x, mark_y, mark_width,
                    extra=(
                        f' class="mark" data-kind="{row.kind}" data-bin="{found.first}" '
                        f'data-count="{found.count}"'
                    ),
                )
                + (
                    _tick_label(x, mark_y - 3, str(found.count), ' text-anchor="middle"')
                    if found.count > 1
                    else ""
                )
                + "</g>"
            )
        if row.kind != TRANSIENT:
            continue
        for change in row.changes:
            if change.partner_day is None:
                continue
            mid = mark_y + MARK_HEIGHT / 2
            begins = plot.centre(change.day, change.day)
            reaches = min(change.partner_day, plot.end)
            ends = plot.centre(reaches, reaches)
            cap = ""
            if change.partner_day <= plot.end:
                cap = _mark(
                    row.kind, ends, mark_y, mark_width, hollow=True,
                    extra=f' class="cap" data-kind="{row.kind}"',
                )
            body.append(
                f"<g><title>Timing pair: {change.day} and {change.partner_day}, "
                f"{(change.partner_day - change.day).days} days apart. The two undo each other "
                "exactly.</title>"
                f'<path class="join" d="M{begins:.1f},{mid:.1f} H{ends:.1f}" '
                f'stroke="{KINDS[row.kind][1]}" stroke-width="2" fill="none"/>' + cap + "</g>"
            )
    desc = (
        f"From {plot.start} to {plot.end}: {_plural(len(changes), 'change')} of the "
        f"difference in {_plural(len(rows), 'row')} by kind, and the levels between them. "
        f"Each mark stands for the changes in one {unit}; a number beside it is how many. "
        "Marks are all one size; sizes are not drawn."
    )
    chart = (
        '<svg role="img" aria-labelledby="bc-strip-t bc-strip-d" width="100%" '
        f'viewBox="0 0 {VIEW_WIDTH} {height:.0f}" data-plot-left="{PLOT_LEFT}" '
        f'data-plot-right="{PLOT_RIGHT}" xmlns="http://www.w3.org/2000/svg" '
        'style="display:block;margin:.6rem 0;max-width:40rem">'
        '<title id="bc-strip-t">Timeline of the changes in the difference</title>'
        f'<desc id="bc-strip-d">{_esc(desc)}</desc>' + "".join(body) + "</svg>"
    )
    return chart, unit


def _counts_table(
    ref: str, changes: Sequence[Change], start: date, end: date
) -> str:
    """Counts per period, each period a link that opens it, and the empty ones in a line."""
    unit = period_unit((end - start).days + 1)
    periods = periods_of(changes, unit, start, end)
    kinds = [k for k in KINDS if any(p.counts[k] for p in periods)]
    held = [p for p in periods if p.total]
    head = "<tr><th>Period</th>" + "".join(f"<th>{_esc(KINDS[k][0])}</th>" for k in kinds) + "</tr>"
    rows = "".join(_period_row(ref, p, kinds) for p in held)
    return (
        "<h3>Where in time the changes are</h3>"
        f"<table>{head}{rows}</table>"
        + _nothing_line(periods, unit)
    )


def _period_row(ref: str, period: Period, kinds: Sequence[str]) -> str:
    cells = "".join(f"<td>{period.counts[k] or ''}</td>" for k in kinds)
    return (
        f'<tr><td><a class="tap" href="{_href(ref, period.first, period.last)}">'
        f"{_esc(period.label)}</a></td>{cells}</tr>"
    )


def _nothing_line(periods: Sequence[Period], unit: str) -> str:
    """The periods that hold nothing, in one line: a run of three or more is one item."""
    items: list[str] = []
    for first, last in empty_runs(periods):
        run = [p for p in periods if first.first <= p.first <= last.first]
        if len(run) >= 3:
            items.append(
                f"from {period_label(unit, first.first)} to {period_label(unit, last.first)}"
                if unit == DAY
                else f"{period_label(unit, first.first)} to {period_label(unit, last.first)}"
            )
        else:
            items.extend(
                f"on {period_label(unit, p.first)}" if unit == DAY else period_label(unit, p.first)
                for p in run
            )
    if not items:
        return ""
    lead = "Nothing" if unit == DAY else "Nothing in"
    return f"<p>{lead} {_esc(_oxford(items))}.</p>"


def _kind_legend(kinds: Sequence[str]) -> str:
    items = "".join(
        f'<li><svg width="14" height="14" aria-hidden="true" style="vertical-align:middle">'
        f'<rect x="1" y="1" width="12" height="12" fill="{KINDS[kind][1]}" '
        'stroke="currentColor"/></svg> '
        f"<strong>{_esc(KINDS[kind][0])}</strong>: {_esc(KINDS[kind][2])}.</li>"
        for kind in kinds
    )
    return f'<ul class="legend">{items}</ul>'


# ---------------------------------------------------------------------------
# The values chart.


def _pounds(minor: int, *, signed: bool = False, pence: bool = True) -> str:
    whole, rest = divmod(abs(minor), 100)
    sign = "-" if minor < 0 else "+" if signed and minor > 0 else ""
    body = f"{whole:,}.{rest:02d}" if pence else f"{whole:,}"
    return f"{sign}£{body}"


def nice_ticks(low: int, high: int, target: int = 5) -> list[int]:
    """Round figures, in minor units, that cover `low` to `high`.

    The step is one, two, or five of a power of ten, and never under a pound.
    """
    span = max(high - low, 100)
    raw = span / target
    magnitude = 10 ** floor(log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    step = max(int(step), 100)
    first = floor(low / step) * step
    ticks = [first]
    while ticks[-1] < high:
        ticks.append(ticks[-1] + step)
    return ticks


def _held_series(
    days: Sequence[date], values: Sequence[int], scale: Scale
) -> list[tuple[date, int]]:
    """The points to draw: the balance held at the start of the range, then those
    inside it. A balance holds until the next is stated."""
    before: tuple[date, int] | None = None
    inside: list[tuple[date, int]] = []
    for day, value in zip(days, values, strict=True):
        if day <= scale.start:
            before = (day, value)
        elif day <= scale.end:
            inside.append((day, value))
    return ([(scale.start, before[1])] if before else []) + inside


def _step_path(series: Sequence[tuple[date, int]], scale: Scale, y: Any) -> str:
    """One path for a whole line: a balance holds until the next one is stated."""
    if not series:
        return ""
    first_day, first_value = series[0]
    parts = [f"M{scale.x(first_day):.1f},{y(first_value):.1f}"]
    for day, value in series[1:]:
        parts.append(f"H{scale.x(day):.1f}V{y(value):.1f}")
    parts.append(f"H{scale.x(scale.end) + scale.per_day:.1f}")
    return "".join(parts)


def _shape(kind: str, x: float, y: float) -> str:
    """A marker told apart by its shape as well as its colour: a filled circle for a
    timing pair, a diamond for a recurring size, a square for an unexplained step, a
    triangle for an unheld Space, and a ring for one the ledger explained."""
    colour = KINDS[kind][1]
    hollow = kind == EXPLAINED
    common = (
        f'class="mark" data-kind="{kind}" fill="{"none" if hollow else colour}" '
        f'stroke="{colour if hollow else "currentColor"}" stroke-width="{2.5 if hollow else 1}"'
    )
    if kind in (TRANSIENT, EXPLAINED):
        return f'<circle {common} cx="{x:.1f}" cy="{y:.1f}" r="5"/>'
    if kind == RECURRING:
        points = f"{x:.1f},{y - 6:.1f} {x + 6:.1f},{y:.1f} {x:.1f},{y + 6:.1f} {x - 6:.1f},{y:.1f}"
        return f'<polygon {common} points="{points}"/>'
    if kind == UNHELD:
        points = f"{x:.1f},{y - 6:.1f} {x + 6:.1f},{y + 5:.1f} {x - 6:.1f},{y + 5:.1f}"
        return f'<polygon {common} points="{points}"/>'
    return f'<rect {common} x="{x - 5:.1f}" y="{y - 5:.1f}" width="10" height="10"/>'


def _legend_line(dash: str, colour: str, width: float) -> str:
    dashes = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        '<svg width="46" height="10" aria-hidden="true" style="vertical-align:middle">'
        f'<line x1="1" y1="5" x2="45" y2="5" stroke="{colour}" stroke-width="{width}"'
        f'{dashes}/></svg>'
    )


def _legend_shape(kind: str) -> str:
    return (
        '<svg width="16" height="16" aria-hidden="true" style="vertical-align:middle">'
        + _shape(kind, 8, 8).replace('class="mark" ', "")
        + "</svg>"
    )


def _values_svgs(chart: BalanceChart, scale: Scale) -> tuple[str, str, str]:
    """(figure column, chart, description) of the values chart, from the record itself."""
    structure = chart.structure.whole if chart.structure else None
    top, top_h, gap, bottom_h, foot = 36, 260, 50, 150, 30
    p1 = (top, top + top_h)
    p2 = (p1[1] + gap, p1[1] + gap + bottom_h)
    height = p2[1] + foot

    drawn: list[tuple[SourceLine, list[tuple[date, int]], list[tuple[date, int]]]] = [
        (
            line,
            _held_series(line.days, line.stated, scale),
            _held_series(line.days, line.predicted, scale),
        )
        for line in chart.lines
    ]
    levels = [value for _, a, b in drawn for _, value in (*a, *b)] or [0]
    ticks1 = nice_ticks(min(levels), max(levels))
    diffs = _held_series(chart.days, chart.differences, scale)
    flat = [value for _, value in diffs] or [0]
    ticks2 = nice_ticks(min(0, *flat), max(0, *flat), target=3)
    whole_pounds = all(t % 100 == 0 for t in (*ticks1, *ticks2))

    def mapper(ticks: list[int], panel: tuple[float, float]) -> Any:
        low, high = ticks[0], ticks[-1]
        span = max(high - low, 1)
        return lambda value: panel[1] - (value - low) / span * (panel[1] - panel[0])

    y1, y2 = mapper(ticks1, p1), mapper(ticks2, p2)
    body = [_axis(scale, month_y=(14, p1[1] + gap / 2 + 4, height - 8))]
    figures: list[str] = [
        _label(4, p1[0] - 18, "Balance", ' font-weight="700"'),
        _label(4, p2[0] - 18, "Difference", ' font-weight="700"'),
    ]
    for ticks, y in ((ticks1, y1), (ticks2, y2)):
        for tick in ticks:
            body.append(
                f'<line x1="0" y1="{y(tick):.1f}" x2="{scale.width:.1f}" y2="{y(tick):.1f}" '
                f'stroke="currentColor" stroke-opacity="{".5" if tick == 0 else ".14"}"/>'
            )
            figures.append(
                _label(AXIS_WIDTH - 6, y(tick) + 4, _pounds(tick, pence=not whole_pounds),
                       ' text-anchor="end"')
            )
    for month in _months(scale):
        x = scale.x(month)
        for panel in (p1, p2):
            body.append(
                f'<line x1="{x:.1f}" y1="{panel[0]}" x2="{x:.1f}" y2="{panel[1]}" '
                f'stroke="currentColor" stroke-opacity="{".28" if month.month == 1 else ".07"}"/>'
            )
    for index, (line, stated, predicted) in enumerate(drawn):
        colour = _SOURCE_COLOURS[index % len(_SOURCE_COLOURS)]
        sdash = _STATED_DASHES[index % len(_STATED_DASHES)]
        pdash = _PREDICTED_DASHES[index % len(_PREDICTED_DASHES)]
        sd = f' stroke-dasharray="{sdash}"' if sdash else ""
        body.append(
            f'<path data-series="stated" data-source="{_esc(line.source)}" '
            f'd="{_step_path(stated, scale, y1)}" fill="none" stroke="{colour}" '
            f'stroke-width="2.5"{sd}><title>Stated by {_esc(line.source)}</title></path>'
            f'<path data-series="predicted" data-source="{_esc(line.source)}" '
            f'd="{_step_path(predicted, scale, y1)}" fill="none" stroke="{colour}" '
            f'stroke-width="1.5" stroke-dasharray="{pdash}">'
            f"<title>Predicted by the rows at the days {_esc(line.source)} states</title></path>"
        )
    body.append(
        f'<path data-series="difference" d="{_step_path(diffs, scale, y2)}" fill="none" '
        'stroke="currentColor" stroke-width="2"><title>Stated minus predicted</title></path>'
    )
    if structure is not None:
        for step in structure.steps:
            if scale.start <= step.day <= scale.end:
                level = chart.differences[step.position]
                body.append(
                    f"<g><title>{step.day}: {_esc(KINDS[step.kind][0])}, "
                    f"{_pounds(step.size_minor, signed=True)}</title>"
                    + _shape(step.kind, scale.x(step.day), y2(level))
                    + "</g>"
                )
    desc = (
        f"From {scale.start} to {scale.end}. "
        + " ".join(
            f"{line.source}: stated balances from {_pounds(min(line.stated))} to "
            f"{_pounds(max(line.stated))}."
            for line in chart.lines
            if line.stated
        )
        + f" The difference ranges from {_pounds(min(0, *flat))} to {_pounds(max(0, *flat))}."
    )
    figure_svg = _svg(
        AXIS_WIDTH, height, "bc-axis", "Figures for the vertical axes",
        "Balance figures above, difference figures below.", "".join(figures),
    )
    chart_svg = _svg(
        scale.width, height, "bc-values",
        "Stated balances, the balances the rows predict, and their difference", desc,
        "".join(body),
    )
    return figure_svg, chart_svg, desc


def _values_legend(chart: BalanceChart, kinds: Sequence[str]) -> str:
    items = []
    for index, line in enumerate(chart.lines):
        colour = _SOURCE_COLOURS[index % len(_SOURCE_COLOURS)]
        items.append(
            f"<li>{_legend_line(_STATED_DASHES[index % 3], colour, 2.5)} The balance "
            f"<strong>{_esc(line.source)}</strong> states, held until its next one.</li>"
            f"<li>{_legend_line(_PREDICTED_DASHES[index % 3], colour, 1.5)} The balance the rows "
            f"predict at the same days, for <strong>{_esc(line.source)}</strong>.</li>"
        )
    items.append(
        f"<li>{_legend_line('', 'currentColor', 2)} The difference: stated minus predicted, "
        "in the lower panel. A flat line away from nil is a constant offset; a bump that "
        "returns is a timing fault.</li>"
    )
    items.extend(
        f"<li>{_legend_shape(kind)} <strong>{_esc(KINDS[kind][0])}</strong>: "
        f"{_esc(KINDS[kind][2])}.</li>"
        for kind in kinds
    )
    return f'<ul class="legend" style="list-style:none;padding-left:0">{"".join(items)}</ul>'


def _change_links(ref: str, changes: Sequence[Change]) -> str:
    """A posted link per change, each opening the values chart around that change.

    A GET link would answer masked, so each is a form, as the ledger's month
    steps are while values are shown.
    """
    if not changes:
        return ""
    around = timedelta(days=_CHANGE_WINDOW_DAYS)
    forms = "".join(
        '<form method="post" action="/balance-chart" '
        f'data-change="{change.day}">'
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="from" value="{change.day - around}">'
        f'<input type="hidden" name="to" value="{change.last_day + around}">'
        f'<button class="tap" type="submit">{change.day}, '
        f"{_esc(KINDS[change.kind][0].lower())}</button></form>"
        for change in changes[:_CHANGE_LINKS]
    )
    more = len(changes) - _CHANGE_LINKS
    return (
        "<p>Open the values chart around a change:</p>"
        f'<div class="monthnav">{forms}</div>'
        + (f"<p>and {more} more, in the steps table below.</p>" if more > 0 else "")
    )


def _steps_table(structure: FaultStructure, scale: Scale) -> str:
    rows = []
    for step in structure.steps:
        if not scale.start <= step.day <= scale.end:
            continue
        partner = structure.steps[step.partner].day if step.partner >= 0 else None
        rows.append(
            "<tr>"
            f"<td>{_mono(step.day)}</td>"
            f'<td class="mono nowrap">{_esc(_pounds(step.size_minor, signed=True))}</td>'
            f"<td>{_esc(KINDS[step.kind][0])}</td>"
            f"<td>{_mono(partner) if partner else '-'}</td>"
            f"<td>{step.gap_days if partner else '-'}</td>"
            f"<td>{_esc(step.size_class) or '-'}</td>"
            f"<td>{_esc(step.source)}</td>"
            f"<td>{'Space leg' if step.unheld else ''}"
            f"{'explained' if step.explained else ''}</td>"
            "</tr>"
        )
    if not rows:
        return "<p>No step falls in this range.</p>"
    head = (
        "<tr><th>Day</th><th>Size</th><th>Kind</th><th>Pair with</th><th>Gap, days</th>"
        "<th>Size class</th><th>Stated by</th><th>Ledger</th></tr>"
    )
    return f'<div class="scroll"><table>{head}{"".join(rows)}</table></div>'


# ---------------------------------------------------------------------------
# The pages.

_HOME = '<p><a class="tap" href="/">Back to overview</a></p>'


def _links(ref: str) -> str:
    return (
        f'<p><a class="tap" href="/ledger?ref={quote(ref, safe="")}">Back to the ledger</a></p>'
        + _HOME
    )


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


def _heading(chart: Any) -> str:
    """The scope as a title, short enough to leave the chart on the first screen."""
    if chart.scope == WHOLE:
        return "<h2>The whole account: the main account and its Spaces together</h2>"
    return "<h2>This account's own stated balances</h2>"


def _scope_note(chart: Any) -> str:
    if chart.scope == WHOLE:
        spaces = ", ".join(_esc(space) for space in chart.spaces)
        return (
            "<p>The balances are stated by sources that cannot see the Spaces, so they are "
            "checked against the rows of the main account and every Space"
            + (f" ({spaces})" if spaces else "")
            + ", where a transfer between them cancels.</p>"
        )
    return "<p>Each balance is checked against this account's own rows.</p>"


def _mode(view: Any, unmasked: bool, start: date | None, end: date | None) -> str:
    ref = view.ref
    if unmasked:
        return (
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button secondary" href="{_href(ref, start, end)}">'
            "Hide values (masked timeline)</a></p>"
        )
    fields = f'<input type="hidden" name="ref" value="{_esc(ref)}">' + (
        f'<input type="hidden" name="from" value="{start.isoformat()}">'
        f'<input type="hidden" name="to" value="{end.isoformat()}">'
        if start is not None and end is not None
        else ""
    )
    return (
        '<p class="muted">This timeline draws no size: every mark is the same shape, and '
        "the rows say what kind of change each is. Sizes are figures, so they appear only "
        "on the values chart, which opens in a new tab on request.</p>"
        f'<form method="post" action="/balance-chart" target="_blank">{fields}'
        + submit_button("Show values (opens in a new tab)")
        + "</form>"
    )


def parse_range(start: str, end: str) -> tuple[date | None, date | None]:
    """A requested `from` and `to`, both or neither. Every refusal is a fixed
    sentence, as the anchor form's are: a page reachable by an address must not
    echo what was typed."""
    if not start.strip() and not end.strip():
        return None, None
    if not (start.strip() and end.strip()):
        raise DataError("a range needs both a from date and a to date")
    try:
        first, last = date.fromisoformat(start.strip()), date.fromisoformat(end.strip())
    except ValueError:
        raise DataError("a date is written YYYY-MM-DD, and must exist") from None
    if last < first:
        raise DataError("the range ends before it starts")
    if (last - first).days >= MAX_RANGE_DAYS:
        raise DataError("the range is longer than forty years")
    return first, last


def render_balance_chart(
    chart: BalanceChart,
    *,
    unmasked: bool,
    start: date | None = None,
    end: date | None = None,
) -> bytes:
    view = Disclosed(chart, unmasked=unmasked)
    name = view.label or view.ref
    body = f"<p><strong>{_esc(name)}</strong></p>"
    if view.state == "unknown":
        return render_page(
            "Balance differences",
            body + '<p class="bad"><strong>Unknown account.</strong> Nothing is held under this '
            "reference and no account is declared with it.</p>" + _HOME,
        )
    if view.state == "withheld":
        return render_page(
            "Balance differences",
            body + f'<p class="warn">Not walked: {_esc(view.withheld)}.</p>' + _links(view.ref),
        )
    if view.state == "no-balances" or chart.first_day is None or chart.last_day is None:
        return render_page(
            "Balance differences",
            body + "<p>No balance is stated for this account, so there is nothing to compare "
            "the rows with.</p>" + _links(view.ref),
        )
    structure = chart.structure.whole if chart.structure is not None else None
    if structure is None:  # pragma: no cover - a drawn chart always has one
        return _page("Balance differences", "No structure was built for this account.")
    scale = choose_scale(chart.first_day, chart.last_day, start, end)
    changes = changes_in(structure, scale.start, scale.end)
    body += _heading(view)
    everything = (
        ""
        if start is None
        else f'<p><a class="tap" href="{_href(view.ref)}">Draw everything held</a></p>'
    )
    if unmasked:
        body += (
            _scope_note(view)
            + _mode(view, unmasked, start, end)
            + f"<p>Drawn from {_mono(scale.start)} to {_mono(scale.end)} at "
            f"{scale.per_day:.2f} pixels a day.</p>"
            + everything
            + _range_summary(changes, scale.start, scale.end)
            + _change_links(view.ref, changes)
        )
        figures, drawing, _ = _values_svgs(chart, scale)
        kinds = [k for k in KINDS if any(c.kind == k for c in changes)]
        body += (
            f"<p>The present difference is <strong>{_esc(_pounds(structure.present_minor))}"
            f"</strong>, the sum of {_plural(len(structure.permanent), 'permanent change')}.</p>"
            + _values_legend(chart, kinds)
            + _scroller(figures, drawing, "Balances and their difference")
            + "<h3>The steps, with their figures</h3>"
            + _steps_table(structure, scale)
        )
    else:
        body += _range_summary(changes, scale.start, scale.end)
        if changes:
            strip, unit = _fitted_strip(structure, changes, _Plot(scale.start, scale.end))
            kinds = [k for k in KINDS if any(c.kind == k for c in changes)]
            body += (
                strip
                + f"<p>Each mark stands for the changes in one {unit}, and a number beside a "
                "mark is how many it holds.</p>"
                + _longest_level_sentence(structure, scale.start, scale.end)
                + _kind_legend(kinds)
                + everything
                + _counts_table(view.ref, changes, scale.start, scale.end)
            )
        else:
            body += everything
        body += (
            _scope_note(view)
            + _mode(view, unmasked, start, end)
            + "<h3>The structure in words</h3>"
            + _structure_html(structure)
        )
    body += _links(view.ref)
    return render_page("Balance differences", body, wide=True)


class BalanceChartPages:
    """The chart's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _balance_chart_get(self, params: dict[str, list[str]]) -> None:
        # Nothing in the query string can unmask: the rendering is chosen by
        # which method was used.
        self._balance_chart(
            (params.get("ref", [""])[0] or "").strip(),
            params.get("from", [""])[0] or "",
            params.get("to", [""])[0] or "",
            unmasked=False,
        )

    def _balance_chart_post(self, form: dict[str, list[str]]) -> None:
        self._balance_chart(
            (form.get("ref", [""])[0] or "").strip(),
            form.get("from", [""])[0] or "",
            form.get("to", [""])[0] or "",
            unmasked=True,
        )

    def _balance_chart(self, ref: str, start: str, end: str, *, unmasked: bool) -> None:
        hook = self.bound_config.balance_chart_data
        if hook is None:
            self._respond(404, _page("Not available", "No balance chart is wired."))
            return
        if not ref:
            self._respond(400, _page("No account named", "Say which account with ?ref=."))
            return
        try:
            first, last = parse_range(start, end)
        except DataError as exc:
            self._respond(400, _page("Not a range", f"{exc}."))
            return
        try:
            chart = hook(ref)
        except Exception as fault:
            # Not str(fault): its text is not under this module's control and a
            # figure could ride in it.
            say("balance_chart.fault", kind=type(fault).__name__)
            self._respond(
                500, _page("Chart failed", "The chart could not be built."), no_store=unmasked
            )
            return
        self._respond(
            404 if chart.state == "unknown" else 200,
            render_balance_chart(chart, unmasked=unmasked, start=first, end=last),
            no_store=unmasked,
        )


__all__ = [
    "KINDS",
    "MARK_HEIGHT",
    "MAX_PIXELS_PER_DAY",
    "OWN",
    "PIXELS_PER_DAY",
    "TARGET_RANGE_WIDTH",
    "BalanceChartPages",
    "StructureReport",
    "choose_scale",
    "nice_ticks",
    "parse_range",
    "render_balance_chart",
    "structure_summary_html",
]
