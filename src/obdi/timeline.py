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

#: Colour per source - the vocabulary of the chart. Plain and few.
_PALETTE = {
    "starling-feed": "#2b6cb0",
    "truelayer-booked": "#2f855a",
    "truelayer-pending": "#b7791f",
    "truelayer-card-booked": "#6b46c1",
}
_OTHER_COLOUR = "#718096"


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
    #: True for asks whose question has no span - balances, account
    #: enumerations, listings - and for legacy rows whose window went
    #: unrecorded. Their honest shape is a point at the moment of asking.
    point: bool = False


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
) -> tuple[list[AskBar], int]:
    """(drawable rows, count of point-in-time asks among them).

    days=None means EVERYTHING the ledger holds - the full-span view,
    where a decade-old epoch ask is a bar like any other and this
    morning's cursor sliver survives only because of the minimum width.
    `now` is the right edge: panning is just asking for a different now.
    """
    right = now or datetime.now(UTC)
    horizon = right - timedelta(days=days) if days is not None else None
    bars: list[AskBar] = []
    undrawn = 0
    for attempt in attempts:
        attempted = _parse_stamp(str(attempt.get("attempted_at", "")))
        if attempted is None or attempted > right:
            continue
        if horizon is not None and attempted < horizon:
            continue
        window = parse_window(str(attempt.get("asked", "")), attempted)
        if window is None:
            # A state-snapshot ask (or a legacy row whose window went
            # unrecorded): drawn as a point at its moment, because that
            # is its true shape - only invented WIDTH would be a lie.
            undrawn += 1
            window = (attempted, attempted)
        trigger = ""
        try:
            meta = json.loads(str(attempt.get("request_meta") or "{}"))
            trigger = str(meta.get("trigger", ""))
        except ValueError:
            pass
        source = str(attempt.get("source", ""))
        outcome = str(attempt.get("outcome", ""))
        label = (
            f"{attempted.isoformat()[:19]}Z {source} "
            f"[{outcome}]{f' trigger={trigger}' if trigger else ''} "
            f"window {window[0].isoformat()[:19]} .. {window[1].isoformat()[:19]}"
        )
        bars.append(
            AskBar(
                attempted_at=attempted,
                start=window[0],
                end=window[1],
                source=source,
                outcome=outcome,
                label=label,
                point=window[0] == window[1] == attempted,
            )
        )
    bars.sort(key=lambda bar: bar.attempted_at, reverse=True)
    return bars, undrawn


def timeline_svg(
    attempts: list[dict[str, object]],
    *,
    days: int | None = 7,
    clamp_days: int = 120,
    now: datetime | None = None,
    max_rows: int = 250,
) -> str:
    """The chart, or an honest sentence when there is nothing to draw."""
    at = now or datetime.now(UTC)
    bars, undrawn = bars_from_attempts(attempts, days=days, now=at)
    clipped_rows = len(bars) > max_rows
    bars = bars[:max_rows]
    if not bars:
        return '<p class="muted">No asks in this range.</p>'

    domain_right = at
    wanted_left = min(bar.start for bar in bars)
    if days is None:
        # The full-span view: no clamp, the domain IS the data.
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
        style = (
            f'fill="none" stroke="{colour}" stroke-width="2" '
            'stroke-dasharray="3 2"'
            if refused
            else f'fill="{colour}" fill-opacity="0.75"'
        )
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
        stamp = bar.attempted_at.isoformat()[5:16].replace("T", " ")
        parts.append(
            f'<text x="{left_pad - 12}" y="{y + row_h - 5}" text-anchor="end" '
            f'fill="currentColor" opacity="0.8">{stamp}</text></g>'
        )

    parts.append("</svg>")

    legend = " ".join(
        f'<span style="color:{colour}">&#9632;</span> {html.escape(name)}'
        for name, colour in _PALETTE.items()
    )
    notes = []
    if undrawn:
        notes.append(f"{undrawn} point-in-time ask(s) drawn as diamonds")
    if clipped_rows:
        notes.append(f"showing newest {max_rows} asks")
    note_html = (
        f'<p class="muted">{"; ".join(notes)}.</p>' if notes else ""
    )
    return (
        f'<p class="muted">{legend} &nbsp; '
        "dashed = refused; diamond = point-in-time ask (no window); "
        "left notch = window extends beyond the chart; "
        "hover a bar for the full ask.</p>"
        f'<div style="overflow-x:auto">{"".join(parts)}</div>'
        f"{note_html}"
    )
