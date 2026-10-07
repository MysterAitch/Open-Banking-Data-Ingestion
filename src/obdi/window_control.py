"""The control that chooses a window of days for a chart, and the reading of what it sent.

Every chart that can be drawn over part of what is held offers this one control, so the
choices, the field names, the sentences of refusal, and the look are the same wherever it
appears. The arithmetic of a window is `date_window`'s and the styles are
`stylesheet_window`'s; this module is the form and the reading of the form.

WHERE THE CHOICE MAY TRAVEL, stated once, here. A window is dates the reader chose and
holds no stored value, but a page that draws a figure over it must not be addressable. A
page whose chart exists only with values (the Position page) takes the choice in a POST
body alone. A page that also draws something over a span without any figure (the balance
chart's masked timeline) may take it in the query string of that masked GET, since the
address then leads only to a page that shows no figure, and so may a link from a page
with values back to it. The page with values is always the answer to a POST: it has no
address of its own, sends no redirect, and is `no-store`. The fields are `WINDOW_FIELDS`
and no others are read from either.

The control is a fieldset of common windows in one tap and, folded away, the rest: a
length to type and two days to give. It is placed by its page inside the form that page
submits, so the choice can be made before values are shown.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .core.date_window import (
    Anchor,
    Period,
    Unit,
    WindowRefused,
    WindowSpec,
    between,
    everything,
    length,
    named,
    resolve,
)

_esc = html.escape

#: The windows offered, in the order they are listed: key, label, and what it asks.
#: The keys are what the form sends, so changing one changes the posted field's meaning.
PRESETS: tuple[tuple[str, str, WindowSpec], ...] = (
    ("all", "Everything", everything()),
    ("d30", "Last 30 days", length(30, Unit.DAYS)),
    ("d60", "Last 60 days", length(60, Unit.DAYS)),
    ("d90", "Last 90 days", length(90, Unit.DAYS)),
    ("d180", "Last 180 days", length(180, Unit.DAYS)),
    ("m3", "Last 3 months", length(3, Unit.MONTHS)),
    ("m6", "Last 6 months", length(6, Unit.MONTHS)),
    ("m12", "Last 12 months", length(12, Unit.MONTHS)),
    ("m24", "Last 24 months", length(24, Unit.MONTHS)),
    ("this-week", Period.THIS_WEEK.value, named(Period.THIS_WEEK)),
    ("last-week", Period.LAST_WEEK.value, named(Period.LAST_WEEK)),
    ("last-4-weeks", Period.LAST_4_WEEKS.value, named(Period.LAST_4_WEEKS)),
    ("this-month", Period.THIS_MONTH.value, named(Period.THIS_MONTH)),
    ("last-month", Period.LAST_MONTH.value, named(Period.LAST_MONTH)),
    ("this-quarter", Period.THIS_QUARTER.value, named(Period.THIS_QUARTER)),
    ("last-quarter", Period.LAST_QUARTER.value, named(Period.LAST_QUARTER)),
    ("this-year", Period.THIS_YEAR.value, named(Period.THIS_YEAR)),
    ("last-year", Period.LAST_YEAR.value, named(Period.LAST_YEAR)),
    ("year-to-date", Period.YEAR_TO_DATE.value, named(Period.YEAR_TO_DATE)),
    ("this-tax", Period.THIS_TAX_YEAR.value, named(Period.THIS_TAX_YEAR)),
    ("last-tax", Period.LAST_TAX_YEAR.value, named(Period.LAST_TAX_YEAR)),
    ("tax-to-date", Period.TAX_YEAR_TO_DATE.value, named(Period.TAX_YEAR_TO_DATE)),
)
#: The ones a thumb reaches without opening anything; the rest are under "more".
FIRST_TAP = ("all", "d90", "m12", "this-tax")

OTHER_LENGTH = "other"
BETWEEN = "between"
#: What the keeping button and a bare Enter send: the window already in force.
KEEP = "keep"
DEFAULT_KEY = "all"
_UNITS = {"days": Unit.DAYS, "weeks": Unit.WEEKS, "months": Unit.MONTHS, "years": Unit.YEARS}
_ANCHORS = {
    "ending": ("ending today", Anchor.ENDING_TODAY),
    "ending-on": ("ending on", Anchor.ENDING_ON),
    "starting-on": ("starting on", Anchor.STARTING_ON),
}
#: A typed field is echoed back so it can be corrected; this is as much of it as is.
_ECHO = 40
#: The most digits of a length that are read; beyond it a length is as good as unbounded.
_DIGITS = 9

#: The fields the window rides in. Only these are read from a request.
WINDOW_FIELDS = (
    "window", "window_held", "window_count", "window_unit", "window_anchor", "window_on",
    "window_from", "window_to",
)


@dataclass(frozen=True)
class WindowChoice:
    """What the form said about the window, read once: the choice, what it asks, or why not."""

    #: The preset's key, `OTHER_LENGTH`, `BETWEEN`, or "" for a window given in code.
    key: str
    #: What to draw; None for everything, and for a refused choice (see `refusal`).
    spec: WindowSpec | None
    #: A sentence saying why the choice cannot be a window, or "".
    refusal: str
    #: The last choice that was valid, which pressing "Redraw" without choosing keeps.
    held: str
    #: The fields as typed, to set the controls to.
    fields: dict[str, str]


def _typed(fields: Mapping[str, str], name: str, default: str = "") -> str:
    return fields.get(name, default).strip()[:_ECHO]


def _read_day(text: str, what: str) -> tuple[date | None, str]:
    if not text:
        return None, f"Give the day the window {what}."
    try:
        return date.fromisoformat(text), ""
    except ValueError:
        return None, "Give each day as year-month-day, such as 2026-10-04."


def _read_length(fields: Mapping[str, str]) -> tuple[WindowSpec | None, str]:
    text = _typed(fields, "window_count")
    if not re.fullmatch(r"-?\d+", text):
        return None, "The length must be a whole number, such as 12."
    count = int(text) if len(text.lstrip("-")) <= _DIGITS else (10**_DIGITS) * (
        -1 if text.startswith("-") else 1
    )
    unit = _UNITS.get(_typed(fields, "window_unit", "months"))
    if unit is None:
        return None, "Choose days, weeks, months, or years."
    anchor_name = _typed(fields, "window_anchor", "ending")
    if anchor_name not in _ANCHORS:
        return None, "Choose whether the window ends today, ends on a day, or starts on a day."
    anchor = _ANCHORS[anchor_name][1]
    if anchor is Anchor.ENDING_TODAY:
        return length(count, unit), ""
    day, why = _read_day(
        _typed(fields, "window_on"),
        "ends on" if anchor is Anchor.ENDING_ON else "starts on",
    )
    if day is None:
        return None, why
    return length(count, unit, anchor, day), ""


def _read_between(fields: Mapping[str, str]) -> tuple[WindowSpec | None, str]:
    first_text, last_text = _typed(fields, "window_from"), _typed(fields, "window_to")
    if not first_text or not last_text:
        return None, "Give both days: the one the window starts on and the one it ends on."
    first, why = _read_day(first_text, "starts on")
    last, _ = _read_day(last_text, "ends on")
    if first is None or last is None:
        return None, why or "Give each day as year-month-day, such as 2026-10-04."
    return between(first, last), ""


def window_choice(
    fields: Mapping[str, str], *, today: date, held_from: date | None
) -> WindowChoice:
    """The window the request's `fields` ask for, or the sentence saying why they cannot.

    A button names the choice (`window`); the keeping button, and Enter in a field, send
    `KEEP`, which means the last valid choice (`window_held`), so a second press adjusts
    the page it is on and does not start over. A name that is none of ours is taken as
    keeping, never as a window. The conventions of a window are `date_window`'s.
    """
    known = {key for key, _, _ in PRESETS} | {OTHER_LENGTH, BETWEEN}
    held = _typed(fields, "window_held", DEFAULT_KEY)
    held = held if held in known else DEFAULT_KEY
    asked = _typed(fields, "window", KEEP)
    key = asked if asked in known else held
    echoed = {name: _typed(fields, name) for name in WINDOW_FIELDS if name in fields}
    spec: WindowSpec | None
    why = ""
    if key == OTHER_LENGTH:
        spec, why = _read_length(fields)
    elif key == BETWEEN:
        spec, why = _read_between(fields)
    else:
        spec = next(s for k, _, s in PRESETS if k == key)
    if spec is not None and not why:
        try:
            resolve(spec, today=today, held_from=held_from)
        except WindowRefused as refused:
            spec, why = None, str(refused)
    if why:
        return WindowChoice(key, None, why, held, echoed)
    drawn_spec = None if spec is None or spec.kind == "everything" else spec
    return WindowChoice(key, drawn_spec, "", key, echoed)


def _chip(key: str, label: str, *, pressed: bool) -> str:
    return (
        f'<button type="submit" name="window" value="{_esc(key)}" class="window-chip" '
        f'aria-pressed="{"true" if pressed else "false"}">{_esc(label)}</button>'
    )


def _select(name: str, options: list[tuple[str, str]], chosen: str, label: str) -> str:
    rows = "".join(
        f'<option value="{_esc(value)}"{" selected" if value == chosen else ""}>{_esc(text)}'
        "</option>"
        for value, text in options
    )
    return (
        f'<div class="window-cell"><label for="{name}">{label}</label>'
        f'<select id="{name}" name="{name}">{rows}</select></div>'
    )


def _field(name: str, label: str, value: str, *, kind: str = "text") -> str:
    extra = {
        "date": ' pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}" placeholder="2026-10-04"',
        "text": ' inputmode="numeric" pattern="[0-9]*" autocomplete="off"',
    }[kind]
    return (
        f'<div class="window-cell"><label for="{name}">{label}</label>'
        f'<input id="{name}" name="{name}" type="{kind}"{extra} value="{_esc(value)}"></div>'
    )


def window_controls(
    choice: WindowChoice,
    *,
    today: str,
    note: str = "",
    legend: str = "Chart window",
    first_tap: tuple[str, ...] = FIRST_TAP,
    omit: tuple[str, ...] = (),
    show_now: bool = True,
    extra: tuple[tuple[str, str], ...] = (),
) -> str:
    """The window's controls: common windows in one tap, and a length or two days to type.

    `note` is a sentence the page adds under the window in force, for what choosing does
    there. Every control is shown, since the page has no script to reveal one; the less
    common are folded into a disclosure that is open when the window in force is one of
    them, or when a choice was refused.

    A page that is not a chart names its own `legend`, chooses which windows take the one
    tap (`first_tap`), leaves out those it cannot offer (`omit`), may say the window in
    force elsewhere than here (`show_now`), and may offer windows of its own beside the shared
    ones (`extra`: key and label, each given the first taps ahead of the shared ones). Reading
    what an `extra` key asks is the page's.
    """
    current = choice.key or DEFAULT_KEY
    names = {key: label for key, label, _ in PRESETS}
    shown = "".join(_chip(key, label, pressed=key == current) for key, label in extra) + "".join(
        _chip(key, names[key], pressed=key == current) for key in first_tap
    )
    rest = "".join(
        _chip(key, label, pressed=key == current)
        for key, label, _ in PRESETS
        if key not in first_tap and key not in omit
    )
    fields = choice.fields
    unit = fields.get("window_unit", "months")
    anchor = fields.get("window_anchor", "ending")

    def refused(key: str) -> str:
        """The refusal, beside the control that was refused and no other."""
        if not choice.refusal or choice.key != key:
            return ""
        return (
            '<p class="warn window-refused" role="alert" data-window-refused>'
            f"{_esc(choice.refusal)}</p>"
        )

    other = (
        '<fieldset class="window-other"><legend>A length</legend>'
        '<div class="window-pair">'
        + _field("window_count", "Length", fields.get("window_count", "12"))
        + _select(
            "window_unit",
            [(value, value) for value in _UNITS],
            unit,
            "Unit",
        )
        + "</div>"
        + _select(
            "window_anchor",
            [(value, text) for value, (text, _) in _ANCHORS.items()],
            anchor,
            "Where it sits",
        )
        + _field("window_on", "On this day (for ending on, or starting on)",
                 fields.get("window_on", ""), kind="date")
        + refused(OTHER_LENGTH)
        + f'<button type="submit" name="window" value="{OTHER_LENGTH}">Show this length</button>'
        "</fieldset>"
    )
    between_dates = (
        '<fieldset class="window-between"><legend>Between two dates</legend>'
        '<div class="window-pair">'
        + _field("window_from", "From", fields.get("window_from", ""), kind="date")
        + _field("window_to", "To", fields.get("window_to", ""), kind="date")
        + "</div>"
        + refused(BETWEEN)
        + f'<button type="submit" name="window" value="{BETWEEN}">Show these dates</button>'
        "</fieldset>"
    )
    more_open = bool(choice.refusal) or current in (
        {OTHER_LENGTH, BETWEEN} | {key for key in names if key not in first_tap}
    )
    spec = choice.spec if choice.spec is not None else everything()
    now = (
        f'<p class="window-now" data-window-now>Window: '
        f"{_esc(spec.describe_with_span(today=date.fromisoformat(today)))}</p>"
        if show_now and not choice.refusal
        else ""
    )
    return (
        f'<fieldset class="window-control"><legend>{_esc(legend)}</legend>'
        + now
        + note
        + f'<div class="window-chips" role="group" aria-label="Common windows">{shown}</div>'
        + f'<details class="window-more"{" open" if more_open else ""}>'
        "<summary>More windows, or choose your own</summary>"
        f'<div class="window-chips" role="group" aria-label="More windows">{rest}</div>'
        + other
        + between_dates
        + "</details></fieldset>"
        + f'<input type="hidden" name="window_held" value="{_esc(choice.held)}">'
    )
