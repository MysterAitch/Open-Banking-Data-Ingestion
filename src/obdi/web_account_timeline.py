"""The coverage timeline in miniature, for an account's own page.

One model, two renderings: this draws the lanes of `coverage_timeline.AccountTimeline`, the model
the full page draws, with the same quiet stretches collapsed (`quiet_stretches`) and the same
sentences for every mark (`web_coverage_timeline`). What differs is only the drawing. It is fitted
to the page's width and never scrolls sideways; a lane is drawn in cells of at least a week, so
the picture is a fixed handful of rectangles however many rows or known balances the account
holds; and marks that would collide merge into one with a count.

THE SPAN is the twelve months ending with the month the page shows, cut at the first day anything
is held and at today. The month shown is bracketed and labelled, and is never collapsed, so the
rows listed beneath and the coverage above are visibly about the same days. Rejected: the whole
history collapsed, whose cells are a quarter or more wide and which shows a month's own rows as a
sliver, so the bracket and the list beneath it could not be read as the same days.

Masked by construction: it holds dates, counts, source names, and kinds of issue, never an amount
or a description, so the page it sits on is identical with values shown and hidden.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from math import ceil
from urllib.parse import urlencode

from .coverage_timeline import (
    KIND_NAMES,
    MISSING,
    PARTIAL,
    STATEMENT,
    TYPED,
    UNMATCHED,
    AccountTimeline,
    Lane,
    Marker,
    Quiet,
    marker_anchor,
    quiet_stretches,
    seam_anchor,
    span_words,
)
from .fetch_gaps import add_months
from .logs import say
from .plural import plural
from .web_coverage_timeline import (
    MADE_BY_OBDI_WORDS,
    MARKS,
    VERIFICATION_GAPS,
    _gap_sentence,
    _key,
    _marker_sentence,
    _seam_sentence,
    expected_sentence,
)

_esc = html.escape

#: The drawing's own units; it is scaled to the width of the page.
WIDTH = 300.0
LABEL_W = 68.0
RIGHT = 6.0
ROW_H = 17.0
TOP = 16.0
#: The room a collapsed stretch takes, in these units.
BREAK_UNITS = 12.0
#: The narrowest a cell may be: a lane is a cell a week, or this many units if a week is narrower.
MIN_CELL_UNITS = 4.0
#: Marks nearer than this merge into one with a count.
MERGE_UNITS = 12.0
#: How far either side of a mark the full page's window reaches.
LINK_MARGIN_DAYS = 21

#: How faint a fully covered cell and a part covered one are drawn (as on the full page).
FULL_FAINT = ' fill-opacity=".3"'
PART_FAINT = ' fill-opacity=".12"'

#: Short names for the lanes, which share a narrow label column.
SHORT_NAMES = {
    "feed": "Feed", "aggregator": "Aggregator", "export": "Export", "statement": "Statements",
}

#: How a lane's reach is said in the one line.
REACH_WORDS = {
    "feed": "the bank feed", "aggregator": "the aggregator", "export": "the export",
    "statement": "statements",
}


@dataclass(frozen=True)
class _Segment:
    first: date
    last: date
    left: float
    right: float
    cut: bool

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1


class _Axis:
    """The one mapping from a day to a place in the drawing, which knows the collapsed stretches."""

    def __init__(self, first: date, last: date, breaks: tuple[Quiet, ...]) -> None:
        total = (last - first).days + 1
        drawn_days = total - sum(b.days for b in breaks)
        room = WIDTH - LABEL_W - RIGHT - len(breaks) * BREAK_UNITS
        self.per_day = room / max(drawn_days, 1)
        self.first, self.last = first, last
        self.segments: list[_Segment] = []
        cursor, x = first, LABEL_W
        for cut in breaks:
            if cut.first > cursor:
                span = (cut.first - cursor).days
                self.segments.append(_Segment(cursor, cut.first - timedelta(days=1), x,
                                              x + span * self.per_day, False))
                x += span * self.per_day
            self.segments.append(_Segment(cut.first, cut.last, x, x + BREAK_UNITS, True))
            x += BREAK_UNITS
            cursor = cut.last + timedelta(days=1)
        if cursor <= last:
            span = (last - cursor).days + 1
            self.segments.append(_Segment(cursor, last, x, x + span * self.per_day, False))

    def x(self, day: date) -> float:
        for seg in self.segments:
            if seg.first <= day <= seg.last:
                fraction = (day - seg.first).days / seg.days
                return seg.left + fraction * (seg.right - seg.left)
        return self.segments[-1].right if day > self.last else LABEL_W

    def end_x(self, day: date) -> float:
        """Where the day ends: the start of the next, so a cell's right edge never overshoots."""
        return self.x(day + timedelta(days=1)) if day < self.last else self.segments[-1].right

    def in_cut(self, day: date) -> bool:
        return any(seg.cut and seg.first <= day <= seg.last for seg in self.segments)


def span_of(view: AccountTimeline, month: str) -> tuple[date, date]:
    """The twelve months ending with `month` ("YYYY-MM"), cut at the first day held and today.

    A month that is not a month (an empty or unparseable one) means the newest twelve months."""
    try:
        year, number = (int(part) for part in month.split("-"))
        start = date(year, number, 1)
    except ValueError:
        start = view.today.replace(day=1)
    end = min(add_months(start, 1) - timedelta(days=1), view.today)
    first = max(add_months(start, -11), view.first_day)
    return min(first, end), end


def _month_days(month: str, view: AccountTimeline) -> tuple[date, date] | None:
    try:
        year, number = (int(part) for part in month.split("-"))
        start = date(year, number, 1)
    except ValueError:
        return None
    return start, min(add_months(start, 1) - timedelta(days=1), view.today)


def _covered_days(lane: Lane, first: date, last: date) -> int:
    return sum(
        max(0, (min(run.last, last) - max(run.first, first)).days + 1) for run in lane.runs
    )


def _lane_state(lane: Lane, first: date, last: date) -> str:
    days = (last - first).days + 1
    covered = _covered_days(lane, first, last)
    if covered >= days:
        return "full"
    if covered:
        return "part"
    window = lane.trailing
    if lane.kind == STATEMENT and window is not None and window[0] <= first <= last <= window[1]:
        return "unavailable"
    return "none"


def _verification_state(view: AccountTimeline, first: date, last: date) -> str:
    """The worst state any day of the cell is in: held back, then no known balance, then agreed."""
    seen = set()
    for band in view.verification.bands:
        if band.first <= last and band.last >= first and band.state != "protected":
            seen.add(band.state)
    for state in ("held", "none", "agrees"):
        if state in seen:
            return state
    return "none"


def _cells(axis: _Axis) -> list[tuple[date, date, float, float]]:
    """(first day, last day, left, right) of each cell, a week or more across."""
    size = max(7, ceil(MIN_CELL_UNITS / axis.per_day)) if axis.per_day > 0 else 7
    cells: list[tuple[date, date, float, float]] = []
    for seg in axis.segments:
        if seg.cut:
            cells.append((seg.first, seg.last, seg.left, seg.right))
            continue
        day = seg.first
        while day <= seg.last:
            last = min(day + timedelta(days=size - 1), seg.last)
            cells.append((day, last, axis.x(day), axis.end_x(last)))
            day = last + timedelta(days=1)
    return cells


def _runs_of(
    cells: list[tuple[date, date, float, float]], state_of: Callable[[date, date], str]
) -> list[tuple[str, float, float]]:
    """The cells' states with neighbours in one state joined: a lane is a handful of rectangles."""
    joined: list[tuple[str, float, float]] = []
    for first, last, left, right in cells:
        state = state_of(first, last)
        if joined and joined[-1][0] == state and abs(joined[-1][2] - left) < 0.05:
            joined[-1] = (state, joined[-1][1], right)
        else:
            joined.append((state, left, right))
    return joined


def _rect(cls: str, left: float, right: float, y: float, height: float, extra: str = "") -> str:
    return (
        f'<rect class="{cls}" x="{left:.1f}" y="{y:.1f}" width="{max(right - left, 0.5):.1f}" '
        f'height="{height:.1f}"{extra}/>'
    )


#: A mark is a link, so it carries a transparent target this many units across and tall, which
#: drawn at a phone's width is a thumb (44 px) and more, however small the mark itself is.
TARGET_UNITS = 44.0


def _mark_link(
    href: str, title: str, inner: str, left: float, right: float, middle: float
) -> str:
    """A mark as a link with a thumb-sized target about it (`TARGET_UNITS`)."""
    centre = (left + right) / 2
    half = max(right - left, TARGET_UNITS) / 2
    target = _rect(
        "cov-target", centre - half, centre + half, middle - TARGET_UNITS / 2, TARGET_UNITS,
        ' fill="transparent"',
    )
    return (
        f'<a class="tap" href="{_esc(href)}"><title>{_esc(title)}</title>{inner}{target}</a>'
    )


def full_href(ref: str, first: date, last: date, fragment: str = "") -> str:
    """The full page over `first` to `last`, landing on the entry `fragment` names."""
    query = {
        "ref": ref, "window": "between", "window_held": "between",
        "window_from": first.isoformat(), "window_to": last.isoformat(),
    }
    return f"/coverage-timeline?{urlencode(query)}" + (f"#e-{fragment}" if fragment else "")


def reach_line(view: AccountTimeline) -> str:
    """The one line he reads on a phone: where each way in reaches, what is to fetch, and when the
    next statement is expected. Dates and counts only."""
    if not view.lanes and view.made_by_obdi:
        return MADE_BY_OBDI_WORDS
    reaches: dict[str, date] = {}
    for lane in view.lanes:
        if lane.kind == TYPED or not lane.runs:
            continue
        last = lane.runs[-1].last
        reaches[lane.kind] = max(last, reaches.get(lane.kind, last))
    parts = [
        f"{REACH_WORDS[kind]} reach{'es' if kind != 'statement' else ''} {day.isoformat()}"
        for kind in ("statement", "export", "feed", "aggregator")
        if (day := reaches.get(kind)) is not None
    ]
    gaps = len(view.gaps)
    parts.append(f"{plural(gaps, 'gap')} to fill" if gaps else "nothing needs fetching")
    line = "; ".join(parts) + "."
    line = line[0].upper() + line[1:]
    expected = expected_sentence(view)
    if expected:
        line = line[:-1] + ";" + expected.replace(" Next", " next")
    return line


def compact_svg(
    view: AccountTimeline, first: date, last: date, shown: tuple[date, date] | None
) -> tuple[str, set[str], int]:
    """The drawing, the marks it used (for the key), and how many stretches it collapsed."""
    keep = [shown] if shown is not None else []
    breaks = quiet_stretches(view, first, last, keep=keep)
    if len(breaks) == 1 and (breaks[0].first, breaks[0].last) == (first, last):
        breaks = ()
    axis = _Axis(first, last, breaks)
    cells = _cells(axis)
    used: set[str] = set()
    lanes = [lane for lane in view.lanes if lane.kind != TYPED]
    rows: list[Lane | None] = [None, *lanes]
    height = TOP + len(rows) * ROW_H + 18
    body: list[str] = []
    marks: list[str] = []
    row_y: dict[str, float] = {"": TOP}
    for index, lane in enumerate(rows):
        y = TOP + index * ROW_H
        if lane is not None:
            row_y[lane.source] = y
        label = "Verified" if lane is None else SHORT_NAMES.get(lane.kind, KIND_NAMES[lane.kind])
        body.append(
            f'<text class="cov-dim" x="{LABEL_W - 4:.1f}" y="{y + ROW_H / 2 + 3:.1f}" '
            f'text-anchor="end">{_esc(label)}</text>'
        )
        if lane is None:
            for state, left, right in _runs_of(
                cells, lambda a, b: _verification_state(view, a, b)
            ):
                cls = {"agrees": "cov-ver-agrees", "held": "cov-ver-held"}.get(
                    state, "cov-ver-none"
                )
                faint = ' fill-opacity=".6"' if state in ("agrees", "held") else ""
                body.append(_rect(cls, left, right, y + 4, ROW_H - 8, faint))
        else:
            for state, left, right in _runs_of(cells, partial(_lane_state, lane)):
                if state == "full":
                    body.append(_rect("cov-bar", left, right, y + 3, ROW_H - 6, FULL_FAINT))
                    used.add("covered")
                elif state == "part":
                    body.append(_rect("cov-bar", left, right, y + 3, ROW_H - 6, PART_FAINT))
                    used.add("possible")
                elif state == "unavailable":
                    body.append(
                        _rect("cov-unavailable", left, right, y + 3, ROW_H - 6,
                              ' fill="url(#cov-unavailable-c)"')
                    )
                    used.add("unavailable")
    bottom = TOP + len(rows) * ROW_H
    for seg in axis.segments:
        if seg.cut:
            for edge in (seg.left, seg.right):
                body.append(
                    f'<polyline class="cov-break-cut" fill="none" points="'
                    f'{edge + 2:.1f},{TOP - 2:.1f} {edge - 2:.1f},{(TOP + bottom) / 2:.1f} '
                    f'{edge + 2:.1f},{bottom + 2:.1f}"/>'
                )
            used.add("break")
    if shown is not None and shown[1] >= first and shown[0] <= last:
        left, right = axis.x(max(shown[0], first)), axis.end_x(min(shown[1], last))
        # The label is centred under the bracket, or ends at the drawing's edge where the
        # bracket is at it, so it is never cut.
        near_edge = (left + right) / 2 > WIDTH - 24
        label_x, anchor = (WIDTH - 2, "end") if near_edge else ((left + right) / 2, "middle")
        body.append(
            _rect("cov-month", left, right, TOP - 3, bottom - TOP + 6)
            + f'<text x="{label_x:.1f}" y="{bottom + 13:.1f}" text-anchor="{anchor}">'
            f"{shown[0]:%Y-%m}</text>"
        )
    # Marks: every one links to the full page, at a window around it, landing on its sentence.
    for gap in view.gaps:
        if gap.last < first or gap.first > last:
            continue
        gap_y = row_y[""] if gap.kind in VERIFICATION_GAPS else row_y.get(gap.source)
        if gap_y is None:
            continue
        left, right = axis.x(max(gap.first, first)), axis.end_x(min(gap.last, last))
        href = full_href(
            view.ref, gap.first - timedelta(days=14),
            min(gap.last + timedelta(days=14), view.today), gap.anchor,
        )
        marks.append(
            _mark_link(
                href, _gap_sentence(view, gap),
                _rect("cov-gap cov-focus", left, right, gap_y + 2, ROW_H - 4),
                left, right, gap_y + ROW_H / 2,
            )
        )
        used.add("gap")
    for seam in view.seams_to_check:
        if not first <= seam.day <= last or seam.source not in row_y:
            continue
        fraction = 1.0
        if seam.last_state == PARTIAL and seam.fraction:
            fraction = seam.fraction
        x = axis.x(seam.day) + fraction * axis.per_day
        kind = "seam-red" if seam.verdict == MISSING else "seam-amber"
        margin = timedelta(days=LINK_MARGIN_DAYS)
        href = full_href(
            view.ref, seam.day - margin, min(seam.day + margin, view.today),
            seam_anchor(seam.source, seam.day),
        )
        seam_y = row_y[seam.source] + ROW_H / 2
        marks.append(
            _mark_link(
                href, _seam_sentence(view, seam), MARKS[kind].draw(x, seam_y), x, x, seam_y
            )
        )
        used.add(kind)
    grouped: list[list[Marker]] = []
    for marker in view.markers:
        if not first <= marker.day <= last:
            continue
        previous = grouped[-1] if grouped else None
        if (
            previous is not None
            and previous[0].kind == marker.kind
            and previous[0].source == marker.source
            and axis.x(marker.day) - axis.x(previous[-1].day) < MERGE_UNITS
        ):
            previous.append(marker)
        else:
            grouped.append([marker])
    for group in grouped:
        lead = group[0]
        x = axis.x(lead.day)
        on_lane = lead.kind == UNMATCHED and lead.source in row_y
        y = row_y[lead.source] if on_lane else row_y[""]
        count = sum(m.count for m in group)
        href = full_href(
            view.ref, group[0].day - timedelta(days=LINK_MARGIN_DAYS),
            min(group[-1].day + timedelta(days=LINK_MARGIN_DAYS), view.today),
            marker_anchor(lead.kind, lead.source, lead.day),
        )
        said = _marker_sentence(view, lead)
        if len(group) > 1:
            said = f"{plural(len(group), 'mark')} of this kind: {said}"
        inner = MARKS[lead.kind].draw(x, y + ROW_H / 2)
        if len(group) > 1:
            inner += f'<text x="{x + 7:.1f}" y="{y + ROW_H / 2 + 3:.1f}">{count}</text>'
        marks.append(_mark_link(href, said, inner, x, x, y + ROW_H / 2))
        used.add(lead.kind)
    today_x = axis.x(view.today) + axis.per_day / 2 if first <= view.today <= last else None
    if today_x is not None:
        marks.append(
            f'<line class="cov-today" x1="{today_x:.1f}" y1="{TOP - 4:.1f}" x2="{today_x:.1f}" '
            f'y2="{bottom + 2:.1f}"/>'
        )
    desc = (
        f"Coverage of {plural(len(lanes), 'source')} from {first.isoformat()} to "
        f"{last.isoformat()}, with {plural(len(view.gaps), 'gap')}."
    )
    if breaks:
        total = sum(b.days for b in breaks)
        desc += (
            f" {plural(len(breaks), 'quiet stretch', 'quiet stretches')} collapsed, "
            f"{span_words(first, first + timedelta(days=total - 1))} in all: not to scale."
        )
    axis_text = (
        f'<text class="cov-dim" x="{LABEL_W:.1f}" y="11">{first.isoformat()}</text>'
        f'<text class="cov-dim" x="{WIDTH - RIGHT:.1f}" y="11" text-anchor="end">'
        f"{last.isoformat()}</text>"
    )
    defs = (
        '<defs><pattern id="cov-unavailable-c" width="4" height="4" '
        'patternUnits="userSpaceOnUse"><line class="cov-unavailable-line" x1="0" y1="0" '
        'x2="0" y2="4"/></pattern></defs>'
    )
    svg = (
        f'<svg class="cov-svg cov-compact-svg" role="img" aria-labelledby="cov-c-t cov-c-d" '
        f'style="width:100%;height:auto" viewBox="0 0 {WIDTH:.0f} {height:.0f}" '
        ">"
        f'<title id="cov-c-t">Coverage timeline for {_esc(view.label)}</title>'
        f'<desc id="cov-c-d">{_esc(desc)}</desc>{defs}{axis_text}'
        + "".join(body)
        + "".join(marks)
        + "</svg>"
    )
    return svg, used, len(breaks)


def compact_block(view: AccountTimeline, month: str) -> str:
    """The block for the account page: the one line, folded over the drawing, and the way to the
    full page. Opens with the line as its summary, so a phone shows the sentence and not the chart.
    """
    first, last = span_of(view, month)
    svg, used, _ = compact_svg(view, first, last, _month_days(month, view))
    full = full_href(view.ref, first, last)
    return (
        '<div class="cov-compact" id="coverage">'
        f'<details><summary>{_esc(reach_line(view))}</summary>{svg}'
        f'<details class="cov-keybox"><summary>Key to the marks</summary>{_key(used)}</details>'
        "</details>"
        f'<p><a class="tap" href="{_esc(full)}">Open the coverage timeline</a></p></div>'
    )


def block_for(config: object, ref: str, month: str, today: date) -> str:
    """The block, or "" where no timeline is wired. A failure is said on the page and in the log,
    never swallowed: an account page without its timeline must not look like one with nothing
    to show."""
    hook = getattr(config, "coverage_timeline_compact", None)
    if hook is None:
        return ""
    try:
        view = hook(ref, today)
    except Exception as fault:
        say("account_timeline.fault", kind=type(fault).__name__)
        return '<p class="muted">The coverage timeline could not be built just now.</p>'
    if view is None:
        return ""
    return compact_block(view, month)
