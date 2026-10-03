"""The balance-difference timeline and its values chart, in both renderings.

It follows the ledger page's mechanism exactly. A GET renders the MASKED
timeline whatever its query string. Showing values takes a POST, answered
directly with the values chart and never redirected, so no address a person can
bookmark, paste into a note, or fetch from a script holds a value, and the
response is sent `no-store` so a browser history does not either.

THE TIMELINE HAS NO VERTICAL MAGNITUDE. A step's size is a figure, and a mark
taller for a bigger step would draw the figure. Every mark is one height, rows
say what KIND of step a mark is, and the only measure that varies is the date,
which is not private. The values chart is where sizes are drawn.

THE SCALE, stated here and only here: a full view is `PIXELS_PER_DAY` wide per
day, so about seven and a half years come to about ten thousand pixels, inside
a container that scrolls sideways so the page itself does not. A `from`/`to`
range is drawn so that the whole range is about `TARGET_RANGE_WIDTH` wide, no
narrower than a full view and no wider per day than `MAX_PIXELS_PER_DAY`, so a
month opens at day scale. The labels are drawn in a column beside the scrolling
chart, so the row names and the figures stay in view while the dates scroll.

Charts are inline SVG with presentation attributes and no script, as the
position page's is, so nothing is added to the shared stylesheet that pages
which must show no figure are searched against.
"""

from __future__ import annotations

import calendar
import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import floor, log10
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .balance_chart import OWN, WHOLE, BalanceChart, SourceLine
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
    Step,
    StructureReport,
)
from .logs import say
from .masking import Disclosed
from .web_accounts import submit_button

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

PIXELS_PER_DAY = 3.65
TARGET_RANGE_WIDTH = 10_000
MAX_PIXELS_PER_DAY = 240.0

#: The one height of every mark on the timeline, whatever it stands for.
MARK_HEIGHT = 14
MARK_WIDTH = 5
ROW_HEIGHT = 30
EDGE = 24
LABEL_WIDTH = 88
AXIS_WIDTH = 78

#: How many days of a long list are named before "and N more".
_NAMED_DAYS = 12

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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _mono(day: object) -> str:
    return f'<span class="mono nowrap">{_esc(str(day))}</span>'


def _day_list(days: Sequence[date]) -> str:
    named = ", ".join(_mono(day) for day in days[:_NAMED_DAYS])
    more = len(days) - _NAMED_DAYS
    return named + (f" and {more} more" if more > 0 else "")


def _per_cent(share: float) -> str:
    text = f"{share * 100:.1f}".removesuffix(".0")
    return f"{text} per cent"


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
            f"<p>The difference is nil at all {balances} stated balances: one level, and "
            f"{_per_cent(1.0)} of the stated balances move from the one before as the rows "
            "do.</p>"
        )
    steps = len(structure.steps)
    body = (
        f"<p>{structure.agreeing} of the {balances} stated balances "
        f"({_per_cent(structure.agreeing / balances)}) move from the one before by exactly "
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


def _linked(href: str | None, inner: str) -> str:
    return f'<a href="{href}">{inner}</a>' if href else inner


def _axis(
    scale: Scale,
    *,
    year_y: float | None,
    month_y: Sequence[float],
    lines: tuple[float, float] | None,
    ref: str | None,
) -> str:
    """Month ticks, each labelled with its month AND year, so any screenful says
    the date; year labels above them; day labels where the scale has the room.

    Labels link to a narrower range of the masked timeline when `ref` is given.
    """
    parts: list[str] = []
    months = _months(scale)
    per_day = scale.per_day
    for first in months:
        x = scale.x(first)
        major = first.month == 1
        if lines is not None:
            parts.append(
                f'<line x1="{x:.1f}" y1="{lines[0]:.1f}" x2="{x:.1f}" y2="{lines[1]:.1f}" '
                f'stroke="currentColor" stroke-opacity="{".35" if major else ".14"}"/>'
            )
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        href = _href(ref, first, last) if ref else None
        text = f"{first:%b} {first.year}"
        for y in month_y:
            parts.append(_linked(href if y == month_y[0] else None, _label(x + 3, y, text)))
    if year_y is not None:
        years = {scale.start.year: scale.start}
        years.update({m.year: m for m in months if m.month == 1})
        for year, anchor in sorted(years.items()):
            x = scale.x(max(anchor, scale.start))
            span_start, span_end = date(year, 1, 1), date(year, 12, 31)
            href = _href(ref, span_start, span_end) if ref else None
            parts.append(
                _linked(href, _label(x + 3, year_y, str(year), ' font-weight="700"'))
            )
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


def _rows(structure: FaultStructure) -> list[tuple[str, str, list[tuple[int, Step]]]]:
    """(row name, kind, (number, step) pairs), in the order the rows are drawn."""
    numbered = list(enumerate(structure.steps))
    rows: list[tuple[str, str, list[tuple[int, Step]]]] = []
    pairs = [(n, step) for n, step in numbered if step.kind == TRANSIENT]
    if pairs:
        rows.append(("Timing pairs", TRANSIENT, pairs))
    for size_class in structure.classes:
        members = [(n, step) for n, step in numbered if step.size_class == size_class.letter]
        rows.append((f"Size class {size_class.letter}", RECURRING, members))
    for kind in (UNEXPLAINED, UNHELD, EXPLAINED):
        found = [(n, step) for n, step in numbered if step.kind == kind]
        if found:
            rows.append((KINDS[kind][0], kind, found))
    return rows


def _mark(scale: Scale, step: Any, y: float, kind: str, extra: str = "") -> str:
    colour = KINDS[kind][1]
    return (
        f'<rect class="mark" data-kind="{kind}" x="{scale.x(step.day):.1f}" y="{y:.1f}" '
        f'width="{MARK_WIDTH}" height="{MARK_HEIGHT}" fill="{colour}" stroke="currentColor" '
        f'stroke-width="1"{extra}/>'
    )


def _timeline_svgs(structure: FaultStructure, scale: Scale, ref: str) -> tuple[str, str, str]:
    """(label column, chart, description) of the timeline. No element's geometry
    depends on a size: marks are one height and joins depend only on dates."""
    rows = _rows(structure)
    top = 44
    height = top + ROW_HEIGHT * (len(rows) + 1) + 22
    pad = (ROW_HEIGHT - MARK_HEIGHT) / 2
    labels = [_label(4, top + ROW_HEIGHT / 2 + 4, "Levels")]
    body = [
        _axis(
            scale,
            year_y=12,
            month_y=(28, height - 6),
            lines=(top, height - 20),
            ref=ref,
        )
    ]
    last_level = len(structure.levels) - 1
    for index, level in enumerate(structure.levels):
        left = scale.x(max(level.first_day, scale.start))
        right = scale.x(min(level.until, scale.end)) + (scale.per_day if index == last_level else 0)
        if right < scale.x(scale.start) or left > scale.x(scale.end) + scale.per_day:
            continue
        body.append(
            f'<g><title>Level {index + 1}: stated balances from {level.first_day} to '
            f"{level.last_day} ({level.balances}); the difference does not change across "
            "them.</title>"
            f'<rect class="level" x="{left:.1f}" y="{top + pad:.1f}" '
            f'width="{max(right - left, 3):.1f}" height="{MARK_HEIGHT}" fill="currentColor" '
            f'fill-opacity="{".16" if index % 2 else ".34"}" stroke="currentColor" '
            'stroke-opacity=".6"/></g>'
        )
    for number, (name, kind, steps) in enumerate(rows, start=1):
        y0 = top + number * ROW_HEIGHT
        labels.append(_label(4, y0 + ROW_HEIGHT / 2 + 4, name))
        body.append(
            f'<line x1="{EDGE}" y1="{y0 + ROW_HEIGHT:.1f}" x2="{scale.width - EDGE:.1f}" '
            f'y2="{y0 + ROW_HEIGHT:.1f}" stroke="currentColor" stroke-opacity=".1"/>'
        )
        for at, step in steps:
            inside = scale.start <= step.day <= scale.end
            if kind == TRANSIENT:
                other = structure.steps[step.partner]
                if step.partner < at or not (step.day <= scale.end and other.day >= scale.start):
                    continue
                mid = y0 + ROW_HEIGHT / 2
                ends = "".join(
                    _mark(scale, end, y0 + pad, kind)
                    for end in (step, other)
                    if scale.start <= end.day <= scale.end
                )
                left = scale.x(max(step.day, scale.start)) + MARK_WIDTH / 2
                right = scale.x(min(other.day, scale.end)) + MARK_WIDTH / 2
                body.append(
                    f"<g><title>Timing pair: {step.day} and {other.day}, {step.gap_days} days "
                    "apart. The two undo each other exactly.</title>"
                    f'<path class="join" d="M{left:.1f},{mid:.1f} H{right:.1f}" '
                    f'stroke="{KINDS[kind][1]}" stroke-width="2" fill="none"/>' + ends + "</g>"
                )
                continue
            if not inside:
                continue
            what = KINDS[kind][0] + (f", size class {step.size_class}" if step.size_class else "")
            body.append(
                f"<g><title>{step.day}: {what}, stated by {_esc(step.source)}.</title>"
                + _mark(scale, step, y0 + pad, kind)
                + "</g>"
            )
    in_range = [s for s in structure.steps if scale.start <= s.day <= scale.end]
    desc = (
        f"From {scale.start} to {scale.end}: {_plural(len(in_range), 'step')} of the "
        f"difference in {_plural(len(rows), 'row')} by kind, and "
        f"{_plural(len(structure.levels), 'level')}. "
        "Marks are all one height; sizes are not drawn."
    )
    height_total = height
    label_svg = _svg(
        LABEL_WIDTH, height_total, "bc-rows", "Row names", "The kind of step each row holds.",
        "".join(labels),
    )
    chart_svg = _svg(
        scale.width, height_total, "bc-strip", "Timeline of the changes in the difference",
        desc, "".join(body),
    )
    return label_svg, chart_svg, desc


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
    body = [
        _axis(
            scale,
            year_y=None,
            month_y=(14, p1[1] + gap / 2 + 4, height - 8),
            lines=None,
            ref=None,
        )
    ]
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
    if chart.scope == WHOLE:
        spaces = ", ".join(_esc(space) for space in chart.spaces)
        return (
            "<h2>The whole account: the main account and its Spaces together</h2>"
            "<p>The balances are stated by sources that cannot see the Spaces, so they are "
            "checked against the rows of the main account and every Space"
            + (f" ({spaces})" if spaces else "")
            + ", where a transfer between them cancels.</p>"
        )
    return (
        "<h2>This account's own stated balances</h2>"
        "<p>Each balance is checked against this account's own rows.</p>"
    )


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
        '<p class="muted">This timeline draws no size: every mark is one height, and the '
        "rows say what kind of change each is. Sizes are figures, so they appear only on "
        "the values chart, which opens in a new tab on request.</p>"
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
    body += _heading(view) + _mode(view, unmasked, start, end)
    body += (
        f"<p>Drawn from {_mono(scale.start)} to {_mono(scale.end)} at "
        f"{scale.per_day:.2f} pixels a day. "
        + (
            "Everything held is drawn; the year and month labels below open a narrower range."
            if start is None
            else f'<a class="tap" href="{_href(view.ref)}">Draw everything held</a>'
        )
        + "</p>"
    )
    in_range = [s for s in structure.steps if scale.start <= s.day <= scale.end]
    if unmasked:
        figures, drawing, _ = _values_svgs(chart, scale)
        kinds = [k for k in KINDS if any(s.kind == k for s in in_range)]
        body += (
            f"<p>The present difference is <strong>{_esc(_pounds(structure.present_minor))}"
            f"</strong>, the sum of {_plural(len(structure.permanent), 'permanent change')}.</p>"
            + _values_legend(chart, kinds)
            + _scroller(figures, drawing, "Balances and their difference")
            + "<h3>The steps, with their figures</h3>"
            + _steps_table(structure, scale)
        )
    else:
        labels, drawing, _ = _timeline_svgs(structure, scale, view.ref)
        kinds = [k for k in KINDS if any(s.kind == k for s in in_range)]
        body += (
            _scroller(labels, drawing, "Timeline of the changes in the difference")
            + _kind_legend(kinds)
            + '<h3>The structure in words</h3>'
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
