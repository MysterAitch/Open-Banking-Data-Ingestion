"""The Overview's markup: what needs a person, then one row per account.

Hierarchy does the work. An item that needs attention is the heaviest thing on
the page; a healthy answer is one calm sentence that still says what was
checked and when, because an empty list must never be mistakable for checks
that never ran. Every state is a word as well as a colour.

NO FIGURES. This is served by GET, and no GET shows a monetary value. The
account rows hold counts and dates, and `Overview` carries nothing else.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import UTC, date, datetime
from urllib.parse import quote

from .overview import (
    ALERT_CONDITIONS,
    ARCHIVED,
    CURRENT,
    EMPTY,
    FILE_ONLY,
    NEVER_ASKED,
    OVERVIEW_CACHE_SECONDS,
    OVERVIEW_CHECKS,
    QUIET,
    REBUILDING,
    SILENT,
    SOON,
    STATE_RULES,
    AccountOverview,
    AttentionItem,
    Overview,
)
from .standing_data import standing_lines

_esc = html.escape

_SEVERITY_CLASS = {1: "now", SOON: "soon", 3: "housekeeping"}
_SEVERITY_PILL = {1: "pill-bad", SOON: "pill-warn", 3: "pill-quiet"}

_STATE_PILL = {
    CURRENT: "pill-ok",
    QUIET: "pill-quiet",
    SILENT: "pill-bad",
    NEVER_ASKED: "pill-warn",
    FILE_ONLY: "pill-quiet",
    EMPTY: "pill-quiet",
    ARCHIVED: "pill-quiet",
    REBUILDING: "pill-warn",
}

#: States for which "when did the provider last answer" is not a question.
_NOT_ASKED_ABOUT = frozenset({FILE_ONLY, EMPTY, ARCHIVED})


def _clock(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%MZ")


def _age(moment: datetime, now: datetime) -> str:
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return f"{seconds} seconds ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    return f"{round(seconds / 3600)} hours ago"


def _days_ago(day: date, today: date) -> str:
    days = (today - day).days
    return {0: "today", 1: "yesterday"}.get(days, f"{days} days ago")


def _item_html(item: AttentionItem) -> str:
    css = _SEVERITY_CLASS[item.severity]
    pill = _SEVERITY_PILL[item.severity]
    return (
        f'<li class="{css}">'
        f'<p><span class="pill {pill}">{_esc(item.severity_word)}</span> '
        f"{_esc(item.message)}</p>"
        f'<p class="muted">{_esc(item.remedy)} '
        f'<a class="tap" href="{_esc(item.href)}">Open</a></p></li>'
    )


def _checks_line(overview: Overview) -> str:
    """How many checks ran, when, and how old that is."""
    if overview.checks_run == overview.checks_total:
        return f"{overview.checks_total} checks run at {_clock(overview.generated_at)}"
    return (
        f"{overview.checks_run} of {overview.checks_total} checks run at "
        f"{_clock(overview.generated_at)}; the rest could not run"
    )


def _attention_html(overview: Overview, now: datetime) -> str:
    names = ", ".join((*ALERT_CONDITIONS, *OVERVIEW_CHECKS))
    if overview.items:
        count = len(overview.items)
        lead = (
            f'<p><strong>{count} {"thing needs" if count == 1 else "things need"} '
            f"attention.</strong> {_esc(_checks_line(overview))}.</p>"
        )
        body = lead + '<ol class="attention">' + "".join(map(_item_html, overview.items)) + "</ol>"
    else:
        body = (
            f'<p class="allclear"><strong>{_esc(_checks_line(overview))}: '
            "nothing needs attention.</strong></p>"
        )
    return (
        body
        + f'<p class="muted">Checked: {_esc(names)}. Assembled {_age(overview.generated_at, now)}'
        f" and reused for up to {OVERVIEW_CACHE_SECONDS} seconds. "
        '<a class="tap" href="/?fresh=1">Check again</a></p>'
    )


def _sources_html(sources: tuple[str, ...]) -> str:
    return " ".join(f'<span class="pill pill-quiet">{_esc(source)}</span>' for source in sources)


def _state_html(account: AccountOverview) -> str:
    archived = account.state == ARCHIVED and account.closed is not None
    since = f" since {account.closed.isoformat()}" if archived and account.closed else ""
    pill = _STATE_PILL[account.state]
    return f'<span class="pill {pill}">{_esc(account.state)}</span>{_esc(since)}'


def _asked_html(account: AccountOverview, today: date) -> str:
    if account.state in _NOT_ASKED_ABOUT:
        return '<span class="muted">-</span>'
    if account.last_asked is None:
        return "never"
    asked = account.last_asked.date()
    return f"{asked.isoformat()} ({_days_ago(asked, today)})"


def _account_row(account: AccountOverview, today: date, rebuilding: str = "") -> str:
    target = quote(account.ref, safe="")
    if account.bound is None:
        bound = '<span class="muted">-</span>'
    else:
        bound = "bound" if account.bound else "not bound"
    items = (
        f'<a class="tap bad" href="#attention">{account.items} '
        f'{"item" if account.items == 1 else "items"}</a>'
        if account.items
        else '<span class="muted">none</span>'
    )
    newest = (
        f"{account.newest.isoformat()} ({_days_ago(account.newest, today)})"
        if account.newest
        else '<span class="muted">-</span>'
    )
    def fact(name: str, value: str) -> str:
        return f"<div><dt>{name}</dt><dd>{value}</dd></div>"

    # One card per account, not a table row.
    # Eight columns do not fit a phone: as a table the reference wrapped
    # mid-word and the page had to be scrolled sideways to reach the links,
    # which are the reason for coming here.
    return (
        '<li class="account">'
        f'<p class="account-name">{_state_html(account)} '
        f"<strong>{_esc(account.label)}</strong></p>"
        f'<span class="mono muted">{_esc(account.ref)}</span>'
        f'<p class="account-sources">{_sources_html(account.sources)}</p>'
        '<dl class="facts">'
        + fact("Rows", f"{account.rows:,}")
        + fact("Newest row", newest)
        + fact("Provider last answered", _asked_html(account, today))
        + fact("Actual", bound)
        + fact("Needs attention", items)
        + (
            fact("Verification", _esc(rebuilding))
            if rebuilding
            else ""
            if account.standing is None
            else fact(
                "Verification",
                "<br>".join(_esc(line) for line in standing_lines(account.standing)),
            )
        )
        + "</dl>"
        f'<p class="account-links"><a class="tap" href="/ledger?ref={_esc(target)}">Ledger</a> '
        f'<a class="tap" href="/account?ref={_esc(target)}">Shape</a></p>'
        "</li>"
    )


def _accounts_html(overview: Overview) -> str:
    today = overview.generated_at.date()
    paused = overview.rebuilding.sentence() if overview.rebuilding is not None else ""
    rows = "".join(_account_row(account, today, paused) for account in overview.accounts)
    legend = "".join(
        f"<li><strong>{_esc(state)}</strong> - {_esc(rule)}</li>"
        for state, rule in STATE_RULES.items()
    )
    accounts = (
        f'<ul class="accounts">{rows}</ul>'
        if overview.accounts
        else "<p>No account is held or declared yet.</p>"
    )
    return (
        accounts
        + '<p class="muted">Counts and dates only. Amounts are on each ledger, '
        "masked until asked for.</p>"
        '<p><a class="tap" href="/position">Financial position</a> '
        '<span class="muted">- what is held, in total and month by month</span></p>'
        "<details><summary>What each state means</summary>"
        f'<ul class="legend">{legend}</ul></details>'
    )


#: Where a person goes from the Accounts cards: (destination, label).
#: Coverage is per source and the cards are per account, which is why both exist.
ACCOUNT_LINKS: tuple[tuple[str, str], ...] = (
    ("/coverage", "Coverage by source"),
    ("/accounts", "Declared accounts"),
    ("/import", "Import"),
    ("/review", "Categorise"),
)


def _account_links_html() -> str:
    links = "".join(
        f'<li><a class="tap outline" href="{_esc(href)}">{_esc(label)}</a></li>'
        for href, label in ACCOUNT_LINKS
    )
    return f'<ul class="linkrow">{links}</ul>'


def _notice(message: str) -> str:
    return (
        '<ol class="attention"><li class="now">'
        f'<p><span class="pill pill-bad">Checks did not run</span> {_esc(message)}</p></li></ol>'
    )


def overview_html(
    load: Callable[[bool], Overview] | None,
    *,
    fresh: bool = False,
    now: datetime | None = None,
) -> str:
    """The Overview section, or - if it cannot be built - a notice saying so.

    Neither an unwired hook nor one that raises is allowed to render as an
    empty list: both say that no checks ran.
    """
    now = now or datetime.now(UTC)
    if load is None:
        attention = _notice("This deployment has no Overview wired, so nothing was checked.")
        accounts = '<p class="muted">No account list is available.</p>'
    else:
        try:
            overview = load(fresh)
        except Exception as error:
            attention = _notice(
                f"The overview could not be assembled ({type(error).__name__}), so no checks "
                "ran. The web log has the error."
            )
            accounts = '<p class="muted">No account list is available.</p>'
        else:
            attention = _attention_html(overview, now)
            accounts = _accounts_html(overview)
    return (
        '<div class="overview">'
        '<section id="attention"><h2>Needs attention</h2>'
        f"{attention}</section>"
        f'<section id="accounts"><h2>Accounts</h2>{accounts}{_account_links_html()}</section>'
        "</div>"
    )
