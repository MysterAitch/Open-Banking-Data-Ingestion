"""The coverage timeline page: one account in full, drawn from `coverage_timeline`.

A masked page. It holds dates, counts, source names, account names, and kinds of issue, and no
amount and no description anywhere, including in the text of a chart element's title. There is
no "Show values" here. Being masked, its window may travel in the query string
(`window_control` states where a window may travel).

THE THRESHOLDS AT WHICH MARKS MERGE, stated here and nowhere else (a test holds each side):

  `LISTED_DAY_MIN_PX`   a day's listed rows are drawn as a mark of their own from this many pixels
                        a day; below it they are one mark per bucket of days, as dark as the
                        share of the bucket's days that listed anything.
  `KNOWN_TICK_MIN_PX`   a known balance is a tick of its own from this many pixels a day; below it
                        known balances merge into one tick per bucket, coloured by the worst of
                        them (red, then amber, then green) and darker for more of them.
  `NOTCH_MIN_PX`        a capture's boundary is drawn as a notch only where it is at least this
                        many pixels clear of the previous notch on the lane, so a feed asked
                        hundreds of times a day shows its envelope and not a solid smear.
  `MERGE_PX`            issue markers of one kind closer than this are one mark with a count.
  `GAP_LABEL_PX`        a gap is labelled with its dates only where it is at least this wide.

PAINT ORDER, back to front: the hatch for days before the account existed, the bands that are
true of a span (held back, conflict, protected), the rules, the bars with their listed rows and
notches, the gaps, the seams, the markers, and today. A band never hides a bar.

THE CHART HAS NO SCRIPT, so every mark, gap, seam, and band is a link to an entry in the list
beneath it, and every entry links back; `:target` highlights the one arrived at.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from .account_names import AccountShown, code_html
from .callback import render_page
from .coverage_timeline import (
    ASK_HOLE,
    COMPLETE,
    CONFLICT,
    HELD_BACK,
    INFERRED,
    KIND_NAMES,
    MEETS,
    MISSING,
    OBSERVED,
    PARTIAL,
    POSSIBLY,
    STATED,
    STATEMENT,
    TYPED,
    UNMATCHED,
    UNREPRODUCED,
    AccountTimeline,
    Gap,
    Lane,
    Marker,
    Quiet,
    Seam,
    marker_anchor,
    quiet_stretches,
    seam_anchor,
    span_words,
)
from .date_window import resolve
from .logs import say
from .page_times import range_text
from .plural import plural
from .web_balance_chart import (
    EDGE,
    MAX_PIXELS_PER_DAY,
    PIXELS_PER_DAY,
    TARGET_RANGE_WIDTH,
    Scale,
    _axis,
    _label,
    choose_scale,
)
from .window_control import (
    KEEP,
    WINDOW_FIELDS,
    WindowChoice,
    window_controls,
)
from .window_control import window_choice as read_window_choice

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

LISTED_DAY_MIN_PX = 5.0
KNOWN_TICK_MIN_PX = 3.0
NOTCH_MIN_PX = 10.0
MERGE_PX = 10.0
GAP_LABEL_PX = 210.0

#: Heights, in chart units, of each row. The labels column is built from the same rows so a label
#: shares its lane's baseline.
AXIS_H = 34
VERIFICATION_H = 32
LANE_H = 38
ISSUES_H = 30
VIEW_WIDTH = 360

#: How faint each fill is. The stylesheet carries no opacity (see `stylesheet_timeline`).
FAINT = {
    "bar": ".3",
    "possible": ".12",
    "held": ".12",
    "conflict": ".12",
    "protected": ".1",
    "agrees": ".6",
    "ver-held": ".6",
    "break": ".35",
}

#: What each way of knowing an edge is called, said once for the key and the titles. "Balances
#: meet" is deliberately the weakest claim a statement's start can make short of a first row.
CERTAINTY_WORDS = {
    STATED: "stated by the statement or file itself",
    "asked": "the window that was asked for",
    MEETS: "an opening balance equal to the previous statement's closing balance, which fits "
    "the two meeting but does not prove it, because a missing statement can net to nil",
    OBSERVED: "the first or last row seen, so the source may reach further (at least)",
    INFERRED: "inferred from how regularly statements arrive, or from the day one was received",
}
EDGE_CLASS = {
    STATED: "cov-edge-stated",
    "asked": "cov-edge-asked",
    MEETS: "cov-edge-meets",
    OBSERVED: "cov-edge-observed",
    INFERRED: "cov-edge-inferred",
}
EDGE_NAMES = {
    STATED: "stated",
    "asked": "asked",
    MEETS: "balances meet",
    OBSERVED: "observed",
    INFERRED: "inferred",
}

#: The gaps that are about verification, not about a source's days: drawn on the verification
#: lane. Every other kind is drawn on the lane of the source expected to supply the file.
VERIFICATION_GAPS = frozenset(
    {"no-balance", "automatic-only", "one-balance", "nothing-before", "flag-settle"}
)

#: What each reason for a hole between statements says, as a fact or as a probability.
HOLE_FACTS = {
    "starts-after": "The later statement says its period begins after the earlier one closed.",
    "balances-differ": (
        "The statements either side do not meet: the later one does not open on the balance "
        "the earlier one closed on."
    ),
    "unlisted-rows": "Rows are held for these days that no statement lists.",
    "balances-meet-net-nil": (
        "The balances meet, but the statements close two or more periods apart, so a missing "
        "statement whose movements net to nil is probable."
    ),
    "spacing": "The statements close further apart than they usually do.",
}

#: What each kind of gap is called in its sentence.
GAP_LABELS = {
    "newer-statement": "Newer statements are needed",
    "hole-between": "No statement is held",
    "export-stops": "The export stops short",
    "export-months": "The export lacks months",
    "no-balance": "No known balance tests these rows",
    "automatic-only": "No known balance tests these rows",
    "one-balance": "Only one known balance is held",
    "nothing-before": "Rows before the first known balance are untested",
    "flag-settle": "A review flag is waiting for a known balance",
}

#: The fields this page adds to the window's own.
SCALE_FIELD = "scale"
FIT, WIDE = "fit", "wide"


def _attr(name: str, value: object) -> str:
    return f' {name}="{_esc(str(value), quote=True)}"'


def _el(tag: str, cls: str, *attrs: tuple[str, object], inner: str = "") -> str:
    rendered = "".join(_attr(k, v) for k, v in attrs)
    return f'<{tag} class="{cls}"{rendered}>{inner}</{tag}>' if inner else (
        f'<{tag} class="{cls}"{rendered}/>'
    )


def _rect(cls: str, x: float, y: float, width: float, height: float, faint: str = "") -> str:
    extra = (("fill-opacity", FAINT[faint]),) if faint else ()
    return _el("rect", cls, ("x", f"{x:.1f}"), ("y", f"{y:.1f}"), ("width", f"{max(width, 0):.1f}"),
               ("height", f"{height:.1f}"), *extra)


def _line(cls: str, x1: float, y1: float, x2: float, y2: float) -> str:
    return _el("line", cls, ("x1", f"{x1:.1f}"), ("y1", f"{y1:.1f}"), ("x2", f"{x2:.1f}"),
               ("y2", f"{y2:.1f}"))


# ---------------------------------------------------------------------------
# The marks: one table that the chart and the key both read.


def _diamond(cls: str, cx: float, cy: float) -> str:
    corners = ((cx, cy - 6), (cx + 6, cy), (cx, cy + 6), (cx - 6, cy))
    points = " ".join(f"{px:.1f},{py:.1f}" for px, py in corners)
    return _el("polygon", f"{cls} cov-focus", ("points", points))


def _square(cls: str, cx: float, cy: float) -> str:
    return _el("rect", f"{cls} cov-focus", ("x", f"{cx - 5:.1f}"), ("y", f"{cy - 5:.1f}"),
               ("width", "10"), ("height", "10"))


def _triangle(cls: str, cx: float, cy: float) -> str:
    points = f"{cx:.1f},{cy - 6:.1f} {cx + 6:.1f},{cy + 5:.1f} {cx - 6:.1f},{cy + 5:.1f}"
    return _el("polygon", f"{cls} cov-focus", ("points", points))


def _circle(cls: str, cx: float, cy: float) -> str:
    return _el("circle", f"{cls} cov-focus", ("cx", f"{cx:.1f}"), ("cy", f"{cy:.1f}"), ("r", "5"))


def _bar_mark(cls: str, cx: float, cy: float) -> str:
    return _el("rect", f"{cls} cov-focus", ("x", f"{cx - 2:.1f}"), ("y", f"{cy - 7:.1f}"),
               ("width", "4"), ("height", "14"))


@dataclass(frozen=True)
class MarkStyle:
    label: str
    draw: Callable[[float, float], str]


MARKS: dict[str, MarkStyle] = {
    "seam-red": MarkStyle(
        "A seam where another source holds rows this capture lacks",
        lambda x, y: _diamond("cov-seam-red", x, y),
    ),
    "seam-amber": MarkStyle(
        "A seam with no other source to compare it with",
        lambda x, y: _diamond("cov-seam-amber", x, y),
    ),
    UNREPRODUCED: MarkStyle(
        "A known balance the transactions do not add up to",
        lambda x, y: _square("cov-mark-red", x, y),
    ),
    CONFLICT: MarkStyle(
        "Two sources state different balances for a day (a conflict between sources, not a "
        "fault in the transactions)",
        lambda x, y: _triangle("cov-mark-amber", x, y),
    ),
    HELD_BACK: MarkStyle(
        "Where the transactions stop adding up to the known balances",
        lambda x, y: _bar_mark("cov-mark-amber", x, y),
    ),
    UNMATCHED: MarkStyle(
        "A row another covering source lists and this one does not",
        lambda x, y: _circle("cov-mark-amber-hollow", x, y),
    ),
}


# ---------------------------------------------------------------------------
# Geometry.


@dataclass(frozen=True)
class _Row:
    kind: str
    lane: Lane | None
    y: float
    height: float


def _rows(view: AccountTimeline) -> list[_Row]:
    rows = [_Row("verification", None, AXIS_H, VERIFICATION_H)]
    y = AXIS_H + VERIFICATION_H
    for lane in view.lanes:
        rows.append(_Row("lane", lane, y, LANE_H))
        y += LANE_H
    rows.append(_Row("issues", None, y, ISSUES_H))
    return rows


def _height(rows: Sequence[_Row]) -> float:
    return rows[-1].y + rows[-1].height + AXIS_H


@dataclass
class _Entry:
    ident: str
    group: str
    sentence: str
    first: date
    last: date
    links: list[tuple[str, str]] = field(default_factory=list)
    #: The gap a "fetch" entry is about, so the list above the chart reads its source from it.
    gap: Gap | None = None
    #: Further addresses that land on this entry: every marker merged into a mark is reachable
    #: by its own, so a link made from one marker finds the group that holds it.
    aliases: list[str] = field(default_factory=list)


#: The width, in chart units, of a collapsed stretch: room for a two-line label of its length.
BREAK_W = 72.0

#: The fields this page adds for the quiet stretches (see `quiet_stretches`): `days=all` draws
#: every day, and `expand` names stretches (as `first_last`, comma-joined) to draw in full.
DAYS_FIELD = "days"
EXPAND_FIELD = "expand"
EVERY_DAY = "all"


@dataclass(frozen=True)
class BrokenScale(Scale):
    """A `Scale` whose axis is not uniform: each quiet stretch is `BREAK_W` units wide.

    `x` is the one mapping from a day to a place that knows the breaks, and everything on the
    chart goes through it, so nothing can be drawn as if the axis were continuous. A day inside a
    stretch is placed in proportion across the stretch's fixed width, so a bar that runs through
    it is drawn straight through.
    """

    breaks: tuple[Quiet, ...] = ()

    def in_break(self, day: date) -> bool:
        return any(b.first <= day <= b.last for b in self.breaks)

    def _removed(self, quiet: Quiet) -> float:
        return quiet.days * self.per_day - BREAK_W

    @property
    def width(self) -> float:
        return super().width - sum(self._removed(b) for b in self.breaks)

    def x(self, day: date) -> float:
        removed = 0.0
        for b in self.breaks:
            if day > b.last:
                removed += self._removed(b)
            elif day >= b.first:
                left = EDGE + (b.first - self.start).days * self.per_day - removed
                return left + (day - b.first).days / b.days * BREAK_W
            else:
                break
        return EDGE + (day - self.start).days * self.per_day - removed

    def break_edges(self) -> list[tuple[Quiet, float, float]]:
        """Each stretch with the x of its left and right cut."""
        return [(b, self.x(b.first), self.x(b.first) + BREAK_W) for b in self.breaks]


def _in_break(scale: Scale, day: date) -> bool:
    return isinstance(scale, BrokenScale) and scale.in_break(day)


def _bucket_index(scale: Scale, day: date, width: float) -> int:
    """Which `width`-unit bucket of the drawn axis a day falls in."""
    return int((scale.x(day) - scale.x(scale.start)) / width + 1e-9)


def notch_xs(lane: Lane, scale: Scale) -> list[float]:
    """The x of each capture boundary worth a notch, thinned by `NOTCH_MIN_PX`."""
    xs: list[float] = []
    for capture in lane.captures:
        if capture.last < scale.start or capture.first > scale.end:
            continue
        if _in_break(scale, capture.first) or _in_break(scale, capture.last):
            continue
        fraction = capture.fraction if capture.last_state == PARTIAL and capture.fraction else 1.0
        xs.append(scale.x(capture.first))
        xs.append(scale.x(capture.last) + fraction * scale.per_day)
    kept: list[float] = []
    for x in sorted(xs):
        if not kept or x - kept[-1] >= NOTCH_MIN_PX:
            kept.append(x)
    return kept


def _clamp(scale: Scale, x: float) -> float:
    return min(max(x, scale.x(scale.start)), scale.x(scale.end) + scale.per_day)


def _bucket_days(per_day: float, minimum: float) -> int:
    return max(1, ceil(minimum / per_day))


def listed_marks(lane: Lane, scale: Scale) -> list[tuple[float, float, float]]:
    """(x, width, share of the bucket's days that listed anything) for the lane's listed rows.

    One mark a day from `LISTED_DAY_MIN_PX` a day; one a bucket below it.
    """
    days = sorted(
        d for d in lane.listed if scale.start <= d <= scale.end and not _in_break(scale, d)
    )
    if scale.per_day >= LISTED_DAY_MIN_PX:
        return [(scale.x(d), scale.per_day, 1.0) for d in days]
    size = _bucket_days(scale.per_day, LISTED_DAY_MIN_PX)
    wide = size * scale.per_day
    buckets: dict[int, int] = {}
    for day in days:
        index = _bucket_index(scale, day, wide)
        buckets[index] = buckets.get(index, 0) + 1
    return [
        (scale.x(scale.start) + index * wide, wide, count / size)
        for index, count in sorted(buckets.items())
    ]


def known_ticks(view: AccountTimeline, scale: Scale) -> list[tuple[float, str, int]]:
    """(x, worst problem, count) of the known balances in view; see `KNOWN_TICK_MIN_PX`."""
    known = [
        k for k in view.verification.known
        if scale.start <= k.day <= scale.end and not _in_break(scale, k.day)
    ]
    if scale.per_day >= KNOWN_TICK_MIN_PX:
        return [(scale.x(k.day) + scale.per_day / 2, k.problem, 1) for k in known]
    size = _bucket_days(scale.per_day, KNOWN_TICK_MIN_PX)
    wide = size * scale.per_day
    severity = {UNREPRODUCED: 2, CONFLICT: 1, "": 0}
    buckets: dict[int, tuple[str, int]] = {}
    for item in known:
        index = _bucket_index(scale, item.day, wide)
        worst, count = buckets.get(index, ("", 0))
        buckets[index] = (
            item.problem if severity[item.problem] > severity[worst] else worst,
            count + 1,
        )
    return [
        (scale.x(scale.start) + (index + 0.5) * wide, worst, count)
        for index, (worst, count) in sorted(buckets.items())
    ]


# ---------------------------------------------------------------------------
# Sentences and entries: the chart's text equivalent.


def _lane_name(view: AccountTimeline, source: str) -> str:
    lane = next((item for item in view.lanes if item.source == source), None)
    return KIND_NAMES[lane.kind] if lane is not None else source


def _span(first: date, last: date) -> str:
    """The days of a gap as the What to fetch next page says them: one date for one day."""
    return first.isoformat() if first == last else range_text(first, last)


def _gap_sentence(view: AccountTimeline, gap: Gap) -> str:
    span = _span(gap.first, gap.last)
    days = plural((gap.last - gap.first).days + 1, "day")
    who = _lane_name(view, gap.source)
    if gap.kind == ASK_HOLE:
        reach = (
            "The provider still serves them without anyone present, so ask for them."
            if gap.within_reach
            else "They have passed out of the provider's unattended reach: they need an attended "
            "extend or a file."
        )
        return f"No answered ask reaches {span} ({days}) from the {who.lower()}. {reach}"
    label = GAP_LABELS.get(gap.kind, f"{who}: nothing is held")
    basis = (
        "This is stated by what is held."
        if gap.stated
        else "This is inferred from how regularly the statements held arrive."
    )
    why = f" {gap.why}"
    ends = (
        " Where the missing statement ends is inferred from how regularly they arrive."
        if gap.last_inferred
        else ""
    )
    if gap.reason in HOLE_FACTS:
        # What the held statements prove is told apart from what is guessed about the hole.
        why = f" {HOLE_FACTS[gap.reason]}"
        basis = ends.strip() if gap.reason != "unlisted-rows" else (
            f"{plural(gap.unlisted_rows, 'row')} other sources hold in these days "
            f"{'is' if gap.unlisted_rows == 1 else 'are'} listed by no statement.{ends}"
        )
    probably = ""
    if gap.probably:
        closing = " and ".join(day.isoformat() for day in gap.closings)
        probably = (
            f" Probably {plural(gap.probably, 'statement')} "
            f"{'is' if gap.probably == 1 else 'are'} missing"
            + (f", closing about {closing}." if closing else ".")
        )
    return f"{label} for {span} ({days}).{why}{probably} {basis}".replace("  ", " ")


def _seam_sentence(view: AccountTimeline, seam: Seam) -> str:
    who = _lane_name(view, seam.source).lower()
    day = seam.day.isoformat()
    if seam.verdict == MISSING:
        held = " and ".join(_lane_name(view, s).lower() for s in seam.held_by)
        return (
            f"{plural(seam.missing_rows, 'row')} on {day} "
            f"{'is' if seam.missing_rows == 1 else 'are'} held from the {held} and not from "
            f"this {who}: the capture ended on {day}"
            f"{' part-way through the day' if seam.last_state == PARTIAL else ''}, and no later "
            "capture supplied them."
        )
    if seam.last_state == PARTIAL:
        taken = "taken during that day"
    else:
        taken = "taken at a time nobody recorded, so the day may be cut"
    return (
        f"The {who} capture ending on {day} was {taken}, and no other source covers {day} to "
        "compare it with. Nothing is shown to be missing."
    )


def _marker_sentence(view: AccountTimeline, marker: Marker) -> str:
    day = marker.day.isoformat()
    if marker.kind == UNREPRODUCED:
        return f"The transactions do not add up to the known balance for {day}."
    if marker.kind == CONFLICT:
        return (
            f"Two sources state different balances for {day}: a conflict between sources, "
            "not a fault in the transactions."
        )
    if marker.kind == HELD_BACK:
        return f"The transactions stop adding up to the known balances at {day}."
    who = _lane_name(view, marker.source).lower()
    return (
        f"{plural(marker.count, 'row')} on {day} {'is' if marker.count == 1 else 'are'} listed by "
        f"another source that covers the day and not by the {who}."
    )


def _ledger_href(ref: str, day: date) -> str:
    return f"/ledger?{urlencode({'ref': ref, 'month': f'{day.year:04d}-{day.month:02d}'})}"


def _chart_href(ref: str, first: date, last: date) -> str:
    around = {
        "ref": ref,
        "window": "between",
        "window_held": "between",
        "window_from": (first - timedelta(days=7)).isoformat(),
        "window_to": (last + timedelta(days=7)).isoformat(),
    }
    return f"/balance-chart?{urlencode(around)}"


def _links_for(ref: str, entry: _Entry, *, review: bool = False) -> list[tuple[str, str]]:
    links = [
        (_ledger_href(ref, entry.first), f"The account at {entry.first:%Y-%m}"),
        (_chart_href(ref, entry.first, entry.last), "Balance differences around then"),
        ("/fetch-timeline", "Fetch history"),
    ]
    if review:
        links.append(("/review-flags", "Review flags"))
    return links


# ---------------------------------------------------------------------------
# The chart.


@dataclass
class _Drawn:
    layers: dict[str, list[str]]
    used: set[str]
    entries: list[_Entry]
    defs: str


def _in_window(day_first: date, day_last: date, scale: Scale) -> bool:
    return day_last >= scale.start and day_first <= scale.end


def _link(ident: str, title: str, inner: str) -> str:
    return (
        f'<a href="#e-{ident}" id="m-{ident}"><title>{_esc(title)}</title>{inner}</a>'
    )


def _run_end(lane: Lane, run_last: date) -> tuple[str, float | None]:
    ends = [c for c in lane.captures if c.last == run_last]
    if any(c.last_state == POSSIBLY for c in ends):
        return POSSIBLY, None
    partial = [c for c in ends if c.last_state == PARTIAL and c.fraction is not None]
    if partial and len(partial) == len(ends):
        return PARTIAL, max(c.fraction or 0.0 for c in partial)
    return COMPLETE, None


def _break_marks(
    scale: BrokenScale, top: float, bottom: float, expand_href: Callable[[Quiet], str] | None
) -> tuple[list[str], list[str]]:
    """(the shading under the bars, the cuts and labels over them) for each collapsed stretch.

    Each end of a stretch is a zigzag cut through the axis and every lane, so the axis is not
    read as continuous, and the stretch is labelled with how long it is and linked to the chart
    with it drawn in full. The label sits at mid-height, between the two axes' tick labels.
    """
    under: list[str] = []
    over: list[str] = []
    for quiet, left, right in scale.break_edges():
        words = span_words(quiet.first, quiet.last)
        full = (
            f"{quiet.first.isoformat()} to {quiet.last.isoformat()} - {words}, nothing changed"
        )
        under.append(_rect("cov-break-fill", left, top, right - left, bottom - top, "break"))
        cuts = "".join(
            _el(
                "polyline", "cov-break-cut",
                ("points", _zigzag(edge, top, bottom)), ("fill", "none"),
            )
            for edge in (left, right)
        )
        tokens = words.split()
        lines = [" ".join(tokens[i : i + 2]) for i in range(0, len(tokens), 2)]
        mid = (top + bottom) / 2
        text = "".join(
            f'<text class="cov-break-label" x="{(left + right) / 2:.1f}" '
            f'y="{mid + (n - (len(lines) - 1) / 2) * 13 + 4:.1f}" text-anchor="middle">'
            f"{_esc(line)}</text>"
            for n, line in enumerate(lines)
        )
        hit = _el("rect", "cov-break-hit cov-focus", ("x", f"{left:.1f}"), ("y", f"{top:.1f}"),
                  ("width", f"{right - left:.1f}"), ("height", f"{bottom - top:.1f}"),
                  ("fill", "transparent"))
        inner = f"<title>{_esc(full)}</title>{hit}{cuts}{text}"
        if expand_href is not None:
            over.append(f'<a href="{_esc(expand_href(quiet))}">{inner}</a>')
        else:
            over.append(f"<g>{inner}</g>")
    return under, over


def _zigzag(x: float, top: float, bottom: float) -> str:
    """A cut: a vertical line that steps three units either side of `x` every six."""
    points = []
    y, side = top, 1
    while y < bottom:
        points.append(f"{x + 3 * side:.1f},{y:.1f}")
        y += 6
        side = -side
    points.append(f"{x + 3 * side:.1f},{bottom:.1f}")
    return " ".join(points)


def _draw(
    view: AccountTimeline,
    scale: Scale,
    rows: Sequence[_Row],
    expand_href: Callable[[Quiet], str] | None = None,
) -> _Drawn:
    layers: dict[str, list[str]] = {
        name: [] for name in (
            "hatch", "bands", "rules", "bars", "breaks", "gaps", "seams", "marks", "today"
        )
    }
    used: set[str] = set()
    entries: list[_Entry] = []
    top = AXIS_H
    bottom = rows[-1].y + rows[-1].height
    left, right = scale.x(scale.start), scale.x(scale.end) + scale.per_day
    px = scale.per_day

    if view.first_day > scale.start:
        edge = min(scale.x(view.first_day), right)
        layers["hatch"].append(
            _el("rect", "cov-hatch", ("x", f"{left:.1f}"), ("y", top),
                ("width", f"{edge - left:.1f}"), ("height", bottom - top),
                ("fill", "url(#cov-hatch)"))
        )
    for band in view.verification.bands:
        if band.state == "held" and _in_window(band.first, band.last, scale):
            x0, x1 = _clamp(scale, scale.x(band.first)), _clamp(scale, scale.x(band.last) + px)
            layers["bands"].append(_rect("cov-band-held", x0, top, x1 - x0, bottom - top, "held"))
    if view.verification.protected is not None:
        prot = view.verification.protected
        if _in_window(prot.first, prot.last, scale):
            x0, x1 = _clamp(scale, scale.x(prot.first)), _clamp(scale, scale.x(prot.last) + px)
            layers["bands"].append(
                _rect("cov-band-protected", x0, top, x1 - x0, bottom - top, "protected")
            )
    for marker in view.markers:
        if marker.kind == CONFLICT and scale.start <= marker.day <= scale.end:
            layers["bands"].append(
                _rect("cov-band-conflict", scale.x(marker.day), top,
                      max(px, 3.0), bottom - top, "conflict")
            )

    month = scale.start.replace(day=1)
    while month <= scale.end:
        if month >= scale.start and not _in_break(scale, month):
            cls = "cov-rule-year" if month.month == 1 else "cov-rule"
            layers["rules"].append(_line(cls, scale.x(month), top, scale.x(month), bottom))
        month = (month + timedelta(days=32)).replace(day=1)
    if isinstance(scale, BrokenScale) and scale.breaks:
        under, over = _break_marks(scale, top - AXIS_H, bottom + AXIS_H, expand_href)
        layers["bands"].extend(under)
        layers["breaks"].extend(over)
        used.add("break")

    # Verification lane.
    vrow = rows[0]
    mid = vrow.y + vrow.height / 2
    for band in view.verification.bands:
        if not _in_window(band.first, band.last, scale):
            continue
        x0, x1 = _clamp(scale, scale.x(band.first)), _clamp(scale, scale.x(band.last) + px)
        cls, faint = {
            "agrees": ("cov-ver-agrees", "agrees"),
            "held": ("cov-ver-held", "ver-held"),
        }.get(band.state, ("cov-ver-none", ""))
        layers["bars"].append(_rect(cls, x0, mid - 5, x1 - x0, 10, faint))
    for x, problem, _count in known_ticks(view, scale):
        cls = {UNREPRODUCED: "cov-known-bad", CONFLICT: "cov-known-conflict"}.get(
            problem, "cov-known-ok"
        )
        layers["bars"].append(_line(cls, x, mid - 9, x, mid + 9))

    # One lane per source.
    for row in rows:
        if row.lane is None or row.kind != "lane":
            continue
        lane = row.lane
        bar_y, bar_h = row.y + 7, LANE_H - 14
        for run in lane.runs:
            if not _in_window(run.first, run.last, scale):
                continue
            x0 = _clamp(scale, scale.x(run.first))
            end_state, fraction = _run_end(lane, run.last)
            x1 = scale.x(run.last) + px
            if end_state == PARTIAL and fraction is not None:
                x1 = scale.x(run.last) + fraction * px
            x1 = _clamp(scale, x1)
            solid_end = x1 - (px if end_state == POSSIBLY else 0)
            layers["bars"].append(_rect("cov-bar", x0, bar_y, max(solid_end - x0, 0), bar_h, "bar"))
            if end_state == POSSIBLY:
                layers["bars"].append(
                    _rect("cov-bar-possible", max(solid_end, x0), bar_y, x1 - max(solid_end, x0),
                          bar_h, "possible")
                )
                used.add("possible")
            if run.first >= scale.start:
                layers["bars"].append(
                    _line(EDGE_CLASS[run.first_basis], x0, bar_y - 2, x0, bar_y + bar_h + 2)
                )
                used.add(run.first_basis)
            if run.last <= scale.end:
                layers["bars"].append(
                    _line(EDGE_CLASS[run.last_basis], x1, bar_y - 2, x1, bar_y + bar_h + 2)
                )
                used.add(run.last_basis)
            used.add("covered")
        for capture in lane.captures:
            # A run joins its captures and shows only its weakest outer edges, but where a
            # statement begins on "balances meet" the join itself is the claim, so it is drawn.
            if capture.first_basis in (MEETS, INFERRED) and (
                scale.start <= capture.first <= scale.end
            ):
                x = scale.x(capture.first)
                layers["bars"].append(
                    _line(EDGE_CLASS[capture.first_basis], x, bar_y - 2, x, bar_y + bar_h + 2)
                )
                used.add(capture.first_basis)
        for x, width, share in listed_marks(lane, scale):
            opacity = "" if share >= 1.0 else f' fill-opacity="{min(1.0, 0.3 + 0.7 * share):.1f}"'
            layers["bars"].append(
                f'<rect class="cov-listed" x="{x + 0.5:.1f}" y="{bar_y + bar_h - 5:.1f}" '
                f'width="{max(width - 1, 1):.1f}" height="5"{opacity}/>'
            )
            used.add("listed")
        for x in notch_xs(lane, scale):
            layers["bars"].append(_line("cov-notch", x, bar_y - 4, x, bar_y + bar_h + 4))
            used.add("notch")
        if lane.kind == STATEMENT and lane.trailing is not None:
            first, last = lane.trailing
            # The quiet stretch stops where a gap on this lane begins: a newer statement that
            # is needed is not a statement that does not exist yet.
            starts = [g.first for g in view.gaps if g.source == lane.source and first <= g.first]
            if starts:
                last = min(last, min(starts) - timedelta(days=1))
            if first <= last and _in_window(first, last, scale):
                x0, x1 = _clamp(scale, scale.x(first)), _clamp(scale, scale.x(last) + px)
                layers["gaps"].append(
                    _el("rect", "cov-unavailable", ("x", f"{x0:.1f}"), ("y", f"{bar_y:.1f}"),
                        ("width", f"{max(x1 - x0, 0):.1f}"), ("height", f"{bar_h:.1f}"),
                        ("fill", "url(#cov-unavailable)"))
                )
                if x1 - x0 >= 100:
                    layers["gaps"].append(
                        f'<text class="cov-dim" x="{x0 + 4:.1f}" y="{bar_y + bar_h / 2 + 4:.1f}">'
                        "not available yet</text>"
                    )
                used.add("unavailable")
            if lane.next_expected is not None and lane.next_expected >= scale.start:
                at_x = _clamp(scale, scale.x(min(lane.next_expected, scale.end)) + px / 2)
                text = f"next statement expected {lane.next_expected.isoformat()}"
                anchor = ' text-anchor="end"' if at_x > scale.width / 2 else ""
                layers["gaps"].append(
                    f'<g class="cov-expected"><title>{_esc(text)}</title>'
                    + _el("polygon", "cov-expected-mark",
                          ("points", f"{at_x:.1f},{bar_y - 1:.1f} {at_x + 5:.1f},{bar_y + 5:.1f} "
                                     f"{at_x:.1f},{bar_y + 11:.1f} {at_x - 5:.1f},{bar_y + 5:.1f}"))
                    + f'<text class="cov-dim" x="{at_x + (-8 if anchor else 8):.1f}" '
                    f'y="{bar_y - 3:.1f}"{anchor}>expected '
                    f"{lane.next_expected.isoformat()}</text></g>"
                )
                used.add("expected")

    lane_row = {row.lane.source: row for row in rows if row.lane is not None}
    first_lane = next(iter(lane_row.values()), None)
    verification_row = rows[0]

    # Gaps.
    seen_anchors: set[str] = set()
    for gap in view.gaps:
        row_of_gap: _Row | None
        if gap.kind in VERIFICATION_GAPS:
            row_of_gap, at_h = verification_row, VERIFICATION_H
        else:
            row_of_gap, at_h = lane_row.get(gap.source, first_lane), LANE_H
        if row_of_gap is None or not _in_window(gap.first, gap.last, scale):
            continue
        at = row_of_gap
        ident = gap.anchor
        suffix = 1
        while ident in seen_anchors:
            suffix += 1
            ident = f"{gap.anchor}-{suffix}"
        seen_anchors.add(ident)
        x0, x1 = _clamp(scale, scale.x(gap.first)), _clamp(scale, scale.x(gap.last) + px)
        sentence = _gap_sentence(view, gap)
        if gap.last_inferred:
            # A hole whose existence is a fact but whose end is a guess: a firm start and an
            # outline that stays open at the soft end.
            top_y, bottom_y = at.y + 5, at.y + at_h - 5
            inner = (
                _el("polyline", "cov-gap cov-focus",
                    ("points", f"{x1:.1f},{top_y:.1f} {x0:.1f},{top_y:.1f} "
                               f"{x0:.1f},{bottom_y:.1f} {x1:.1f},{bottom_y:.1f}"),
                    ("fill", "none"))
                + _el("rect", "cov-gap-hit", ("x", f"{x0:.1f}"), ("y", f"{top_y:.1f}"),
                      ("width", f"{max(x1 - x0, 0):.1f}"), ("height", f"{bottom_y - top_y:.1f}"),
                      ("fill", "transparent"))
            )
        else:
            inner = _rect("cov-gap cov-focus", x0, at.y + 5, x1 - x0, at_h - 10)
        inner += _line("cov-gap-firm", x0, at.y + 5, x0, at.y + at_h - 5)
        if x1 - x0 >= GAP_LABEL_PX:
            label = f"gap {_span(gap.first, gap.last)}"
            inner += f'<text x="{x0 + 5:.1f}" y="{at.y + at_h / 2 + 4:.1f}">{_esc(label)}</text>'
        layers["gaps"].append(_link(ident, sentence, inner))
        used.add("gap")
        entries.append(_Entry(ident, "fetch", sentence, gap.first, gap.last, gap=gap))

    # Seams that need a look.
    for seam in view.seams_to_check:
        seam_row = lane_row.get(seam.source)
        if seam_row is None or not scale.start <= seam.day <= scale.end:
            continue
        at = seam_row
        ident = seam_anchor(seam.source, seam.day)
        fraction = seam.fraction if seam.last_state == PARTIAL and seam.fraction else 1.0
        x = scale.x(seam.day) + fraction * px
        kind = "seam-red" if seam.verdict == MISSING else "seam-amber"
        sentence = _seam_sentence(view, seam)
        layers["seams"].append(_link(ident, sentence, MARKS[kind].draw(x, at.y + 9)))
        used.add(kind)
        entries.append(_Entry(ident, "seam", sentence, seam.day, seam.day))

    # Issues lane and unmatched marks.
    irow = rows[-1]
    size = _bucket_days(px, MERGE_PX)
    wide = size * px
    merged: dict[tuple[str, str, int], list[Marker]] = {}
    for marker in view.markers:
        if not scale.start <= marker.day <= scale.end:
            continue
        index = _bucket_index(scale, marker.day, wide)
        merged.setdefault((marker.kind, marker.source, index), []).append(marker)
    for (kind, source, index), group in sorted(merged.items()):
        ident = marker_anchor(kind, source, group[0].day)
        count = sum(m.count for m in group)
        x = scale.x(scale.start) + (index + 0.5) * wide
        lead = group[0]
        on_lane = lane_row.get(source) if kind == UNMATCHED else None
        y = on_lane.y + LANE_H - 9 if on_lane is not None else irow.y + irow.height / 2
        sentence = (
            _marker_sentence(view, lead)
            if len(group) == 1
            else f"{plural(len(group), 'marks')} of this kind between "
            f"{group[0].day.isoformat()} and {group[-1].day.isoformat()}: "
            + _marker_sentence(view, group[0])
        )
        inner = MARKS[kind].draw(x, y)
        if len(group) > 1:
            inner += f'<text x="{x + 7:.1f}" y="{y + 4:.1f}">{count}</text>'
        layers["marks"].append(_link(ident, sentence, inner))
        used.add(kind)
        entries.append(
            _Entry(
                ident, "look", sentence, group[0].day, group[-1].day,
                aliases=[marker_anchor(m.kind, m.source, m.day) for m in group[1:]],
            )
        )

    if scale.start <= view.today <= scale.end:
        x = scale.x(view.today) + px / 2
        layers["today"].append(_line("cov-today", x, top - 6, x, bottom + 6))
        near_right = x > scale.width - 44
        anchor = ' text-anchor="end"' if near_right else ""
        layers["today"].append(
            f'<text x="{x + (-4 if near_right else 4):.1f}" y="{top - 8}"{anchor}>today</text>'
        )

    defs = (
        '<defs><pattern id="cov-hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><line class="cov-hatch-line" x1="0" y1="0" x2="0" '
        'y2="6"/></pattern><pattern id="cov-unavailable" width="4" height="4" '
        'patternUnits="userSpaceOnUse"><line class="cov-unavailable-line" x1="0" y1="0" '
        'x2="0" y2="4"/></pattern></defs>'
    )
    return _Drawn(layers, used, entries, defs)


_PAINT = ("hatch", "bands", "rules", "bars", "breaks", "gaps", "seams", "marks", "today")


def collapsed_words(scale: Scale) -> str:
    """How much of the axis is abbreviated, or "" where none is: "3 quiet stretches collapsed,
    5 years 8 months in all". Said in the verdict and the chart's description, because a
    chart with breaks is not to scale."""
    if not isinstance(scale, BrokenScale) or not scale.breaks:
        return ""
    total = sum(b.days for b in scale.breaks)
    return (
        f"{plural(len(scale.breaks), 'quiet stretch', 'quiet stretches')} collapsed, "
        f"{span_words(scale.start, scale.start + timedelta(days=total - 1))} in all"
    )


def not_to_scale(scale: Scale) -> str:
    """The sentence, with its leading space, that says how much is abbreviated; "" if none."""
    said = collapsed_words(scale)
    return f" {said[0].upper()}{said[1:]}: the chart is not to scale." if said else ""


def _axis_for(scale: Scale, *, month_y: Sequence[float]) -> str:
    """The balance chart's axis where the axis is continuous. Where it is cut, the same ticks
    and labels, except those a cut would overlap or that fall inside a collapsed stretch."""
    if not isinstance(scale, BrokenScale) or not scale.breaks:
        return _axis(scale, month_y=month_y)
    edges = [(left, right) for _, left, right in scale.break_edges()]

    def clear(x: float, width: float) -> bool:
        return not any(x - 6 <= right and left <= x + width for left, right in edges)

    parts: list[str] = []
    month = scale.start.replace(day=1)
    while month <= scale.end:
        if month >= scale.start and not scale.in_break(month) and clear(scale.x(month), 62):
            for y in month_y:
                parts.append(_label(scale.x(month) + 3, y, f"{month:%b} {month.year}"))
        month = (month + timedelta(days=32)).replace(day=1)
    step = 1 if scale.per_day >= 60 else 7 if scale.per_day >= 14 else 0
    if step:
        # A window that begins mid-month has no month tick at its left edge, so the first day is
        # named in full, as the continuous axis does.
        named_start = scale.fitted and scale.start.day != 1
        if named_start:
            parts.append(
                _label(scale.x(scale.start) + 2, month_y[0] + 13,
                       f"{scale.start.day} {scale.start:%b} {scale.start.year}",
                       ' fill-opacity=".7"')
            )
        for offset in range(scale.days):
            day = scale.start + timedelta(days=offset)
            if (
                (day.day - 1) % step == 0
                and not (named_start and offset * scale.per_day < 80)
                and day.day != 1
                and not scale.in_break(day)
                and clear(scale.x(day), 16)
                and not any(
                    0 <= scale.x(day) - scale.x(first_of) < 62
                    for first_of in (day.replace(day=1),)
                    if first_of >= scale.start and not scale.in_break(first_of)
                )
            ):
                parts.append(
                    _label(scale.x(day) + 2, month_y[0] + 13, str(day.day), ' fill-opacity=".7"')
                )
    return "".join(parts)


def chart_svg(
    view: AccountTimeline,
    scale: Scale,
    *,
    fit: bool,
    expand_href: Callable[[Quiet], str] | None = None,
) -> tuple[str, _Drawn, list[_Row]]:
    rows = _rows(view)
    drawn = _draw(view, scale, rows, expand_href)
    height = _height(rows)
    width = scale.width
    desc = (
        f"Coverage of {plural(len(view.lanes), 'source')} from {scale.start.isoformat()} to "
        f"{scale.end.isoformat()}, with {plural(len(view.gaps), 'gap')} and "
        f"{plural(len(drawn.entries), 'marked place')}."
        + not_to_scale(scale)
    )
    axis = _axis_for(scale, month_y=[14]) + _axis_for(scale, month_y=[height - AXIS_H + 14])
    size = (
        'style="width:100%;height:auto"' if fit else f'width="{width:.0f}" height="{height:.0f}"'
    )
    svg = (
        f'<svg class="cov-svg" role="img" aria-labelledby="cov-t cov-d" {size} '
        f'viewBox="0 0 {width:.0f} {height:.0f}" xmlns="http://www.w3.org/2000/svg">'
        f'<title id="cov-t">Coverage timeline for {_esc(view.label)}</title>'
        f'<desc id="cov-d">{_esc(desc)}</desc>{drawn.defs}'
        + "".join(drawn.layers["hatch"])
        + "".join("".join(drawn.layers[n]) for n in _PAINT[1:3])
        + axis
        + "".join("".join(drawn.layers[n]) for n in _PAINT[3:])
        + "</svg>"
    )
    return svg, drawn, rows


def _labels(view: AccountTimeline, rows: Sequence[_Row], height: float) -> str:
    def cell(text: str, extra: str, h: float) -> str:
        return f'<div class="cov-label" style="height:{h:.0f}px">{text}{extra}</div>'

    out = [f'<div class="cov-label" style="height:{AXIS_H}px"></div>']
    for row in rows:
        if row.kind == "verification":
            out.append(cell("Adds up", "", row.height))
        elif row.lane is not None:
            out.append(
                cell(
                    _esc(KIND_NAMES[row.lane.kind]),
                    f"<small>{code_html(row.lane.source)}</small>",
                    row.height,
                )
            )
        else:
            out.append(cell("To look at", "", row.height))
    out.append(f'<div class="cov-label" style="height:{AXIS_H}px"></div>')
    return f'<div class="cov-labels" aria-hidden="true">{"".join(out)}</div>'


def _key(used: set[str]) -> str:
    items: list[str] = []

    def swatch(inner: str, text: str) -> None:
        items.append(
            f'<li><svg width="30" height="18" viewBox="0 0 30 18" aria-hidden="true" '
            f'class="cov-svg">{inner}</svg><span>{_esc(text)}</span></li>'
        )

    if "covered" in used:
        swatch(_rect("cov-bar", 2, 3, 26, 12, "bar"), "A source reaches these days")
    if "listed" in used:
        swatch(
            _rect("cov-bar", 2, 3, 26, 12, "bar") + '<rect class="cov-listed" x="4" y="10" '
            'width="5" height="5"/><rect class="cov-listed" x="16" y="10" width="5" height="5"/>',
            "Days on which the source listed rows",
        )
    if "possible" in used:
        swatch(
            _rect("cov-bar", 2, 3, 14, 12, "bar") + _rect("cov-bar-possible", 16, 3, 12, 12,
                                                            "possible"),
            "A last day that may be cut: when the file was exported is not recorded",
        )
    if "notch" in used:
        swatch(_line("cov-notch", 15, 1, 15, 17), "Where one capture begins or ends")
    if "gap" in used:
        swatch(_rect("cov-gap", 2, 3, 26, 12), "Days to fill")
    if "unavailable" in used:
        swatch(
            _el("rect", "cov-unavailable", ("x", 2), ("y", 3), ("width", 26), ("height", 12),
                ("fill", "url(#cov-unavailable)")),
            "Not available yet: the next statement does not exist until its period ends",
        )
    if "expected" in used:
        swatch(
            _el("polygon", "cov-expected-mark", ("points", "15,2 20,8 15,14 10,8")),
            "When the next statement is expected to close",
        )
    for kind, style in MARKS.items():
        if kind in used:
            swatch(style.draw(15, 9), style.label)
    edges = [
        (STATED, "thick edge"), ("asked", "thin edge"), (MEETS, "dotted edge"),
        (OBSERVED, "dashed edge"), (INFERRED, "faint dotted edge"),
    ]
    edge_words = "; ".join(
        f"a {name} is {EDGE_NAMES[basis]}, which is {CERTAINTY_WORDS[basis]}"
        for basis, name in edges
        if basis in used
    )
    edge_line = f'<p class="cov-key-edge">Edges: {_esc(edge_words)}.</p>' if edge_words else ""
    edge_swatches = []
    for basis, _ in edges:
        if basis in used:
            edge_swatches.append(
                f'<li><svg width="30" height="18" viewBox="0 0 30 18" aria-hidden="true" '
                f'class="cov-svg">{_line(EDGE_CLASS[basis], 15, 1, 15, 17)}</svg>'
                f"<span>{_esc(EDGE_NAMES[basis].capitalize())} edge</span></li>"
            )
    return (
        f'<ul class="cov-key" aria-label="Key">{"".join(items)}{"".join(edge_swatches)}</ul>'
        + edge_line
    )


def _entries_html(ref: str, entries: Sequence[_Entry]) -> str:
    groups = (("fetch", "What to fetch"), ("look", "Where to look"), ("seam", "Seams"))
    out = ['<div class="cov-entries">']
    for key, title in groups:
        found = [e for e in entries if e.group == key]
        if not found:
            continue
        out.append(f"<h3>{title}</h3>")
        for entry in found:
            links = entry.links or _links_for(ref, entry, review=key == "look")
            if key == "fetch":
                links = [("/gaps", "What to fetch next"), *links]
            items = "".join(
                f'<li><a href="{_esc(href)}">{_esc(text)}</a></li>' for href, text in links
            )
            out.append(
                f'<div class="cov-entry" id="e-{entry.ident}">'
                + "".join(f'<span id="e-{alias}"></span>' for alias in entry.aliases)
                + f"<p>{_esc(entry.sentence)}</p>"
                f'<ul class="cov-links"><li><a href="#m-{entry.ident}">Find it on the chart</a>'
                f"</li>{items}</ul></div>"
            )
    out.append("</div>")
    return "".join(out) if entries else ""


def _next_list(view: AccountTimeline, drawn: _Drawn) -> str:
    """What to fetch next, one line each, above the chart: the question he asks of this page.

    Each line says which way in and which days, and links to the entry that says more.
    """
    gaps = [e for e in drawn.entries if e.group == "fetch"]
    if not gaps:
        return ""
    items = "".join(
        f'<li><a href="#e-{e.ident}">{_esc(_lane_name(view, e.gap.source))}: '
        f"{_esc(_span(e.first, e.last))}</a></li>"
        for e in gaps[:4]
        if e.gap is not None
    )
    more = len(gaps) - 4
    tail = f"<li>and {more} more, listed below the chart</li>" if more > 0 else ""
    return f'<ul class="cov-next" aria-label="Fetch next">{items}{tail}</ul>'


def verdict(view: AccountTimeline, drawn: _Drawn, scale: Scale) -> str:
    gaps = sum(1 for e in drawn.entries if e.group == "fetch")
    seams = sum(1 for e in drawn.entries if e.group == "seam")
    looks = sum(1 for e in drawn.entries if e.group == "look")
    sources = [lane for lane in view.lanes if lane.kind != TYPED]
    head = f"{plural(len(sources), 'source')}, {range_text(scale.start, scale.end)}"
    if not (gaps or seams or looks):
        said = f"{head}: nothing to fetch, nothing to check, and nothing to look at."
    else:
        said = (
            f"{head}: {plural(gaps, 'gap')} to fill, {plural(seams, 'seam')} to check, "
            f"{plural(looks, 'thing')} to look at."
        )
    return said + not_to_scale(scale) + expected_sentence(view)


def expected_sentence(view: AccountTimeline) -> str:
    """The sentence naming when the next statement is expected, where one can be said.

    Said only where nothing is waiting on a statement: while a gap names newer statements that are
    needed, "expected" would be the wrong word for them.
    """
    days = [lane.next_expected for lane in view.lanes if lane.next_expected is not None]
    waiting = any(gap.kind == "newer-statement" for gap in view.gaps) or any(
        lane.due for lane in view.lanes
    )
    if not days or waiting:
        return ""
    return f" Next statement expected about {min(days).isoformat()}."


# ---------------------------------------------------------------------------
# The window, and the page.


@dataclass(frozen=True)
class Chosen:
    choice: WindowChoice
    start: date | None
    end: date | None
    words: str
    drawn: bool


def choose_window(
    fields: Mapping[str, str] | None, *, today: date, first_day: date, default_key: str = "m12"
) -> Chosen:
    """The days the request asks to draw: its window, else the last twelve months."""
    asked = dict(fields) if fields else {"window": default_key, "window_held": default_key}
    choice = read_window_choice(asked, today=today, held_from=first_day)
    if choice.refusal:
        return Chosen(choice, None, None, f"<p class=\"warn\">{_esc(choice.refusal)}</p>", False)
    if choice.spec is None:
        return Chosen(choice, first_day, today, "", True)
    window = resolve(choice.spec, today=today, held_from=first_day)
    if window.empty or window.first is None or window.last is None:
        return Chosen(choice, None, None, f"<p class=\"warn\">{_esc(window.empty_reason)}</p>",
                      False)
    return Chosen(choice, window.first, window.last, "", True)


def pick_scale(
    chosen: Chosen, mode: str, *, windowed: bool, breaks: Sequence[Quiet] = ()
) -> Scale | None:
    """The scale for the chosen days. The room a collapsed stretch gives up is spent on the
    days that are drawn: the pixels a day takes are worked out as if the window were only those."""
    if chosen.start is None or chosen.end is None:
        return None
    total = (chosen.end - chosen.start).days + 1
    drawn_days = total - sum(b.days for b in breaks)
    shaped_end = chosen.start + timedelta(days=drawn_days - 1)
    if mode == WIDE:
        shaped = choose_scale(chosen.start, shaped_end, chosen.start, shaped_end, fitted=False)
    elif mode == FIT:
        room = VIEW_WIDTH - 2 * EDGE - len(breaks) * BREAK_W
        shaped = Scale(chosen.start, shaped_end, max(room / drawn_days, 0.05))
    elif windowed:
        shaped = choose_scale(chosen.start, shaped_end, chosen.start, shaped_end, fitted=True)
    else:
        shaped = Scale(chosen.start, shaped_end, PIXELS_PER_DAY)
    if not breaks:
        return shaped
    return BrokenScale(
        chosen.start, chosen.end, shaped.per_day, fitted=shaped.fitted, breaks=tuple(breaks)
    )


def _mode_links(ref: str, fields: Mapping[str, str], mode: str) -> str:
    base = {"ref": ref, **fields}

    def href(scale: str) -> str:
        query = {**base, **({SCALE_FIELD: scale} if scale else {})}
        return _esc(f"/coverage-timeline?{urlencode(query)}")

    parts = []
    if mode != "":
        parts.append(f'<a class="tap" href="{href("")}">Read at day scale and scroll</a>')
    if mode != FIT:
        parts.append(f'<a class="tap" href="{href(FIT)}">Fit it to the screen</a>')
    parts.append(
        f'<a class="tap" href="{href(WIDE)}" target="_blank" rel="noopener">'
        "Draw it very wide to pan across (opens in a new tab)</a>"
    )
    return '<p class="linkrow">' + " ".join(parts) + "</p>"


def _expand_text(ranges: Sequence[tuple[date, date]]) -> str:
    return ",".join(f"{a.isoformat()}_{b.isoformat()}" for a, b in ranges)


def parse_expanded(text: str) -> list[tuple[date, date]]:
    """The stretches a request asks to see in full; anything malformed is ignored."""
    found: list[tuple[date, date]] = []
    for part in text.split(","):
        first, _, last = part.partition("_")
        try:
            found.append((date.fromisoformat(first), date.fromisoformat(last)))
        except ValueError:
            continue
    return found


def _quiet_list(
    quiet: Sequence[Quiet], href: Callable[[Quiet], str]
) -> str:
    """The text of what the chart skipped, each with the link that draws it in full."""
    if not quiet:
        return ""
    items = "".join(
        f'<li>{_esc(q.first.isoformat())} to {_esc(q.last.isoformat())} - '
        f"{_esc(span_words(q.first, q.last))}, nothing changed. "
        f'<a class="tap" href="{_esc(href(q))}">Show these days</a></li>'
        for q in quiet
    )
    return f'<ul class="cov-quiet-list" aria-label="Quiet stretches skipped">{items}</ul>'


def _strip(
    view: AccountTimeline, first: date, last: date
) -> tuple[str, _Drawn, list[_Row], Scale]:
    """A short steady-state strip: the first fortnight of a window in which nothing changes."""
    strip = Scale(first, min(last, first + timedelta(days=13)), 14.0)
    svg, drawn, rows = chart_svg(view, strip, fit=False)
    return svg, drawn, rows, strip


def render_account_timeline(
    view: AccountTimeline,
    *,
    fields: Mapping[str, str] | None,
    mode: str = "",
    every_day: bool = False,
    expanded: Sequence[tuple[date, date]] = (),
    keep: Sequence[tuple[date, date]] = (),
) -> bytes:
    """The account's timeline page for the window `fields` ask for (twelve months if none).

    Quiet stretches are collapsed unless `every_day`; `expanded` are stretches the request has
    opened; `keep` are days never collapsed (the month an account page is showing).
    """
    heading = f"<p>{AccountShown.named(view.ref, view.label).inline()}</p>"
    chosen = choose_window(fields, today=view.today, first_day=view.first_day)
    carried = {
        k: v for k, v in chosen.choice.fields.items() if k in WINDOW_FIELDS
    } if fields else {"window": "m12", "window_held": "m12"}
    extras: dict[str, str] = {}
    if every_day:
        extras[DAYS_FIELD] = EVERY_DAY
    if expanded:
        extras[EXPAND_FIELD] = _expand_text(expanded)
    form = (
        '<form method="get" action="/coverage-timeline" data-window-form>'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        + (f'<input type="hidden" name="{SCALE_FIELD}" value="{_esc(mode)}">' if mode else "")
        + "".join(
            f'<input type="hidden" name="{name}" value="{_esc(value)}">'
            for name, value in extras.items()
        )
        + f'<button type="submit" name="window" value="{KEEP}" class="visually-hidden" '
        'tabindex="-1" aria-hidden="true">Redraw</button>'
        + window_controls(chosen.choice, today=view.today.isoformat())
        + "</form>"
    )
    plain = pick_scale(chosen, mode, windowed=True)
    if plain is None or chosen.start is None or chosen.end is None:
        return render_page(
            "Coverage timeline", heading + form + chosen.words, wide=True,
            heading="Coverage timeline",
        )
    quiet: tuple[Quiet, ...] = (
        () if every_day
        else quiet_stretches(view, chosen.start, chosen.end, keep=keep, expanded=expanded)
    )
    whole = len(quiet) == 1 and (quiet[0].first, quiet[0].last) == (chosen.start, chosen.end)

    def link(**more: str) -> str:
        query = {"ref": view.ref, **carried, **({SCALE_FIELD: mode} if mode else {}), **more}
        return f"/coverage-timeline?{urlencode(query)}"

    def expand_href(opened: Quiet) -> str:
        return link(**{EXPAND_FIELD: _expand_text([*expanded, (opened.first, opened.last)])})

    scale: Scale
    if whole:
        svg, drawn, rows, scale = _strip(view, chosen.start, chosen.end)
        verdict_text = (
            f"Nothing changed from {chosen.start.isoformat()} to {chosen.end.isoformat()} "
            f"({span_words(chosen.start, chosen.end)}): every source held one state throughout. "
            "The strip shows the first two weeks of it."
        )
    else:
        scale = pick_scale(chosen, mode, windowed=True, breaks=quiet) or plain
        svg, drawn, rows = chart_svg(view, scale, fit=mode == FIT, expand_href=expand_href)
        verdict_text = verdict(view, drawn, scale)
    height = _height(rows)
    if mode == FIT and not whole:
        frame = (
            f'<div class="cov-frame">{_labels(view, rows, height)}'
            f'<div class="cov-fit">{svg}</div></div>'
        )
    else:
        # The scroller reads right to left so that, where the chart is wider than the screen, it
        # opens at its newest end; the chart's own box reads left to right as ever.
        frame = (
            f'<div class="cov-frame">{_labels(view, rows, height)}'
            f'<div class="cov-scroll" tabindex="0" role="region" '
            f'aria-label="Coverage timeline, scrolls sideways, opens at the newest day">'
            f'<div class="cov-inner" style="width:{scale.width:.0f}px">'
            f"{svg}</div></div></div>"
        )
    collapse_links = (
        f'<a class="tap" href="{_esc(link(**{DAYS_FIELD: EVERY_DAY}))}">Show every day</a>'
        if quiet and not whole
        else f'<a class="tap" href="{_esc(link())}">Collapse the quiet stretches</a>'
        if every_day or expanded or whole
        else ""
    )
    body = (
        heading
        + f'<p class="cov-verdict" data-verdict>{_esc(verdict_text)}</p>'
        + account_notes(view)
        + _next_list(view, drawn)
        + frame
        + (f'<p class="linkrow">{collapse_links}</p>' if collapse_links else "")
        + _quiet_list(quiet if not whole else (), expand_href)
        + '<details class="cov-keybox"><summary>Key to the marks</summary>'
        + _key(drawn.used)
        + "</details>"
        + form
        + _mode_links(view.ref, {**carried, **extras}, mode)
        + _entries_html(view.ref, drawn.entries)
        + f'<p><a class="tap" href="/ledger?ref={_esc(view.ref)}">Back to the account</a></p>'
    )
    return render_page("Coverage timeline", body, wide=True, heading="Coverage timeline")


#: The household lane is drawn in months, in this many units at most across.
HOUSEHOLD_WIDTH = 320
_MONTH_WORDS = {"full": "every day covered", "part": "part covered", "none": "not covered"}


def month_cells(view: AccountTimeline) -> list[tuple[date, str]]:
    """Each month from the account's first to today's: "full" only if EVERY day of it is
    covered by some source, "part" if some day is, "none" otherwise. The union of the lanes'
    runs is what is covered; typed entries cover nothing (`coverage_timeline`)."""
    runs = sorted((r.first, r.last) for lane in view.lanes for r in lane.runs)
    merged: list[list[date]] = []
    for first, last in runs:
        if merged and first <= merged[-1][1] + timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    cells: list[tuple[date, str]] = []
    month = view.first_day.replace(day=1)
    while month <= view.today:
        following = (month + timedelta(days=32)).replace(day=1)
        last = min(following - timedelta(days=1), view.today)
        total = (last - month).days + 1
        covered = sum(
            max(0, (min(b, last) - max(a, month)).days + 1) for a, b in merged
        )
        cells.append((month, "full" if covered == total else "part" if covered else "none"))
        month = following
    return cells


def _household_lane(view: AccountTimeline) -> str:
    cells = month_cells(view)
    size = max(3.0, min(14.0, HOUSEHOLD_WIDTH / max(len(cells), 1)))
    body = []
    for index, (month, state) in enumerate(cells):
        cls = {"full": "cov-bar", "part": "cov-bar-possible", "none": "cov-ver-none"}[state]
        faint = {"full": "bar", "part": "possible", "none": ""}[state]
        body.append(
            f"<g><title>{month:%Y-%m}: {_MONTH_WORDS[state]}</title>"
            f"{_rect(cls, index * size, 2, size - 1, 16, faint)}</g>"
        )
    width = len(cells) * size
    return (
        f'<svg class="cov-svg" role="img" width="{width:.0f}" height="20" '
        f'viewBox="0 0 {width:.0f} 20" aria-label="Months covered, by month">{"".join(body)}</svg>'
    )


def account_notes(view: AccountTimeline) -> str:
    """What the lanes cannot say about this kind of account, said once."""
    notes = []
    if view.is_space:
        notes.append(
            "This is a Space: exports and statements cannot see Spaces, so only the bank's own "
            "feed lists its rows."
        )
    if view.made_by_obdi and not view.lanes:
        notes.append(MADE_BY_OBDI_WORDS)
    return "".join(f'<p class="muted">{_esc(note)}</p>' for note in notes)


#: What an account says whose rows no source lists.
MADE_BY_OBDI_WORDS = (
    "No source lists the rows of this account: obdi makes them from another account's "
    "withdrawals, and its balances are stated by hand."
)


def _household_sentence(view: AccountTimeline) -> str:
    if view.made_by_obdi and not view.lanes:
        return MADE_BY_OBDI_WORDS
    gaps = len(view.gaps)
    seams = len(view.seams_to_check)
    looks = len(view.markers)
    bands = view.verification.bands
    agrees = next((b for b in reversed(bands) if b.state == "agrees"), None)
    standing = (
        f"Adds up to every known balance up to {agrees.last.isoformat()}"
        if agrees is not None
        else "Not checked against any known balance"
    )
    todo = (
        f"{plural(gaps, 'gap')} to fill, {plural(seams, 'seam')} to check, "
        f"{plural(looks, 'thing')} to look at"
        if gaps or seams or looks
        else "nothing to fetch and nothing to look at"
    )
    return f"{standing}; {todo}."


def render_household(views: Sequence[AccountTimeline]) -> bytes:
    """One compact lane per account, each label a link to that account's full timeline.

    The lane is drawn by the month: a month is covered only if every day of it is, and one
    covered in part looks different. The one axis is the months themselves, so each lane has
    the same cell width only where the accounts' lives are the same length; a lane begins at
    its account's own first month and says so in its label.
    """
    if not views:
        return render_page(
            "Coverage timeline",
            "<p>No account holds rows yet, so there is no coverage to draw.</p>",
            heading="Coverage timeline",
        )
    items = []
    for view in views:
        first = view.first_day
        items.append(
            f'<li><p class="cov-lane-name"><a href="/coverage-timeline?ref='
            f'{_esc(quote(view.ref, safe=""))}">{AccountShown.named(view.ref, view.label).inline()}'
            f"</a></p>{_household_lane(view)}"
            f'<p class="muted">{"" if not view.lanes else f"From {_esc(first.isoformat())}. "}'
            f"{_esc(_household_sentence(view))}</p></li>"
        )
    body = (
        f"<p>{plural(len(views), 'account')}, each drawn by the month: a filled month is "
        "covered on every day by some source, a dashed one only in part.</p>"
        f'<ul class="cov-household">{"".join(items)}</ul>'
    )
    return render_page("Coverage timeline", body, wide=True, heading="Coverage timeline")


def _window_of(fields: Mapping[str, list[str]]) -> dict[str, str] | None:
    found = {name: fields[name][0] for name in WINDOW_FIELDS if fields.get(name)}
    return found or None


def _today() -> date:
    """The day the page is relative to: the one place it reads the clock, and a seam."""
    return datetime.now(UTC).date()


class CoverageTimelinePages:
    """The page's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _coverage_timeline_get(self, params: dict[str, list[str]]) -> None:
        hook = self.bound_config.coverage_timeline_data
        if hook is None:
            self._respond(404, render_page("Not available", "<p>No timeline is wired.</p>"))
            return
        ref = (params.get("ref", [""])[0] or "").strip()
        mode = (params.get(SCALE_FIELD, [""])[0] or "").strip()
        mode = mode if mode in (FIT, WIDE) else ""
        if not ref:
            household = self.bound_config.coverage_timeline_household
            if household is None:
                self._respond(404, render_page("Not available", "<p>Say which account.</p>"))
                return
            self._respond(200, render_household(household(_today())))
            return
        try:
            view = hook(ref, _today())
        except Exception as fault:
            # Not str(fault): its text is not under this module's control.
            say("coverage_timeline.fault", kind=type(fault).__name__)
            self._respond(
                500, render_page("Timeline failed", "<p>The timeline could not be built.</p>")
            )
            return
        if view is None:
            self._respond(
                404,
                render_page(
                    "Coverage timeline",
                    '<p class="bad"><strong>Unknown account.</strong> Nothing is held under '
                    "this reference.</p>",
                ),
            )
            return
        self._respond(
            200,
            render_account_timeline(
                view,
                fields=_window_of(params),
                mode=mode,
                every_day=(params.get(DAYS_FIELD, [""])[0] or "") == EVERY_DAY,
                expanded=parse_expanded(params.get(EXPAND_FIELD, [""])[0] or ""),
            ),
        )


__all__ = [
    "AXIS_H",
    "BREAK_W",
    "DAYS_FIELD",
    "EVERY_DAY",
    "EXPAND_FIELD",
    "FIT",
    "GAP_LABEL_PX",
    "KNOWN_TICK_MIN_PX",
    "LISTED_DAY_MIN_PX",
    "MARKS",
    "MAX_PIXELS_PER_DAY",
    "MERGE_PX",
    "NOTCH_MIN_PX",
    "SCALE_FIELD",
    "TARGET_RANGE_WIDTH",
    "WIDE",
    "BrokenScale",
    "CoverageTimelinePages",
    "chart_svg",
    "collapsed_words",
    "known_ticks",
    "listed_marks",
    "month_cells",
    "notch_xs",
    "parse_expanded",
    "pick_scale",
    "render_account_timeline",
    "render_household",
    "verdict",
]
