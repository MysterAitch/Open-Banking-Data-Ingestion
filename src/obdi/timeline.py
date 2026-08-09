"""The fetch timeline: every ask as a bar over the time it asked about.

The attempt ledger already records each ask's moment, source, outcome
and exact window - this module only projects it. Two time axes at once:
VERTICALLY, when each ask was made (newest at the top, one row per
ask); HORIZONTALLY, the span of history it asked about. The shapes that
fall out are the system's fetch strategy made visible: TrueLayer's
tiers as mostly-short bars with daily 7-day and weekly 56-day steps,
Starling's cursor as slivers hugging the right edge, connection-era
ladder probes as bursts of widening bars, and the straddle experiments
as oddly-precise cuts.

The magnitude problem is real - a changesSince=2016 ask spans a decade,
a cursor ask spans thirty minutes - and is handled by CLIPPING rather
than log scales: the horizontal domain is capped (default 120 days),
and a bar that starts earlier gets a notched left edge plus its true
range in the hover title. A log axis would make every bar legible and
every COMPARISON illegible; clipping keeps the common case honest and
marks the exceptional one.

Server-rendered SVG, no scripts: the page stays inspectable text.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_FROM_TO = re.compile(r"from=(\d{4}-\d{2}-\d{2})&to=(\d{4}-\d{2}-\d{2})")
_CHANGES = re.compile(r"changes[Ss]ince=([0-9T:.+Z-]+)")
_SINCE = re.compile(r"since=(\d{4}-\d{2}-\d{2})")

#: The routine window in force for every code version that ever wrote a
#: windowless card ask - verified constant across the repository history
#: (one addition, no changes), which is what licenses the inference.
_WITNESSED_ROUTINE_DAYS = 90

#: Sources whose windowless "routine" asks provably used that default.
_INFERABLE_SOURCES = frozenset({"truelayer-card-booked"})

#: Colour per source - the vocabulary of the chart. Plain and few.
_PALETTE = {
    "starling-feed": "#2b6cb0",
    "truelayer-booked": "#2f855a",
    "truelayer-pending": "#b7791f",
    "truelayer-card-booked": "#6b46c1",
}
_OTHER_COLOUR = "#718096"

#: Refusals wear the alarm colour regardless of source. Observed live: a
#: refused routine-full ask drew as a 9px hollow diamond in its source's
#: colour - the chart's most important events were its least visible marks,
#: buried among a hundred healthy diamonds. The hover title still names the
#: source; the colour's job is to make failure findable at a glance.
_REFUSED_COLOUR = "#b91c1c"


def _parse_stamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class AskBar:
    attempted_at: datetime
    start: datetime
    end: datetime
    source: str
    outcome: str
    label: str
    #: Where the window came from, and therefore how it is drawn:
    #:   recorded   the ask row itself named the range - solid bar
    #:   recovered  the range came from the LINKED ARTEFACT's origin
    #:              (the ledger row said only "routine") - dotted outline
    #:   inferred   no artefact, but the code provably sent the routine
    #:              default at the time - hatched
    #:   point      the ask has no span (a snapshot), or nothing at all
    #:              is known - diamond
    provenance: str = "recorded"

    @property
    def point(self) -> bool:
        return self.provenance == "point"


def parse_window(asked: str, attempted_at: datetime) -> tuple[datetime, datetime] | None:
    """The span of history one ask covered, from its recorded string.

    Asks that name no span (accounts lists, balances, bare "routine"
    rows from before windows were recorded) return None - they are
    counted, not drawn, because a bar of invented width would be a lie
    with a colour.
    """
    match = _FROM_TO.search(asked)
    if match:
        start = _parse_stamp(match.group(1) + "T00:00:00Z")
        end = _parse_stamp(match.group(2) + "T23:59:59Z")
        if start and end and start <= end:
            return (start, end)
        return None
    match = _CHANGES.search(asked)
    if match:
        start = _parse_stamp(match.group(1))
        if start and start <= attempted_at:
            return (start, attempted_at)
        return None
    match = _SINCE.search(asked)
    if match:
        start = _parse_stamp(match.group(1) + "T00:00:00Z")
        if start and start <= attempted_at:
            return (start, attempted_at)
    return None


def bars_from_attempts(
    attempts: list[dict[str, object]],
    *,
    days: int | None,
    now: datetime | None = None,
) -> tuple[list[AskBar], int, list[str]]:
    """(drawable rows, count of point asks, recorded-vs-evidence mismatches).

    days=None means EVERYTHING the ledger holds - the full-span view,
    where a decade-old epoch ask is a bar like any other and this
    morning's cursor sliver survives only because of the minimum width.
    `now` is the right edge: panning is just asking for a different now.
    """
    right = now or datetime.now(UTC)
    horizon = right - timedelta(days=days) if days is not None else None
    bars: list[AskBar] = []
    undrawn = 0
    mismatches: list[str] = []
    for attempt in attempts:
        attempted = _parse_stamp(str(attempt.get("attempted_at", "")))
        if attempted is None or attempted > right:
            continue
        if horizon is not None and attempted < horizon:
            continue
        source_name = str(attempt.get("source", ""))
        asked = str(attempt.get("asked", ""))
        window = parse_window(asked, attempted)
        # Every origin this digest landed under. Identical payloads land
        # under SIBLING origins (rolling-epoch fetches differ only by
        # their computed date), so the ask must be compared against all
        # of them - an arbitrary sibling once made this check cry wolf
        # 58 times against asks that agreed with their own fetch.
        origin_windows = []
        for origin in str(attempt.get("artefact_origins") or "").split(","):
            parsed = parse_window(origin, attempted)
            if parsed is not None:
                origin_windows.append(parsed)
        provenance = "recorded"
        if window is not None and origin_windows:
            agreed = any(
                window[0].date() == candidate[0].date()
                and window[1].date() == candidate[1].date()
                for candidate in origin_windows
            )
            if not agreed:
                shown = origin_windows[0]
                mismatches.append(
                    f"{attempted.isoformat()[:19]}Z {source_name}: ask row "
                    f"says {window[0].date()}..{window[1].date()} but no "
                    f"origin of its artefact agrees (nearest: "
                    f"{shown[0].date()}..{shown[1].date()})"
                )
        elif window is None and origin_windows:
            distinct = {
                (candidate[0].date(), candidate[1].date())
                for candidate in origin_windows
            }
            if len(distinct) == 1:
                # Unambiguous: every fetch of these bytes asked the same
                # window, so the recovery cannot name a sibling's.
                window = origin_windows[0]
                provenance = "recovered"
        elif window is None and source_name in _INFERABLE_SOURCES and "routine" in asked:
            window = (
                attempted - timedelta(days=_WITNESSED_ROUTINE_DAYS),
                attempted,
            )
            provenance = "inferred"
        if window is None:
            # A state-snapshot ask, or nothing known at all: a point at
            # its moment, because only invented WIDTH would be a lie.
            undrawn += 1
            window = (attempted, attempted)
            provenance = "point"
        trigger = ""
        try:
            meta = json.loads(str(attempt.get("request_meta") or "{}"))
            trigger = str(meta.get("trigger", ""))
        except ValueError:
            pass
        source = str(attempt.get("source", ""))
        outcome = str(attempt.get("outcome", ""))
        note = {
            "recorded": "",
            "recovered": " (window recovered from the landed artefact)",
            "inferred": (
                " (window inferred from the routine default in force - "
                "not recorded)"
            ),
            "point": "",
        }[provenance]
        label = (
            f"{attempted.isoformat()[:19]}Z {source} "
            f"[{outcome}]{f' trigger={trigger}' if trigger else ''} "
            f"window {window[0].isoformat()[:19]} .. "
            f"{window[1].isoformat()[:19]}{note}"
        )
        bars.append(
            AskBar(
                attempted_at=attempted,
                start=window[0],
                end=window[1],
                source=source,
                outcome=outcome,
                label=label,
                provenance=provenance,
            )
        )
    # Oldest at the top, reading DOWN moves forward in time - the
    # waterfall convention from network inspectors. Newest-first read as
    # "older fetches happened after newer ones" until the labels were
    # studied, and a chart that needs studying to avoid a double take
    # has the rows the wrong way up.
    bars.sort(key=lambda bar: bar.attempted_at)
    return bars, undrawn, mismatches


def timeline_svg(
    attempts: list[dict[str, object]],
    *,
    days: int | None = 7,
    clamp_days: int | None = 120,
    now: datetime | None = None,
    max_rows: int = 250,
) -> str:
    """The chart, or an honest sentence when there is nothing to draw.

    Two independent axes, two independent controls: `days` filters WHICH
    asks appear (the vertical axis), `clamp_days` bounds how far back
    the chart reaches (the horizontal axis). Conflating them was tried
    and read wrong - every row filter narrower than the clamp drew the
    identical horizontal axis, so "zooming" moved nothing. clamp_days of
    None means fit: the domain stretches to hold every drawn bar and
    nothing is clipped.
    """
    at = now or datetime.now(UTC)
    bars, undrawn, mismatches = bars_from_attempts(attempts, days=days, now=at)
    clipped_rows = len(bars) > max_rows
    bars = bars[:max_rows]
    if not bars:
        return '<p class="muted">No asks in this range.</p>'

    domain_right = at
    wanted_left = min(bar.start for bar in bars)
    if days is None or clamp_days is None:
        # Fit: no clamp, the domain IS the data.
        domain_left = wanted_left
    else:
        clamp_left = at - timedelta(days=max(clamp_days, days))
        domain_left = max(wanted_left, clamp_left)
    span = (domain_right - domain_left).total_seconds() or 1.0

    width = 960
    left_pad, right_pad, top_pad = 150, 12, 22
    row_h = 13
    height = top_pad + row_h * len(bars) + 26
    plot_w = width - left_pad - right_pad

    def x_of(when: datetime) -> float:
        clamped = max(min(when, domain_right), domain_left)
        return left_pad + plot_w * (
            (clamped - domain_left).total_seconds() / span
        )

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;height:auto;font:10px sans-serif">'
    ]
    # One hatch pattern per palette colour, for inferred windows.
    parts.append("<defs>")
    for key, colour in {**_PALETTE, "other": _OTHER_COLOUR}.items():
        safe = key.replace(".", "-")
        parts.append(
            f'<pattern id="hatch-{safe}" width="5" height="5" '
            'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="5" height="5" fill="{colour}" fill-opacity="0.2"/>'
            f'<line x1="0" y1="0" x2="0" y2="5" stroke="{colour}" '
            'stroke-width="2"/></pattern>'
        )
    parts.append("</defs>")

    # Date ticks: six across the domain, labelled month-day.
    for i in range(7):
        tick = domain_left + timedelta(seconds=span * i / 6)
        x = x_of(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top_pad - 4}" x2="{x:.1f}" '
            f'y2="{height - 20}" stroke="#00000022" stroke-width="1"/>'
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" '
            f'fill="currentColor" opacity="0.7">{tick.date().isoformat()[5:]}</text>'
        )

    previous_day = None
    refused_drawn = 0
    for index, bar in enumerate(bars):
        y = top_pad + index * row_h
        colour = _PALETTE.get(bar.source, _OTHER_COLOUR)
        x1, x2 = x_of(bar.start), x_of(bar.end)
        # Minimum width: at full-span zoom a 30-minute cursor ask is
        # thousandths of a pixel, and an invisible slice can be neither
        # seen nor hovered. Three pixels keeps every ask drillable; the
        # hover title carries the true span the width cannot.
        bar_w = max(x2 - x1, 3.0)
        clipped = bar.start < domain_left
        refused = bar.outcome != "landed"
        if refused:
            refused_drawn += 1
            style = (
                f'fill="none" stroke="{_REFUSED_COLOUR}" stroke-width="2" '
                'stroke-dasharray="3 2"'
            )
        elif bar.provenance == "inferred":
            key = (bar.source if bar.source in _PALETTE else "other").replace(".", "-")
            style = f'fill="url(#hatch-{key})" stroke="{colour}" stroke-width="1"'
        elif bar.provenance == "recovered":
            style = (
                f'fill="{colour}" fill-opacity="0.55" stroke="{colour}" '
                'stroke-width="1.5" stroke-dasharray="2 2"'
            )
        else:
            style = f'fill="{colour}" fill-opacity="0.75"'
        parts.append(
            f'<g><title>{html.escape(bar.label)}'
            f'{" (window extends left of chart)" if clipped else ""}</title>'
        )
        if bar.point:
            # A rhombus at the moment of asking: visibly not a bar, so a
            # snapshot can never be misread as a covered span.
            cy = y + (row_h - 4) / 2
            half = 4.5
            parts.append(
                f'<path d="M {x2:.1f} {cy - half:.1f} L {x2 + half:.1f} {cy:.1f} '
                f'L {x2:.1f} {cy + half:.1f} L {x2 - half:.1f} {cy:.1f} Z" {style}/>'
            )
        else:
            parts.append(
                f'<rect x="{x1:.1f}" y="{y}" width="{bar_w:.1f}" '
                f'height="{row_h - 4}" rx="1.5" {style}/>'
            )
        if clipped:
            # The notch: this window reaches further back than the chart.
            parts.append(
                f'<path d="M {left_pad - 8} {y + (row_h - 4) / 2} '
                f'l 7 -{(row_h - 4) / 2} l 0 {row_h - 4} z" fill="{colour}"/>'
            )
        # The date labels each DAY once; same-day rows carry only their
        # time. Fifty identical date labels are noise wearing ink.
        day = bar.attempted_at.date()
        if day != previous_day:
            stamp = bar.attempted_at.strftime("%a %d-%b")
            opacity = "0.85"
            previous_day = day
        else:
            stamp = bar.attempted_at.strftime("%H:%M")
            opacity = "0.55"
        parts.append(
            f'<text x="{left_pad - 12}" y="{y + row_h - 5}" text-anchor="end" '
            f'fill="currentColor" opacity="{opacity}">{stamp}</text></g>'
        )

    parts.append("</svg>")

    legend = " ".join(
        f'<span style="color:{colour}">&#9632;</span> {html.escape(name)}'
        for name, colour in _PALETTE.items()
    )
    notes = []
    if mismatches:
        notes.append(
            f"{len(mismatches)} ask(s) DISAGREE with their own landed "
            "artefact about the window asked - investigate: "
            + " | ".join(html.escape(m) for m in mismatches[:3])
        )
    # Always stated, never implied: "0 of N refused" is a claim the reader
    # can trust, where an absent count reads as "nobody looked".
    notes.append(f"{refused_drawn} of {len(bars)} asks in range refused")
    if undrawn:
        notes.append(f"{undrawn} point-in-time ask(s) drawn as diamonds")
    if clipped_rows:
        notes.append(f"showing newest {max_rows} asks")
    note_html = (
        f'<p class="muted">{"; ".join(notes)}.</p>' if notes else ""
    )
    return (
        f'<p class="muted">{legend} &nbsp; '
        '<span style="color:#b91c1c">dashed red = refused</span> '
        "(bar or diamond - hover names the source); "
        "diamond = point-in-time ask (no window); "
        "dotted outline = window recovered from the landed artefact; "
        "hatched = window inferred from the routine default; "
        "left notch = window extends beyond the chart; "
        "hover a bar for the full ask.</p>"
        f'<div style="overflow-x:auto">{"".join(parts)}</div>'
        f"{note_html}"
    )
