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
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .callback import render_page
from .logs import say
from .masking import MASKED_TOTAL, Disclosed
from .position import MonthPoint, Position
from .web_accounts import submit_button
from .web_ledger import _balance_word

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


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _ledger_href(ref: str) -> str:
    return f"/ledger?ref={_esc(quote(ref, safe=''))}"


def _days(days: int) -> str:
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def _figure(word: str, amount: str) -> str:
    return f'{_esc(word)} <span class="mono nowrap">{_esc(amount)}</span>'


def _fact(name: str, value: str) -> str:
    return f"<div><dt>{name}</dt><dd>{value}</dd></div>"


def _ref_line(view: Any) -> str:
    """The reference beneath a label, unless the label is the reference."""
    if view.label == view.ref:
        return ""
    return f'<span class="mono muted">{_esc(view.ref)}</span>'


def _account_card(view: Any) -> str:
    archived = (
        '<span class="pill pill-quiet">archived</span> ' if view.archived else ""
    )
    kind = f'<span class="muted">{_esc(view.kind)}</span><br>' if view.kind else ""
    through = (
        f"{_esc(view.rows_through)} ({_esc(_days(view.rows_through_age_days))})"
        if view.rows_through
        else '<span class="muted">no rows held</span>'
    )
    if view.checks_differ:
        checks = (
            f'<a class="tap bad" href="{_ledger_href(view.ref)}">'
            f"{_plural(view.checks_differ, 'check')} differ</a>"
        )
    elif view.checks_agree:
        checks = f"{_plural(view.checks_agree, 'later check')} agree"
    else:
        checks = '<span class="muted">none: one balance stated, so nothing tests it</span>'
    flag = (
        '<p class="warn">The rows between its stated balances do not add up, so this '
        "balance may be wrong. It is still counted. "
        f'<a class="tap" href="{_ledger_href(view.ref)}">See its ledger</a></p>'
        if view.checks_differ
        else ""
    )
    return (
        '<li class="account">'
        f'<p class="account-name">{archived}<strong>{_esc(view.label)}</strong></p>'
        f"{kind}{_ref_line(view)}"
        f'<p class="figure">{_figure(_balance_word(view.direction), view.balance)}</p>'
        '<dl class="facts">'
        + _fact("Rows through", through)
        + _fact("Rows held", _esc(f"{int(view.rows):,}"))
        + _fact("Later balance checks", checks)
        + "</dl>"
        + flag
        + f'<p class="account-links"><a class="tap" href="{_ledger_href(view.ref)}">Ledger</a></p>'
        "</li>"
    )


def _group_html(group: Any) -> str:
    members = group.accounts
    cards = '<ul class="accounts">' + "".join(_account_card(a) for a in members) + "</ul>"
    subtotal = (
        f'<p>Subtotal: <strong>{_figure(_balance_word(group.direction), group.subtotal)}'
        "</strong></p>"
    )
    title = f"{_esc(_GROUP_TITLES[group.key])} ({len(members)})"
    if group.key == "archived":
        return (
            f"<details><summary><strong>{title}</strong>&nbsp;- counted like any other "
            f"account, folded away</summary>{subtotal}{cards}</details>"
        )
    return f"<h3>{title}</h3>{subtotal}{cards}"


def _asset_card(view: Any) -> str:
    return (
        '<li class="account">'
        f'<p class="account-name"><strong>{_esc(view.asset_id)}</strong></p>'
        f'<span class="muted">{_esc(_KIND_WORDS.get(view.kind, view.kind))}</span>'
        f'<p class="figure">{_figure(_balance_word(view.direction), view.value)}</p>'
        '<dl class="facts">'
        + _fact(
            "Observed",
            f'<span class="nowrap">{_esc(view.observed_on)}</span> ({_esc(_days(view.age_days))})',
        )
        + _fact("Source", _esc(view.source))
        + _fact("Observations", _esc(str(view.observations)))
        + "</dl></li>"
    )


def _entitlement_card(view: Any) -> str:
    return (
        '<li class="account">'
        f'<p class="account-name"><strong>{_esc(view.asset_id)}</strong></p>'
        f'<span class="muted">{_esc(_KIND_WORDS.get(view.kind, view.kind))}</span>'
        f'<p class="figure"><span class="mono nowrap">{_esc(view.annual_income)}</span> a year</p>'
        '<dl class="facts">'
        + _fact(
            "Observed",
            f'<span class="nowrap">{_esc(view.observed_on)}</span> ({_esc(_days(view.age_days))})',
        )
        + _fact("Source", _esc(view.source))
        + "</dl></li>"
    )


def _uncounted_card(view: Any) -> str:
    if view.state == "withheld":
        reason = f"No opening balance could be derived: {_esc(view.withheld)}."
    else:
        reason = (
            "No balance has been stated for it, and neither the bank's records nor a "
            "held statement supplies one."
        )
    through = (
        f"{_esc(_plural(int(view.rows), 'row'))} held, through {_esc(view.rows_through)}"
        if view.rows_through
        else "no rows held"
    )
    return (
        '<li class="account">'
        f'<p class="account-name"><span class="pill pill-bad">not counted</span> '
        f"<strong>{_esc(view.label)}</strong></p>"
        f"{_ref_line(view)}"
        f"<p>{reason}</p>"
        f'<p class="muted">{through}</p>'
        f'<p class="account-links"><a class="tap" href="{_ledger_href(view.ref)}">'
        "State a balance on its ledger</a></p>"
        "</li>"
    )


def _headline(view: Any) -> str:
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
            f"<p>{_esc(counted)} As at {_esc(view.as_of)}.</p>"
        )
    uncounted = int(view.accounts_uncounted)
    if uncounted:
        body += (
            '<p class="warn"><strong>'
            f"{_plural(uncounted, 'account')} {'is' if uncounted == 1 else 'are'} not "
            "counted</strong> because no opening balance is known for "
            f"{'it' if uncounted == 1 else 'them'}. {'It is' if uncounted == 1 else 'They are'} "
            "left out of every figure here, and listed below with where to state a "
            "balance.</p>"
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
        return (
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            '<p><a class="button" href="/position">Hide values (masked view)</a></p>'
        )
    return (
        '<p class="muted">Values are masked. Every figure here is a balance or a '
        f"total, so each shows as {MASKED_TOTAL} whatever its size: the number of "
        "digits in a total would say how much there is. Counts, dates, sources, "
        "directions, and flags are real.</p>"
        '<form method="post" action="/position">' + submit_button("Show values") + "</form>"
    )


def _signed(minor: int) -> str:
    whole, pence = divmod(abs(minor), 100)
    return f"{'-' if minor < 0 else ''}£{whole:,}.{pence:02d}"


def _chart(points: tuple[MonthPoint, ...], complete_from: str) -> str:
    """The net-worth line as inline SVG. Called only where values are shown."""
    width, height = 400, 240
    left, right, top, bottom = 12, 12, 30, 42
    n = len(points)
    values = [p.net_worth.minor for p in points]
    low, high = min(values), max(values)
    plot = height - top - bottom

    def x(index: int) -> float:
        if n == 1:
            return (left + width - right) / 2
        return left + index * (width - left - right) / (n - 1)

    def y(value: int) -> float:
        if high == low:
            return top + plot / 2
        return top + (high - value) / (high - low) * plot

    def line(first: int, last: int, extra: str) -> str:
        coords = " ".join(f"{x(i):.1f},{y(values[i]):.1f}" for i in range(first, last + 1))
        return (
            f'<polyline points="{coords}" fill="none" stroke="#2563eb" '
            f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"{extra}/>'
        )

    complete_index = next((i for i, p in enumerate(points) if p.month == complete_from), n)
    parts = []
    if low < 0 < high:
        zero = y(0)
        parts.append(
            f'<line x1="{left}" y1="{zero:.1f}" x2="{width - right}" y2="{zero:.1f}" '
            'stroke="currentColor" stroke-opacity=".35" stroke-dasharray="2 3"/>'
            f'<text x="{width - right}" y="{zero - 4:.1f}" text-anchor="end" font-size="11" '
            'fill="currentColor" fill-opacity=".6">nil</text>'
        )
    if n > 1:
        if complete_index > 0:
            parts.append(
                line(0, min(complete_index, n - 1), ' stroke-dasharray="5 5" stroke-opacity=".6"')
            )
        if complete_index < n - 1:
            parts.append(line(complete_index, n - 1, ""))
    parts.append(
        f'<circle cx="{x(n - 1):.1f}" cy="{y(values[-1]):.1f}" r="4" fill="#2563eb"/>'
    )
    label = 'font-size="12" fill="currentColor"'
    if high == low:
        top_labels = f'<text x="{left}" y="16" {label}>all months {_esc(_signed(low))}</text>'
        bottom_label = ""
    else:
        top_labels = (
            f'<text x="{left}" y="16" {label}>highest {_esc(_signed(high))}</text>'
        )
        bottom_label = (
            f'<text x="{left}" y="{height - bottom + 16}" {label}>'
            f"lowest {_esc(_signed(low))}</text>"
        )
    latest = (
        f'<text x="{width - right}" y="{height - bottom + 16}" text-anchor="end" {label}>'
        f"latest {_esc(_signed(values[-1]))}</text>"
    )
    months = (
        f'<text x="{left}" y="{height - 6}" {label}>{_esc(points[0].month)}</text>'
        + (
            f'<text x="{width - right}" y="{height - 6}" text-anchor="end" {label}>'
            f"{_esc(points[-1].month)}</text>"
            if n > 1
            else ""
        )
    )
    desc = (
        f"Net worth at each month-end from {points[0].month} to {points[-1].month}. "
        f"Lowest {_signed(low)}, highest {_signed(high)}, latest {_signed(values[-1])}."
    )
    return (
        '<svg role="img" aria-labelledby="chart-title chart-desc" '
        f'viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;height:auto;display:block">'
        '<title id="chart-title">Net worth at each month-end</title>'
        f'<desc id="chart-desc">{_esc(desc)}</desc>'
        + "".join(parts)
        + top_labels
        + bottom_label
        + latest
        + months
        + "</svg>"
    )


def _month_rows(view: Any) -> str:
    rows = []
    for point in reversed(view.history):
        partial = (
            ' <span class="pill pill-warn" title="Some of today\'s counted items had no '
            'known figure yet">partial</span>'
            if point.partial
            else ""
        )
        rows.append(
            "<tr>"
            f'<td class="mono nowrap">{_esc(point.month)}</td>'
            f"<td>{_figure(_balance_word(point.direction), point.net_worth)}</td>"
            f"<td>{_esc(str(point.included))} of {_esc(str(point.of))}{partial}</td>"
            "</tr>"
        )
    return (
        '<div class="scroll"><table><tr><th>Month</th><th>Net worth</th>'
        "<th>Counted</th></tr>" + "".join(rows) + "</table></div>"
    )


def _history(position: Position, view: Any, *, unmasked: bool) -> str:
    body = "<h2>History, month by month</h2>"
    if not view.history:
        return body + "<p>There is no history yet: nothing is counted.</p>"
    first = view.history[0].month
    if view.complete_from == first:
        body += (
            f"<p>Every counted item has a figure in every month shown, from {_esc(first)}.</p>"
        )
    else:
        body += (
            f"<p>This history is complete from <strong>{_esc(view.complete_from)}</strong>. "
            "Earlier months are marked partial: they leave out the accounts and assets "
            "that had no known figure yet, so they are not comparable with later ones.</p>"
        )
    if unmasked:
        body += (
            '<div class="chart">'
            + _chart(position.history, position.complete_from)
            + "</div>"
            '<p class="muted">One figure per month-end. A dashed line marks the partial '
            "months. The newest month is drawn at everything held now.</p>"
        )
    else:
        body += (
            "<p>The chart is drawn when values are shown. Its shape would disclose how "
            "large the figures are, so the masked page does not draw one.</p>"
        )
    table = _month_rows(view)
    if len(view.history) > TABLE_FOLD_AFTER:
        body += (
            f"<details><summary>Month table, newest first ({len(view.history)} months)"
            f"</summary>{table}</details>"
        )
    else:
        body += table
    return body


_LIMITS = (
    '<h2>What this page does not check</h2><ul class="muted">'
    "<li>An account's balance rests on its opening balance, which is derived from the "
    "earliest balance stated for it. An opening derived from a single anchor absorbs "
    "every missing or surplus row before that anchor and nothing here can tell; a "
    "second anchor turns it into a test, and a later one that differs is flagged.</li>"
    "<li>Pending rows are included, as the ledger's running position includes them; "
    "void rows never are.</li>"
    "<li>An asset is worth what it was last observed to be worth, as of the date shown, "
    "and no newer. Its age is stated and nothing here revalues it.</li>"
    "<li>Only pounds are added up. Anything in another currency is left out.</li>"
    "</ul>"
)


def render_position(position: Position, *, unmasked: bool) -> bytes:
    view = Disclosed(position, unmasked=unmasked)
    body = _mode(unmasked) + _headline(view)
    if view.groups:
        body += "<h2>Accounts</h2>" + "".join(_group_html(g) for g in view.groups)
    if view.assets:
        body += (
            "<h2>Observed assets</h2>"
            "<p>Subtotal: <strong>"
            + _figure(_balance_word(view.assets_direction), view.assets_subtotal)
            + "</strong>, each at its latest observed value.</p>"
            '<ul class="accounts">' + "".join(_asset_card(a) for a in view.assets) + "</ul>"
        )
    if view.uncounted:
        body += (
            "<h2>Not counted: no opening balance</h2>"
            "<p>Their balances are unknown, which is not the same as nothing. They are in "
            "no total.</p>"
            '<ul class="accounts">' + "".join(_uncounted_card(a) for a in view.uncounted) + "</ul>"
        )
    if view.entitlements:
        body += (
            "<h2>Income entitlements, not counted as wealth</h2>"
            "<p>A promise of income has no pot behind it and no agreed way to put a capital "
            "figure on it, so these are shown as income and never added to the net worth.</p>"
            '<ul class="accounts">'
            + "".join(_entitlement_card(e) for e in view.entitlements)
            + "</ul>"
        )
    body += _history(position, view, unmasked=unmasked)
    body += _LIMITS + _HOME
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

    def _position_post(self) -> None:
        self._position(unmasked=True)

    def _position(self, *, unmasked: bool) -> None:
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
        self._respond(200, render_position(position, unmasked=unmasked), no_store=unmasked)
