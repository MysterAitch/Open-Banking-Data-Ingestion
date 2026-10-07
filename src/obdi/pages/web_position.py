"""The financial position page, in both of its renderings.

It follows the ledger page's mechanism exactly. A GET renders MASKED whatever
its query string. Showing values takes a POST, answered directly with the
unmasked page and never redirected, so no address a person can bookmark, paste
into a note, or fetch from a script holds a value, and the unmasked response is
sent `no-store` so a browser history does not either.

Every record is wrapped in `masking.Disclosed` at the top of `render_position`,
and the markup below reads only the wrapped view.

THE CHART IS A VALUE. The shape of a net-worth line discloses how large the
figures are relative to one another, which masking digits cannot hide, so the
masked page draws no chart at all and says when one is drawn. The chart needs
numbers, which a `Disclosed` view hands back as text, so it is built from the
record itself, and only on the branch where `unmasked` is true: `_history`
takes the flag and returns a sentence, having read nothing, when it is false.

THE CHART CAN LEAVE ACCOUNTS OUT, and nothing else does. The unit of choice is
the account or asset, not a group, because the page groups by which way a
balance sits and a mortgage shares "overdrawn or owed" with every card. The
ticks ride in the form that asks for values, so a choice is a view and is stored
nowhere; `chart_chosen` says one was made, since an unticked box is not sent.
The headline, the totals, and the month table stay whole, and the page says so
beside the chart. The sums are `position.chart_series`.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..core.date_window import (
    Resolution,
    Window,
    WindowRefused,
    WindowSpec,
    resolution_for,
    resolve,
    sample_days,
)
from ..core.logs import say
from ..core.masking import MASKED_TOTAL, Disclosed
from ..core.plural import agree
from ..core.plural import plural as _plural
from ..ingest.accounts import BALANCE_ONLY_KIND
from ..read.account_names import AccountShown
from ..read.overview import AccountOverview
from ..read.position import (
    ChartSeries,
    MonthPoint,
    Position,
    ProvisionalPoint,
    chart_series,
    held_from,
    series_at,
    window_points,
)
from . import values_sitting
from .callback import render_page
from .web_accounts import submit_button
from .web_ledger import _balance_word
from .web_position_trust import UNREAD, figure_lines, list_html
from .web_position_trust import read as read_trust
from .window_control import (
    DEFAULT_KEY,
    KEEP,
    WINDOW_FIELDS,
    WindowChoice,
    window_controls,
)
from .window_control import window_choice as _read_window

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    # Importing the handler's module at runtime would close a cycle: web.py
    # composes this module in.
    from .web import WebConfig

_esc = html.escape

#: Months shown before the month table folds itself away.
TABLE_FOLD_AFTER = 12

_GROUP_TITLES = {
    "in": "In credit",
    "out": "Overdrawn or owed",
    "nil": "Nil balance",
    "archived": "Archived accounts",
}

_KIND_WORDS = {
    "defined_contribution": "defined-contribution pension",
    "defined_benefit": "defined-benefit pension",
    "state_pension": "state pension forecast",
    "investment": "investment",
    "property": "property",
    "other": "other asset",
}

_HOME = '<p><a class="button" href="/">Back to overview</a></p>'


def _ledger_href(ref: str) -> str:
    return f"/ledger?ref={_esc(quote(ref, safe=''))}"


def _periods_href(ref: str) -> str:
    return f"/period-reconciliation?ref={_esc(quote(ref, safe=''))}"


def _days(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def _figure(word: str, amount: str) -> str:
    return f'{_esc(word)} <span class="mono nowrap">{_esc(amount)}</span>'


def _fact(name: str, value: str) -> str:
    return f"<div><dt>{name}</dt><dd>{value}</dd></div>"


def _subtotals(view: Any) -> str:
    """Each direction's subtotal on one line: the counted accounts are listed by trust, not by
    which way their balance sits, so what the groups said is kept here."""
    items = "".join(
        f"<li>{_esc(_GROUP_TITLES[g.key])} ({len(g.accounts)}): subtotal "
        f"{_figure(_balance_word(g.direction), g.subtotal)}</li>"
        for g in view.groups
    )
    return f'<ul class="keylist">{items}</ul>'


def _check_lines(view: Any) -> str:
    """What the later known balances say of one account, as the page said it per account before
    the list became trust bars: where the family's transactions first stop adding up, and where
    to see it. Empty for an account with nothing to say."""
    parts = []
    if view.checks_differ:
        parts.append(
            f"{_plural(view.checks_differ, 'later check')} "
            f"{agree(view.checks_differ, 'differs')}: the transactions between its known "
            "balances do not add up, so this balance may be wrong. It is still counted. "
            f'<a class="tap" href="{_ledger_href(view.ref)}">See its ledger</a> or '
            f'<a class="tap" href="{_periods_href(view.ref)}">where, period by period</a>.'
        )
    if view.family_anchors:
        # The known balances are the whole account's (main plus its Spaces), so a difference
        # is located against the family's transactions, not main's alone.
        if view.family_first_differing:
            parts.append(
                "The transactions first stop adding up to the whole account's known balances "
                f"({_esc(str(view.family_anchors))}) on {_esc(view.family_first_differing)}; "
                f"the difference is {_esc(view.family_pattern)} after that."
            )
        else:
            parts.append(
                "Checked against the whole account (main plus its Spaces): "
                f"{_esc(str(view.family_anchors))} known balances, and the transactions add up "
                "to all of them."
            )
        parts.append(_esc(view.family_opening_note))
    if not parts:
        return ""
    name = AccountShown.named(view.ref, view.label).as_name()
    return f"<li><strong>{name}</strong>: {' '.join(parts)}</li>"


def _checks_fold(view: Any) -> str:
    """The per-account detail of the later balance checks, folded: the trust bars say the
    verdict and this says where."""
    items = "".join(_check_lines(a) for g in view.groups for a in g.accounts)
    if not items:
        return ""
    return (
        "<details><summary>How the later known balances were checked</summary>"
        f'<ul class="pos-left-out">{items}</ul></details>'
    )


def _plain_list(view: Any) -> str:
    """The counted accounts by name, for when their trust could not be read."""
    items = "".join(
        f'<li><a class="tap" href="{_ledger_href(a.ref)}">'
        f"{AccountShown.named(a.ref, a.label).as_name()}</a></li>"
        for g in view.groups
        for a in g.accounts
    )
    return f'<ul class="keylist">{items}</ul>'


def _left_out(view: Any) -> str:
    """An account the figure leaves out: its name linking to where a balance is stated, why, and
    how far it has moved since its history began (a movement, never a balance)."""
    name = AccountShown.named(view.ref, view.label).as_name()
    if view.state == "withheld":
        why = f"no opening balance could be derived: {_esc(view.withheld)}"
    else:
        why = "no known balance has been stated for it"
        if not int(view.rows):
            # A feedless account is the usual cause, and the way out is to declare it.
            why += (
                ", and it holds no transactions, so it may be an account obdi has no feed for: "
                f"declare its kind as {BALANCE_ONLY_KIND} on its account page to have it "
                "counted from its first known balance"
            )
    moved = (
        f"; moved {_figure(view.moved_direction, view.moved)} since {_esc(view.first_row)}"
        if view.first_row
        else ""
    )
    return f'<li><a class="tap" href="{_ledger_href(view.ref)}">{name}</a> - {why}{moved}.</li>'


def _observed(view: Any) -> str:
    """When an asset was last observed and by what, in one clause."""
    return (
        f'observed <span class="nowrap">{_esc(view.observed_on)}</span> '
        f"({_esc(_days(view.age_days))}), from {_esc(view.source)}"
    )


def _asset_card(view: Any) -> str:
    return (
        f"<li><strong>{_esc(view.asset_id)}</strong> "
        f'<span class="muted">{_esc(_KIND_WORDS.get(view.kind, view.kind))}</span>: '
        f"{_figure(_balance_word(view.direction), view.value)}, {_observed(view)}, "
        f"{_plural(int(view.observations), 'observation')}.</li>"
    )


def _entitlement_card(view: Any) -> str:
    return (
        f"<li><strong>{_esc(view.asset_id)}</strong> "
        f'<span class="muted">{_esc(_KIND_WORDS.get(view.kind, view.kind))}</span>: '
        f'<span class="mono nowrap">{_esc(view.annual_income)}</span> a year, '
        f"{_observed(view)}.</li>"
    )


def _headline(view: Any, trust_lines: str = "") -> str:
    counted = (
        f"Counts {_plural(int(view.accounts_counted), 'account')} of "
        f"{view.accounts_total} and {_plural(int(view.assets_counted), 'asset')}."
    )
    held_nothing = (
        int(view.accounts_total) == 0
        and int(view.assets_counted) == 0
        and not view.entitlements
        and int(view.foreign_observations) == 0
    )
    if held_nothing:
        return (
            "<h2>Net worth</h2><p><strong>Nothing is held.</strong> No account is held or "
            "declared and no asset has been observed, so there is no net worth to state.</p>"
        )
    if not view.net_direction:
        body = (
            "<h2>Net worth</h2><p><strong>No net worth is shown.</strong> "
            f"{_esc(counted)} Nothing is counted: no account has an opening balance and "
            "no asset has been observed, and adding up what is unknown as zero would "
            "present a figure nobody has.</p>"
        )
    else:
        body = (
            "<h2>Net worth</h2>"
            f'<p class="figure">{_figure(_balance_word(view.net_direction), view.net_worth)}</p>'
            f"<p>{_esc(counted)} As at {_esc(view.as_of)}.</p>{trust_lines}"
        )
    uncounted = int(view.accounts_uncounted)
    if uncounted:
        one = uncounted == 1
        body += (
            '<p class="muted">Provisional, counting every account: '
            f"{_figure(_balance_word(view.provisional_direction), view.provisional_total)}. "
            f"This takes {_plural(uncounted, 'unknown opening balance')} as nil, so it is "
            f"off by whatever {'that account' if one else 'those accounts'} held before "
            f"{'its' if one else 'their'} history began. The movement is real; the level "
            "is not.</p>"
            '<p class="warn"><strong>'
            f"{_plural(uncounted, 'account')} {'is' if one else 'are'} not "
            "counted</strong> because no opening balance is known for "
            f"{'it' if one else 'them'}. {'It is' if one else 'They are'} "
            "left out of the net worth, every balance, and every subtotal, and listed "
            "below with where to state a balance.</p>"
        )
    if int(view.foreign_observations):
        body += (
            f'<p class="warn">{_plural(int(view.foreign_observations), "observation")} in '
            "a currency other than GBP "
            f"{'was' if int(view.foreign_observations) == 1 else 'were'} left out: only "
            "pounds are added up.</p>"
        )
    return body


def _mode(unmasked: bool) -> str:
    if unmasked:
        return values_sitting.unless_sitting(
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            '<p><a class="button" href="/position">Hide values</a></p>'
        )
    return (
        '<p class="muted">Values are masked. Every figure here is a balance or a '
        f"total, so each shows as {MASKED_TOTAL} whatever its size: the number of "
        "digits in a total would say how much there is. Counts, dates, sources, "
        "directions, and flags are real.</p>"
        '<form method="post" action="/position">'
        + submit_button("Show values")
        + "</form>"
        + values_sitting.show_everywhere_press()
    )


def _signed(minor: int) -> str:
    whole, pence = divmod(abs(minor), 100)
    return f"{'-' if minor < 0 else ''}£{whole:,}.{pence:02d}"


#: How each line is drawn: (stroke, dash pattern, further attributes). The chart and its key
#: both read this, so a swatch in the key cannot differ from the line it names.
_LINE_STYLES: dict[str, tuple[str, str, str]] = {
    "known": ("var(--act)", "", ""),
    "partial": ("var(--act)", "5 5", ' stroke-opacity=".6"'),
    # Dotted with round caps, where the partial months are dashed: the two must
    # not be mistaken for one another.
    "provisional": ("var(--warn)", "1 5", ""),
}

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: A chart of at most this many months names each month; a longer one names its years only.
_NAME_MONTHS_UP_TO = 18
#: Tick labels nearer than this, in the chart's own units, would run into each other.
_LABEL_GAP = 30
#: A tick nearer than this to its neighbour is a smear, not a mark.
_TICK_GAP = 5


def _line_attrs(name: str) -> str:
    stroke, dash, more = _LINE_STYLES[name]
    dashes = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'fill="none" stroke="{stroke}" stroke-width="2.5" stroke-linejoin="round" '
        f'stroke-linecap="round"{dashes}{more}'
    )


def _lines_drawn(
    points: tuple[MonthPoint, ...], complete_from: str, provisional: tuple[ProvisionalPoint, ...]
) -> tuple[str, ...]:
    """Which of `_LINE_STYLES` the chart draws for these series, in the key's order.

    A series of one month is a single mark and no line.
    """
    complete_index = next(
        (i for i, p in enumerate(points) if p.month == complete_from), len(points)
    )
    drawn = []
    if len(points) > 1 and complete_index < len(points) - 1:
        drawn.append("known")
    if len(points) > 1 and complete_index > 0:
        drawn.append("partial")
    if len(provisional) > 1:
        drawn.append("provisional")
    return tuple(drawn)


#: How the part of the plot below nil is marked, in the chart and in its key. A wash of the
#: text's own colour and not the fault colour: owing money is a position, not an error.
_BELOW_NIL_FILL = 'fill="currentColor" fill-opacity=".09"'

SOME_BELOW_NIL = "some"
ALL_BELOW_NIL = "all"


def _below_nil(
    points: tuple[MonthPoint, ...], provisional: tuple[ProvisionalPoint, ...]
) -> str:
    """Whether the figures drawn go below nil: in some months, in all of them, or "" for none."""
    values = [p.net_worth.minor for p in points] + [p.total.minor for p in provisional]
    if not values or min(values) >= 0:
        return ""
    return ALL_BELOW_NIL if max(values) < 0 else SOME_BELOW_NIL


def unit_word(resolution: Resolution) -> str:
    """What one point of a chart is, as a noun: the one place a chart's unit is named.

    The key, the chart's description, the sentence beneath it, and its labels all read
    this, so a chart of days never calls a point a month.
    """
    return {
        Resolution.DAY: "day",
        Resolution.WEEK: "week",
        Resolution.MONTH: "month",
    }[resolution]


def _leaves_out(resolution: Resolution) -> str:
    """Said of a point that leaves something out: on that point's date an item counted today
    had no known figure yet. In the chart's own unit, so a daily chart says "on a day"."""
    return (
        f"{'on' if resolution is Resolution.DAY else 'in'} a {unit_word(resolution)} "
        "that leaves something out"
    )


def _key(
    lines: tuple[str, ...],
    *,
    narrowed: bool,
    below_nil: str = "",
    resolution: Resolution | None = None,
) -> str:
    """The chart's key: each line it draws, as a swatch drawn the way the line is, and its name.

    Where the chart goes below nil the key also says what the marked region means. A chart
    over a window (a `resolution`) also says how often it has a figure; one without is
    monthly.
    """
    if not lines and not below_nil and resolution is None:
        return ""
    total = "Total of the chosen accounts" if narrowed else "Net worth"
    words = {
        "known": f"{total} that is known",
        "partial": f"{total} {_leaves_out(resolution or Resolution.MONTH)}",
        "provisional": "Provisional total, which counts each unknown opening balance as nil",
    }
    swatch = '<svg width="46" height="10" aria-hidden="true" style="vertical-align:middle">'
    items = "".join(
        f'<li data-key="{name}">{swatch}<line x1="2" y1="5" x2="44" y2="5" '
        f"{_line_attrs(name)}/></svg> {_esc(words[name])}</li>"
        for name in lines
    )
    if below_nil:
        meaning = (
            "Below nil throughout: more is owed than held in every month drawn"
            if below_nil == ALL_BELOW_NIL
            else "Below nil: more is owed than held"
        )
        items += (
            f'<li data-key="below-nil">{swatch}<rect x="2" y="0" width="42" height="10" '
            f"{_BELOW_NIL_FILL}/></svg> {meaning}</li>"
        )
    if resolution is not None:
        items += f'<li data-key="resolution">{_esc(_resolution_words(resolution))}</li>'
    return f'<ul class="legend chart-key" style="list-style:none;padding-left:0">{items}</ul>'


def _span_words(months: int) -> str:
    """How long `months` month-ends cover, in years and months."""
    years, rest = divmod(months, 12)
    return " ".join(
        _plural(count, unit) for count, unit in ((years, "year"), (rest, "month")) if count
    )


#: Where a chart's figures are read, in the words its title and description use.
_WHEN = {
    Resolution.MONTH: "at each month-end",
    Resolution.DAY: "on each day",
    Resolution.WEEK: "on the first day, each Sunday, and the last day",
}


def _resolution_words(resolution: Resolution) -> str:
    """The chart's resolution in one short sentence, for its key."""
    return {
        Resolution.MONTH: "One figure per month-end.",
        Resolution.WEEK: "One figure per week.",
        Resolution.DAY: "One figure per day.",
    }[resolution]


def _span_phrase(labels: list[str], resolution: Resolution) -> str:
    """How long a chart covers: its days or weeks, or its months and years."""
    if resolution is Resolution.MONTH:
        return _span_words(len(labels))
    days = (date.fromisoformat(labels[-1]) - date.fromisoformat(labels[0])).days + 1
    if resolution is Resolution.DAY:
        return _plural(days, "day")
    weeks, rest = divmod(days, 7)
    return " ".join(
        _plural(count, unit) for count, unit in ((weeks, "week"), (rest, "day")) if count
    )


def _axis(
    months: list[str],
    x: Callable[[int], float],
    *,
    top: float,
    floor: float,
    edges: tuple[float, float],
) -> str:
    """The ticks under the plot: each January named by its year, and the months between.

    A chart of more than `_NAME_MONTHS_UP_TO` months names its years only and ticks each
    month, or each quarter, where there is room for the ticks; a shorter one names every
    month. Years are placed first, and a label is left out where it would run into one
    already placed, so a year is never lost to the month before it.
    """
    n = len(months)
    step = x(1) - x(0) if n > 1 else float(_LABEL_GAP)
    name_months = n <= _NAME_MONTHS_UP_TO
    every = 1 if name_months or step >= _TICK_GAP else 3 if step * 3 >= _TICK_GAP else 0
    ticks: list[tuple[str, float, str]] = []
    for index, month in enumerate(months):
        number = int(month[5:7])
        if number == 1:
            ticks.append(("year", x(index), month[:4]))
        elif every and (number - 1) % every == 0:
            ticks.append(("month", x(index), _MONTH_NAMES[number - 1] if name_months else ""))
    return _tick_marks(ticks, top=top, floor=floor, edges=edges)


#: How long each kind of tick is, longest first, which is also the order labels are placed in.
_TICK_LENGTHS = {"year": 8, "month": 4, "week": 6, "day": 3}
_TICK_OPACITY = {"day": ".35"}


def _tick_marks(
    ticks: list[tuple[str, float, str]],
    *,
    top: float,
    floor: float,
    edges: tuple[float, float],
    order: tuple[str, ...] = ("year", "month"),
) -> str:
    """Each tick as a mark under the plot, and its name where it fits.

    Kinds are placed in `order`, so a label is left out where it would run into one
    already placed: years are never lost to the month before them.
    """
    parts = []
    placed: list[float] = []
    for kind in order:
        for tick_kind, at, text in ticks:
            if tick_kind != kind:
                continue
            if kind == "year":
                parts.append(
                    f'<line x1="{at:.1f}" y1="{top:.1f}" x2="{at:.1f}" y2="{floor:.1f}" '
                    'stroke="currentColor" stroke-opacity=".12"/>'
                )
            opacity = _TICK_OPACITY.get(kind, ".55")
            parts.append(
                f'<line data-tick="{kind}" x1="{at:.1f}" y1="{floor:.1f}" x2="{at:.1f}" '
                f'y2="{floor + _TICK_LENGTHS[kind]:.1f}" stroke="currentColor" '
                f'stroke-opacity="{opacity}"/>'
            )
            if not text or any(abs(at - other) < _LABEL_GAP for other in placed):
                continue
            placed.append(at)
            anchor = (
                "start" if at - edges[0] < 14 else "end" if edges[1] - at < 14 else "middle"
            )
            weight = ' font-weight="700"' if kind == "year" else ""
            parts.append(
                f'<text data-tick-label="{kind}" x="{at:.1f}" y="{floor + 20:.1f}" '
                f'text-anchor="{anchor}" font-size="11" fill="var(--ink-2)"{weight}>'
                f"{_esc(text)}</text>"
            )
    return "".join(parts)


def _axis_days(
    first: date,
    last: date,
    x: Callable[[date], float],
    *,
    resolution: Resolution,
    top: float,
    floor: float,
    edges: tuple[float, float],
) -> str:
    """The ticks of a chart of days or weeks, placed by date and not by point.

    A daily chart ticks every day, marks each Monday (a week's start) longer and names it
    by its day of the month where there is room, and names each month's first day and each
    January's year. A weekly chart ticks and names months and years only, since its points
    are Sundays and the month's first is not one. Years are placed first, then months, then
    Mondays; a label that would run into one placed is left out.
    """
    daily = resolution is Resolution.DAY
    ticks: list[tuple[str, float, str]] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        at = x(day)
        if day.day == 1 and day.month == 1:
            ticks.append(("year", at, str(day.year)))
        elif day.day == 1:
            ticks.append(("month", at, _MONTH_NAMES[day.month - 1]))
        elif daily and day.weekday() == 0:
            ticks.append(("week", at, str(day.day)))
        elif daily:
            ticks.append(("day", at, ""))
    return _tick_marks(
        ticks, top=top, floor=floor, edges=edges, order=("year", "month", "week", "day")
    )


def _chart(
    points: tuple[MonthPoint, ...],
    complete_from: str,
    provisional: tuple[ProvisionalPoint, ...] = (),
    *,
    narrowed: bool = False,
    resolution: Resolution = Resolution.MONTH,
    windowed: bool = False,
) -> str:
    """The net-worth line, and the provisional line when there is one, as inline SVG.

    Called only where values are shown. The scale covers both lines, so neither
    is drawn against the other's range. The known months are a subset of the
    provisional ones, which end in the same month, so one column per provisional
    month places both. A `narrowed` chart is drawn from some accounts only, and
    says so in its own text, since a figure copied out of it is not the net worth.

    A chart of days or weeks (`resolution`) labels its points by day and places them in
    proportion to the days between them, so a short first or last week is not drawn as
    a whole one. A `windowed` chart is of part of what is held, and its lowest, highest,
    and latest say they are the window's.
    """
    width, base_height = 400, 260
    # A windowed chart of chosen accounts needs a line of its own to say so, where the
    # latest figure's label has no room for the words.
    extra = 16 if windowed and narrowed else 0
    height = base_height + extra
    # Under the plot: the axis's ticks and their names, the lowest and latest figures, and
    # the first and last month with how long the chart covers between them.
    left, right, top, bottom = 12, 12, 30, 62
    months = [p.month for p in (provisional or points)]
    column = {month: index for index, month in enumerate(months)}
    n = len(months)
    known = [(column[p.month], p.net_worth.minor) for p in points]
    dotted = [(index, p.total.minor) for index, p in enumerate(provisional)]
    values = [value for _, value in known + dotted]
    low, high = min(values), max(values)
    plot = base_height - top - bottom
    by_date = resolution is not Resolution.MONTH
    days = [date.fromisoformat(label) for label in months] if by_date else []

    def x_date(day: date) -> float:
        if n == 1:
            return (left + width - right) / 2
        return left + (day - days[0]).days * (width - left - right) / (days[-1] - days[0]).days

    def x(index: int) -> float:
        if by_date:
            return x_date(days[index])
        if n == 1:
            return (left + width - right) / 2
        return left + index * (width - left - right) / (n - 1)

    def y(value: int) -> float:
        if high == low:
            return top + plot / 2
        return top + (high - value) / (high - low) * plot

    def line(series: list[tuple[int, int]], first: int, last: int, name: str, style: str) -> str:
        coords = " ".join(f"{x(c):.1f},{y(v):.1f}" for c, v in series[first : last + 1])
        return f'<polyline points="{coords}" data-series="{name}" {_line_attrs(style)}/>'

    complete_index = next((i for i, p in enumerate(points) if p.month == complete_from), len(known))
    drawn = _lines_drawn(points, complete_from, provisional)
    floor = float(base_height - bottom)
    edges = (float(left), float(width - right))
    parts = [
        _axis_days(
            days[0], days[-1], x_date, resolution=resolution, top=top, floor=floor, edges=edges
        )
        if by_date and n > 1
        else _axis(months, x, top=top, floor=floor, edges=edges)
    ]
    if _below_nil(points, provisional):
        # From nil down to the floor of the plot, or the whole plot where nil is above it.
        nil_y = y(0) if high > 0 else float(top)
        parts.append(
            f'<rect data-below-nil x="{left}" y="{nil_y:.1f}" width="{width - left - right}" '
            f'height="{floor - nil_y:.1f}" {_BELOW_NIL_FILL}/>'
        )
    if low < 0 < high:
        zero = y(0)
        parts.append(
            f'<line x1="{left}" y1="{zero:.1f}" x2="{width - right}" y2="{zero:.1f}" '
            'stroke="currentColor" stroke-opacity=".35" stroke-dasharray="2 3"/>'
            f'<text x="{width - right}" y="{zero - 4:.1f}" text-anchor="end" font-size="11" '
            'fill="var(--ink-2)">nil</text>'
        )
    if "provisional" in drawn:
        parts.append(line(dotted, 0, len(dotted) - 1, "provisional", "provisional"))
    if "partial" in drawn:
        parts.append(line(known, 0, min(complete_index, len(known) - 1), "known", "partial"))
    if "known" in drawn:
        parts.append(line(known, complete_index, len(known) - 1, "known", "known"))
    if dotted:
        parts.append(
            f'<circle cx="{x(n - 1):.1f}" cy="{y(dotted[-1][1]):.1f}" r="4" fill="none" '
            'stroke="var(--warn)" stroke-width="2"/>'
        )
    if known:
        parts.append(
            f'<circle cx="{x(known[-1][0]):.1f}" cy="{y(known[-1][1]):.1f}" r="4" '
            'fill="var(--act)"/>'
        )
    label = 'font-size="12" fill="currentColor"'
    scope = "window " if windowed else ""
    if high == low:
        top_labels = (
            f'<text x="{left}" y="16" {label}>{scope}all {unit_word(resolution)}s '
            f"{_esc(_signed(low))}</text>"
        )
        bottom_label = ""
    else:
        top_labels = (
            f'<text x="{left}" y="16" {label}>{scope}highest {_esc(_signed(high))}</text>'
        )
        bottom_label = (
            f'<text x="{left}" y="{base_height - bottom + 38}" {label}>'
            f"{scope}lowest {_esc(_signed(low))}</text>"
        )
    # Where the chart is windowed and narrowed too, the second fact has a line of its own.
    chosen_only = "chosen only, " if narrowed and not extra else ""
    latest = (
        f'<text x="{width - right}" y="{base_height - bottom + 38}" text-anchor="end" {label}>'
        f"{chosen_only}{scope}latest {_esc(_signed(known[-1][1]))}</text>"
        if known
        else ""
    )
    chosen_line = (
        f'<text x="{left}" y="{base_height - 6}" {label}>chosen accounts only, not the '
        "net worth</text>"
        if extra
        else ""
    )
    latest_provisional = (
        f'<text x="{width - right}" y="{16 if high != low else 32}" text-anchor="end" {label}>'
        f"provisional latest {_esc(_signed(dotted[-1][1]))}</text>"
        if dotted
        else ""
    )
    month_labels = (
        f'<text x="{left}" y="{height - 6}" {label}>{_esc(months[0])}</text>'
        + (
            f'<text x="{width - right}" y="{height - 6}" text-anchor="end" {label}>'
            f"{_esc(months[-1])}</text>"
            if n > 1
            else ""
        )
        + f'<text data-span x="{width / 2:.0f}" y="{height - 6}" text-anchor="middle" '
        f'font-size="12" fill="var(--ink-2)">{_esc(_span_phrase(months, resolution))}</text>'
    )
    when = _WHEN[resolution]
    desc = f"From {months[0]} to {months[-1]}, {when}. "
    if windowed:
        desc += "A window of what is held: lowest, highest, and latest are of the window. "
    if narrowed:
        desc += "Drawn from the chosen accounts only, so not the net worth. "
    if "partial" in drawn:
        desc += f"Dashed {_leaves_out(resolution)}. "
    if known:
        desc += (
            f"{'Chosen total' if narrowed else 'Net worth'}, "
            f"lowest {_signed(min(v for _, v in known))}, "
            f"highest {_signed(max(v for _, v in known))}, latest {_signed(known[-1][1])}. "
        )
    if dotted:
        desc += (
            f"Provisional total, lowest {_signed(min(v for _, v in dotted))}, highest "
            f"{_signed(max(v for _, v in dotted))}, latest {_signed(dotted[-1][1])}."
        )
    what = (
        "Net worth and provisional total" if known and dotted
        else "Provisional total" if dotted
        else "Net worth"
    )
    if narrowed:
        what = f"Chosen accounts only, not the net worth: {what.lower()}"
    return (
        '<svg role="img" aria-labelledby="chart-title chart-desc" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;height:auto;display:block">'
        f'<title id="chart-title">{what} {when}</title>'
        f'<desc id="chart-desc">{_esc(desc.strip())}</desc>'
        + "".join(parts)
        + top_labels
        + bottom_label
        + latest
        + chosen_line
        + latest_provisional
        + month_labels
        + "</svg>"
    )


def _month_rows(view: Any) -> str:
    rows = []
    known = {point.month: point for point in view.history}
    # With a provisional series the months start where either series does.
    months = [p.month for p in view.provisional_history] or [p.month for p in view.history]
    provisional = {point.month: point for point in view.provisional_history}
    for label in reversed(months):
        point = known.get(label)
        if point is None:
            cells = '<td class="muted">not counted yet</td><td class="muted">-</td>'
        else:
            partial = (
                ' <span class="pill pill-warn" title="Some of today\'s counted items had no '
                'known figure yet">partial</span>'
                if point.partial
                else ""
            )
            cells = (
                f"<td>{_figure(_balance_word(point.direction), point.net_worth)}</td>"
                f"<td>{_esc(str(point.included))} of {_esc(str(point.of))}{partial}</td>"
            )
        guess = provisional.get(label)
        extra = (
            f'<td class="muted">{_figure(_balance_word(guess.direction), guess.total)}</td>'
            if guess is not None
            else ""
        )
        rows.append(f'<tr><td class="mono nowrap">{_esc(label)}</td>{cells}{extra}</tr>')
    head = "<th>Month</th><th>Net worth</th><th>Counted</th>" + (
        "<th>Provisional total</th>" if provisional else ""
    )
    # Named here, because the nearest heading or summary above it is the chart's form.
    caption = '<caption class="visually-hidden">History, month by month</caption>'
    return f'<div class="scroll"><table>{caption}<tr>{head}</tr>' + "".join(rows) + "</table></div>"


def _legend(
    has_known: bool,
    has_provisional: bool,
    *,
    narrowed: bool = False,
    resolution: Resolution | None = None,
    last_day: str = "",
    ends_today: bool = True,
) -> str:
    """The sentence under the chart saying which line is which.

    A `resolution` says the chart is over a window: how often it has a figure, and where
    its last point is read (`ends_today` says whether that is everything held now).
    """
    if resolution is Resolution.WEEK:
        text = (
            "One figure per week, read on each Sunday and on the window's first and last "
            "day. "
        )
    elif resolution is Resolution.DAY:
        text = "One figure per day. "
    else:
        text = "One figure per month-end. "
    if has_known:
        what = "the total of the chosen accounts that is known" if narrowed else (
            "the net worth that is known"
        )
        leaves_out = _leaves_out(resolution or Resolution.MONTH)
        text += f"The solid blue line is {what}, dashed {leaves_out}. "
    if has_provisional:
        text += (
            "The dotted line is the provisional total, which counts each unknown opening "
            "balance as nil. The provisional line's shape shows real movement; its height "
            "is offset by the unknown opening balances. "
        )
    if resolution is not None:
        newest = (
            f"everything {'chosen' if narrowed else 'held'} now"
            if ends_today
            else f"the end of {last_day}"
        )
        return text + f"The last point is drawn at {newest}."
    return text + (
        "The newest month is drawn at everything chosen now."
        if narrowed
        else "The newest month is drawn at everything held now."
    )


def _label_and_ref(item: Any) -> str:
    return AccountShown.named(item.ref, item.label).inline()


def _serial(names: list[str]) -> str:
    if len(names) < 3:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


_TICK_ROLES = {
    "account": "",
    "asset": " (asset)",
    "uncounted": " (not counted: moves only the provisional line)",
}


def window_choice(position: Position, fields: Mapping[str, str]) -> WindowChoice:
    """The window the posted `fields` ask for, as this page's position holds days."""
    return _read_window(
        fields, today=date.fromisoformat(position.as_of), held_from=held_from(position)
    )


_MASKED_NOTE = '<p class="muted">Choosing a window shows values, with the chart drawn over it.</p>'


def _ticks(
    view: Any,
    drawn: frozenset[str] | None,
    *,
    unmasked: bool,
    choice: WindowChoice,
) -> str:
    """The form that asks for values: the window, and one tick per account and asset.

    The names, kinds, and directions are structure, so the masked page offers the
    same ticks and a choice can be made before values are shown. The button says
    which of the two requests it makes. The first button in the form sends `_KEEP`, so a
    bare Enter in a field redraws what the page already shows and never picks a window.
    """
    boxes = []
    for item in view.chart_items:
        ticked = drawn is None or item.key in drawn
        kind = f' <span class="muted">{_esc(_KIND_WORDS.get(item.kind, item.kind))}</span>' if (
            item.kind
        ) else ""
        way = f' <span class="muted">{_esc(_balance_word(item.direction))}</span>' if (
            item.direction
        ) else ""
        boxes.append(
            '<label class="tick"><input type="checkbox" name="chart_in" '
            f'value="{_esc(item.key)}"{" checked" if ticked else ""}> '
            f"<span>{_label_and_ref(item)}{kind}{way}"
            f'<span class="muted">{_esc(_TICK_ROLES[item.role])}</span></span></label>'
        )
    liability_note = (
        '<p class="muted">Leaving out a liability leaves the asset it is secured against '
        "in the chart unless that is unticked too.</p>"
        if any(item.role == "asset" for item in view.chart_items)
        else ""
    )
    button = "Redraw the chart" if unmasked else "Show values, chart drawn from these"
    chosen = len([item for item in view.chart_items if drawn is None or item.key in drawn])
    total = len(view.chart_items)
    summary = (
        f"Drawn from everything ({_plural(total, 'item')})"
        if chosen == total
        else f"Drawn from {chosen} of {_plural(total, 'item')}"
    )
    return (
        '<form method="post" action="/position">'
        f'<button type="submit" name="window" value="{KEEP}" class="visually-hidden" '
        f'tabindex="-1" aria-hidden="true">{_esc(button)}</button>'
        + window_controls(
            choice, today=view.as_of, note="" if unmasked else _MASKED_NOTE
        )
        + '<details class="position-ticks"><summary>'
        + _esc(summary)
        + '</summary><fieldset class="chart-choice">'
        "<legend>Draw the chart from</legend>"
        '<input type="hidden" name="chart_chosen" value="1">'
        + "".join(boxes)
        + liability_note
        + "</fieldset></details>"
        + submit_button(button).replace(
            "<button ", f'<button name="window" value="{KEEP}" ', 1
        )
        + "</form>"
    )


@dataclass(frozen=True)
class _Windowed:
    """A chart's window as the page reads it: the days, the resolution, and the words."""

    spec: WindowSpec
    window: Window
    resolution: Resolution | None
    today: date
    #: What was chosen, the days it covers, and what was cut from what was asked.
    words: str
    #: Said beneath the chart, where its lowest, highest, and latest are read: set when the
    #: window is narrower than everything held. The month table stays whole whatever it is.
    note: str = ""

    @property
    def last_day(self) -> str:
        return self.window.last.isoformat() if self.window.last else ""

    @property
    def ends_today(self) -> bool:
        return self.window.last == self.today

    def series(self, position: Position, drawn: frozenset[str] | None) -> ChartSeries:
        if (
            self.window.empty
            or self.window.first is None
            or self.window.last is None
            or self.resolution is None
        ):
            return ChartSeries((), "", ())
        days =sample_days(self.window.first, self.window.last, self.resolution)
        return series_at(position, window_points(days, self.resolution, self.today), drawn)


_PER = {Resolution.DAY: "day", Resolution.WEEK: "week", Resolution.MONTH: "month-end"}


def _windowed(position: Position, spec: WindowSpec) -> _Windowed | None:
    """The window `spec` makes of what `position` holds, with its words; None for everything.

    Everything held is the chart's default, drawn as it always was, so it is not a window.
    The conventions of a window are `date_window`'s; this only reports them.
    """
    if spec.kind == "everything":
        return None
    today = date.fromisoformat(position.as_of)
    start = held_from(position)
    try:
        window = resolve(spec, today=today, held_from=start)
    except WindowRefused as refused:
        # The form refuses such a choice before it gets here; this keeps a caller that
        # did not from drawing a chart from a guess.
        window = Window(None, None, today, today, False, False, True, str(refused))
    named_as = spec.describe(today=today)
    if window.empty or window.first is None or window.last is None:
        words = (
            f'<p data-window-words><strong>{_esc(named_as)}.</strong> '
            f"{_esc(window.empty_reason)}</p>"
        )
        return _Windowed(spec, window, None, today, words)
    resolution = resolution_for(window.first, window.last)
    sentence = (
        f"{named_as}: {window.first.isoformat()} to {window.last.isoformat()}, "
        f"one figure per {_PER[resolution]}."
    )
    if window.ended_in_future:
        sentence += (
            f" The period runs to {window.asked_last.isoformat()}, which has not come, so "
            "it is shown to today."
        )
    if window.began_before_held:
        sentence += (
            f" Nothing is held before {window.first.isoformat()}, so the window starts there."
        )
    narrower = start is not None and (window.first > start or window.last < today)
    note = (
        '<p class="muted" data-window-note>This window is narrower than everything held, so the '
        "chart's lowest, highest, and latest are of the window and not of everything held. "
        "The month table below stays monthly and whole: it is the record, and the chart is "
        "a view of it.</p>"
        if narrower
        else ""
    )
    words = f"<p data-window-words><strong>{_esc(sentence)}</strong></p>"
    return _Windowed(spec, window, resolution, today, words, note)


def _history(
    position: Position,
    view: Any,
    *,
    unmasked: bool,
    chart_in: Collection[str] | None = None,
    choice: WindowChoice,
) -> str:
    window = choice.spec
    body = "<h2>History, month by month</h2>"
    if not view.history and not view.provisional_history:
        return body + "<p>There is no history yet: nothing is counted.</p>"
    if not view.history:
        body += (
            "<p>Nothing is counted, so there is no net-worth history. The provisional "
            "total below follows only the movement of accounts whose opening balance is "
            "not known.</p>"
        )
    elif view.complete_from == view.history[0].month:
        body += (
            "<p>Every counted item has a figure in every month shown, from "
            f"{_esc(view.complete_from)}.</p>"
        )
    else:
        body += (
            f"<p>This history is complete from <strong>{_esc(view.complete_from)}</strong>. "
            "Earlier months are marked partial: they leave out the accounts and assets "
            "that had no known figure yet, so they are not comparable with later ones.</p>"
        )
    # Only an unmasked page reads the choice; the masked one offers every tick, ticked.
    keys = {item.key for item in position.chart_items}
    drawn = frozenset(set(chart_in) & keys) if unmasked and chart_in is not None else None
    ticks = _ticks(view, drawn, unmasked=unmasked, choice=choice)
    if unmasked:
        narrowed = drawn is not None and drawn != keys
        series = chart_series(position, drawn)
        body += ticks
        if choice.refusal:
            body += (
                '<p data-window-refused-note>The window was not changed, and no chart is drawn '
                "from a choice that was refused.</p>"
            )
            return body + _month_table(view, unmasked=unmasked)
        if narrowed:
            left_out = [_label_and_ref(i) for i in view.chart_items if i.key not in (drawn or ())]
            body += (
                f'<p class="warn"><strong>The chart leaves out: {_serial(left_out)}.</strong> '
                "The headline and every total on this page still count everything held; only "
                "the chart is narrower, so its lines are not the household's net worth.</p>"
            )
        windowed = _windowed(position, window) if window is not None else None
        resolution = windowed.resolution if windowed is not None else None
        if windowed is not None:
            body += windowed.words
            series = windowed.series(position, drawn)
        if drawn is not None and not drawn:
            body += "<p>Nothing is ticked, so no chart is drawn. Tick at least one to draw it.</p>"
        elif windowed is not None and windowed.window.empty:
            body += "<p>No chart is drawn.</p>"
        elif not series.history and not series.provisional:
            body += (
                "<p>Nothing that is ticked has a figure in this window, so no chart is "
                "drawn.</p>"
                if windowed is not None
                else "<p>Nothing that is ticked has a figure in any month yet, so no chart is "
                "drawn.</p>"
            )
        else:
            legend = _legend(
                bool(series.history),
                bool(series.provisional),
                narrowed=narrowed,
                resolution=resolution,
                last_day=windowed.last_day if windowed is not None else "",
                ends_today=windowed.ends_today if windowed is not None else True,
            )
            body += (
                _key(
                    _lines_drawn(series.history, series.complete_from, series.provisional),
                    narrowed=narrowed,
                    below_nil=_below_nil(series.history, series.provisional),
                    resolution=resolution,
                )
                + '<div class="chart">'
                + _chart(
                    series.history,
                    series.complete_from,
                    series.provisional,
                    narrowed=narrowed,
                    resolution=resolution or Resolution.MONTH,
                    windowed=windowed is not None,
                )
                + "</div>"
                + (windowed.note if windowed is not None else "")
                + f'<p class="muted">{legend}</p>'
            )
    else:
        body += ticks + (
            "<p>The chart is drawn when values are shown. Its shape would disclose how "
            "large the figures are, so the masked page does not draw one.</p>"
        )
    return body + _month_table(view, unmasked=unmasked)


def _month_table(view: Any, *, unmasked: bool) -> str:
    """The month table: monthly and whole whatever window the chart is drawn over.

    Folded where it is long, and always while masked: every figure in it is then the same token,
    so what it holds is the counts, and a phone screen of one repeated token earns no place.
    """
    table = _month_rows(view)
    shown = len(view.provisional_history) or len(view.history)
    if shown > TABLE_FOLD_AFTER or not unmasked:
        return (
            f"<details><summary>Month table, newest first ({shown} months)"
            f"</summary>{table}</details>"
        )
    return table


_LIMITS_HEAD = '<details><summary>What this page does not check</summary><ul class="muted">'

#: Shown only while an account is uncounted, so a page with nothing provisional
#: on it never mentions the idea.
_LIMIT_PROVISIONAL = (
    "<li>A provisional figure treats unknown opening balances as nil. It is off by "
    "whatever those accounts held before their history began, and nothing here can say "
    "how much that was.</li>"
)

_LIMITS = (
    "<li>An account's balance rests on its opening balance, which is worked out from the "
    "earliest known balance. With only one known balance, the opening balance is simply "
    "whatever makes that balance true, so transactions missing before it cannot be detected. "
    "A second known balance makes the first a test, and a later known balance that differs "
    "is flagged.</li>"
    "<li>Pending transactions are included, as the ledger's running position includes them; "
    "void transactions and folded copies of Space payments never are.</li>"
    "<li>An asset is worth what it was last observed to be worth, as of the date shown, "
    "and no newer. Its age is stated and nothing here revalues it.</li>"
    "<li>Only pounds are added up. Anything in another currency is left out.</li>"
)


def render_position(
    position: Position,
    *,
    unmasked: bool,
    chart_in: Collection[str] | None = None,
    window: WindowSpec | None = None,
    window_fields: Mapping[str, str] | None = None,
    accounts: Sequence[AccountOverview] | None = None,
    today: date | None = None,
) -> bytes:
    """The page; `chart_in` names the items the chart is drawn from, None for all.

    The days the chart covers are `window_fields`, the window's posted fields as the form
    sends them (`window_choice` reads them), or `window` where code gives a ready-made one;
    neither means everything held. Names that match no item are ignored. The masked
    rendering reads none of these.

    `accounts` are the Overview's, from which each counted account's trust is read
    (`web_position_trust`); None says the trust could not be read and the page says so once.
    `today` is the day the bars end on, the position's own day where none is given.
    """
    if unmasked and window_fields is not None:
        choice = window_choice(position, window_fields)
    else:
        choice = WindowChoice(
            DEFAULT_KEY if window is None or window.kind == "everything" or not unmasked else "",
            window if unmasked else None,
            "",
            DEFAULT_KEY,
            {},
        )
    view = Disclosed(position, unmasked=unmasked)
    counted = [(a.ref, a.label) for g in view.groups for a in g.accounts]
    # The Overview's name for an account is the one Today shows, so the two pages name it alike.
    shown = {ref: AccountShown.named(ref, label) for ref, label in counted}
    shown.update({a.ref: AccountShown.named(a.ref, a.label) for a in accounts or ()})

    def shown_of(ref: str) -> AccountShown:
        return shown.get(ref) or AccountShown(ref)

    today = today or date.fromisoformat(str(view.as_of))
    lines = unread = ""
    reading = None
    if counted:
        if accounts is None:
            unread = UNREAD
        else:
            reading = read_trust(counted, accounts, today)
            lines = figure_lines(reading, today, shown_of)
    body = _mode(unmasked) + _headline(view, lines)
    if view.groups:
        balances = (
            {
                a.ref: _figure(_balance_word(a.direction), a.balance)
                for g in view.groups
                for a in g.accounts
            }
            if unmasked
            else {}
        )
        archived = frozenset(a.ref for g in view.groups if g.key == "archived" for a in g.accounts)
        body += "<h2>Counted accounts</h2>" + _subtotals(view)
        if reading is not None:
            body += list_html(reading, today, shown_of, balances, archived)
        else:
            body += unread + _plain_list(view)
        body += _checks_fold(view)
    if view.assets:
        body += (
            "<h2>Observed assets</h2>"
            "<p>Subtotal: <strong>"
            + _figure(_balance_word(view.assets_direction), view.assets_subtotal)
            + "</strong>, each at its latest observed value.</p>"
            '<ul class="pos-left-out">' + "".join(_asset_card(a) for a in view.assets) + "</ul>"
        )
    if view.uncounted:
        body += (
            "<h2>Left out: no opening balance</h2>"
            "<p>Their balances are unknown, which is not the same as nothing. They are in "
            "no balance, subtotal, or net worth. Open one to state a balance for a date; "
            "importing older statements later does not make it wrong.</p>"
            '<ul class="pos-left-out">' + "".join(_left_out(a) for a in view.uncounted) + "</ul>"
        )
    if view.entitlements:
        body += (
            "<h2>Income entitlements, not counted as wealth</h2>"
            "<p>A promise of income has no pot behind it and no agreed way to put a capital "
            "figure on it, so these are shown as income and never added to the net worth.</p>"
            '<ul class="pos-left-out">'
            + "".join(_entitlement_card(e) for e in view.entitlements)
            + "</ul>"
        )
    body += _history(position, view, unmasked=unmasked, chart_in=chart_in, choice=choice)
    limits = _LIMITS + (_LIMIT_PROVISIONAL if view.uncounted else "")
    body += _LIMITS_HEAD + limits + "</ul></details>" + _HOME
    return render_page("Position", body, wide=True)


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class PositionPages:
    """The position page's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _position_get(self) -> None:
        # Nothing in the query string is read: the rendering is chosen by which
        # method was used.
        self._position(unmasked=False)

    def _position_post(self, form: dict[str, list[str]]) -> None:
        # A choice exists only where the form says it was made: a bare "Show values"
        # carries no ticks and means everything, where a made choice with no ticks
        # means nothing. The window's fields travel in this body and nowhere else.
        chosen = "chart_chosen" in form
        window = {name: form[name][0] for name in WINDOW_FIELDS if form.get(name)}
        self._position(
            unmasked=True,
            chart_in=form.get("chart_in", []) if chosen else None,
            window_fields=window or None,
        )

    def _position(
        self,
        *,
        unmasked: bool,
        chart_in: Collection[str] | None = None,
        window_fields: Mapping[str, str] | None = None,
    ) -> None:
        hook: Callable[[], Position] | None = self.bound_config.position_data
        if hook is None:
            # A destination in the navigation strip must resolve, as the
            # Overview's does when it is unwired: the page says so, with 200.
            self._respond(
                200,
                _page("Position", "This deployment has no position wired, so nothing is shown."),
            )
            return
        try:
            position = hook()
        except Exception as fault:
            # Not str(fault): its text is not under this module's control and a
            # figure could ride in it.
            say("position.fault", kind=type(fault).__name__)
            self._respond(500, _page("Position failed", "The position could not be built."))
            return
        # The Overview is the one place each account's trust is read from. One that cannot be
        # read leaves the figure and the chart whole and the page says the trust is missing.
        accounts: tuple[AccountOverview, ...] | None = None
        overview = self.bound_config.overview
        if overview is not None:
            try:
                accounts = overview(False).accounts
            except Exception as fault:
                say("position.overview-fault", kind=type(fault).__name__)
        self._respond(
            200,
            render_position(
                position,
                unmasked=unmasked,
                chart_in=chart_in,
                window_fields=window_fields,
                accounts=accounts,
            ),
            no_store=unmasked,
        )
