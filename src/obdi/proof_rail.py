"""The proof rail: one thin bar for an account's history, from its first known day to today.

Two halves, so a page that draws the rail and a page that draws forty of them cannot disagree:

- `build_rail` is pure. Dates in (the account's standing and protection, the first row, today),
  segments out. It reads nothing and holds no figure: a rail is dates, and so reads the same
  masked or not.
- `rail_svg` lays the segments out as one inline SVG. It names no colour: every part carries a
  class, and the stylesheet gives the class a token (`.rail-*` in `stylesheet_account`), so a
  change of scheme is a change of tokens. The rail also carries its own text alternative
  (`rail_text`), because a bar of colour says nothing to a screen reader or in greyscale.

WHAT EACH KIND OF SEGMENT SAYS (the sentence for each is `_SENTENCES`, the one place the page's
words for them are written):

- `agree`: the rows reproduce the known balances here (teal, solid);
- `break`: the day the rows stop agreeing, where something holds the account back (red, a mark);
- `unproven`: after the agreement, up to the last known balance (amber, hatched);
- `unknown`: no known balance covers this stretch, before the first or after the last (grey,
  hatched).

The segments are in PAINTING order: the `break` mark is last among the stretches it sits on, so
it is drawn over them.

THE PROTECTION MARK is a tick at the day the account is protected through, and a broken one is a
different mark, not a missing one: a lock that has been picked is not an unlocked door.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date

AGREE = "agree"
BREAK = "break"
UNPROVEN = "unproven"
UNKNOWN = "unknown"

#: The mark's width, and a break's, in pixels: a day is far narrower than a pixel on a rail
#: spanning years, and a mark that vanishes is no mark.
MARK_PIXELS = 3
BREAK_PIXELS = 5
#: The bar's height in pixels, and how far the protection tick rises above and below it.
RAIL_HEIGHT = 12
TICK_OVERHANG = 3

#: The sentence for each stretch of the bar, said of the dates it runs between. `{start}` and
#: `{end}` are the stretch's own dates; the unchecked stretch names the known balance it ends at,
#: because that is the thing the transactions are not shown to add up to.
_SENTENCES = {
    AGREE: "The transactions add up to the known balances from {start} to {end}.",
    UNPROVEN: (
        "From {start} to {end} the transactions are not shown to add up to the known balance "
        "for {end}."
    ),
    UNKNOWN: "No known balance from {start} to {end}.",
}


@dataclass(frozen=True)
class RailSegment:
    kind: str
    start: date
    end: date


@dataclass(frozen=True)
class RailMark:
    """Where the account is protected through, and whether that protection has broken."""

    day: date
    broken: bool


@dataclass(frozen=True)
class Rail:
    start: date
    end: date
    segments: tuple[RailSegment, ...]
    protected: RailMark | None


def _clamp(day: date, low: date, high: date) -> date:
    return max(low, min(high, day))


def build_rail(
    *,
    first: date | None,
    known_from: date | None,
    known_to: date | None,
    through: date | None,
    held_day: date | None,
    protected_through: date | None,
    protection_broken: bool,
    today: date,
) -> Rail:
    """The rail for one account.

    `first` is the first row's date; `known_from` and `known_to` the first and last known
    balance; `through` the last day of agreement; `held_day` the day something holds it back, if
    anything does. A set of dates that cannot belong to one account (`known_to` before
    `known_from`, or agreement reaching outside the known balances) is a fault in the caller and
    raises rather than drawing a plausible-looking bar over it.
    """
    if (known_from is None) != (known_to is None):
        raise ValueError("known_from and known_to are both given or both absent")
    if known_from is not None and known_to is not None:
        if known_to < known_from:
            raise ValueError(f"known_to {known_to} is before known_from {known_from}")
        if through is not None and not known_from <= through <= known_to:
            raise ValueError(
                f"agreement through {through} lies outside the known balances "
                f"{known_from} to {known_to}"
            )
    elif through is not None:
        raise ValueError("agreement through a day needs a known balance")

    days = [day for day in (first, known_from) if day is not None]
    start = min(days, default=today)
    end = max([today, start, *([known_to] if known_to is not None else [])])
    segments: list[RailSegment] = []
    if known_from is None or known_to is None:
        segments.append(RailSegment(UNKNOWN, start, end))
    else:
        if start < known_from:
            segments.append(RailSegment(UNKNOWN, start, known_from))
        if through is not None:
            segments.append(RailSegment(AGREE, known_from, through))
        cursor = through if through is not None else known_from
        if cursor < known_to:
            segments.append(RailSegment(UNPROVEN, cursor, known_to))
        if known_to < end:
            segments.append(RailSegment(UNKNOWN, known_to, end))
        if held_day is not None:
            held = _clamp(held_day, start, end)
            segments.append(RailSegment(BREAK, held, held))
    mark = (
        RailMark(_clamp(protected_through, start, end), protection_broken)
        if protected_through is not None
        else None
    )
    return Rail(start, end, tuple(segments), mark)


def rail_text(rail: Rail) -> str:
    """The rail in words: the text alternative, and enough for a person who cannot see the bar."""
    parts = [f"History from {rail.start.isoformat()} to {rail.end.isoformat()}."]
    for segment in rail.segments:
        if segment.kind == BREAK:
            parts.append(f"The transactions stop adding up at {segment.start.isoformat()}.")
        else:
            parts.append(
                _SENTENCES[segment.kind].format(
                    start=segment.start.isoformat(), end=segment.end.isoformat()
                )
            )
    # An account nothing protects says nothing of protection: its absence is not a problem.
    if rail.protected is not None:
        if rail.protected.broken:
            parts.append(f"Protected through {rail.protected.day.isoformat()}, but broken.")
        else:
            parts.append(f"Protected through {rail.protected.day.isoformat()}.")
    return " ".join(parts)


def _percent(rail: Rail, day: date) -> str:
    span = max((rail.end - rail.start).days, 1)
    return f"{(day - rail.start).days / span * 100:.3f}%"


def _width(rail: Rail, segment: RailSegment) -> str:
    span = max((rail.end - rail.start).days, 1)
    return f"{(segment.end - segment.start).days / span * 100:.3f}%"


def rail_svg(rail: Rail, *, uid: str = "rail") -> str:
    """The rail as one inline SVG, 100% wide.

    `uid` makes the hatch patterns' ids unique where a page carries more than one rail; ids are
    document-wide and two rails sharing one would share a pattern. Lengths are in percent, so the
    hatch keeps its angle at any width.
    """
    text = html.escape(rail_text(rail))
    name = html.escape(uid, quote=True)
    parts = [
        f'<svg class="rail" width="100%" height="{RAIL_HEIGHT + 2 * TICK_OVERHANG}" '
        f'role="img" aria-labelledby="{name}-t">',
        f'<title id="{name}-t">{text}</title>',
        "<defs>",
    ]
    for kind in (UNPROVEN, UNKNOWN):
        parts.append(
            f'<pattern id="{name}-{kind}" width="6" height="6" patternUnits="userSpaceOnUse">'
            f'<path class="rail-hatch rail-hatch-{kind}" d="M-1 7 L7 -1"/></pattern>'
        )
    parts.append("</defs>")
    top = TICK_OVERHANG
    for segment in rail.segments:
        if segment.kind == BREAK:
            parts.append(
                f'<rect class="rail-break" x="{_percent(rail, segment.start)}" y="{top}" '
                f'width="{BREAK_PIXELS}" height="{RAIL_HEIGHT}"/>'
            )
            continue
        fill = f' fill="url(#{name}-{segment.kind})"' if segment.kind != AGREE else ""
        parts.append(
            f'<rect class="rail-{segment.kind}" x="{_percent(rail, segment.start)}" y="{top}" '
            f'width="{_width(rail, segment)}" height="{RAIL_HEIGHT}"{fill}/>'
        )
    if rail.protected is not None:
        cls = "rail-mark rail-mark-broken" if rail.protected.broken else "rail-mark"
        parts.append(
            f'<rect class="{cls}" x="{_percent(rail, rail.protected.day)}" y="0" '
            f'width="{MARK_PIXELS}" height="{RAIL_HEIGHT + 2 * TICK_OVERHANG}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)
