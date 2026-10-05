"""The three hub pages: where data comes in, whether it is healthy, and what explains a fault.

Each is a destination of the navigation strip (see `navigation`) and is useful in itself, not a
list of links: Bring in says the state of every way data enters, Checks says the result of
every check, and Diagnostics says what each page there is for and carries the repairs.

EVERY PART IS GUARDED ON ITS OWN. A hub is the page a person opens when something is wrong, so
a hook that raises hides its own part and says so, and the rest of the page is still there.

NO AMOUNT, NO DESCRIPTION. Every GET is masked: these pages carry counts, dates, names, and
statuses, read from the same hooks the pages they link to read.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote

from .account_names import AccountShown, AccountsShown
from .alerts import consent_rung
from .bank_balances import BANK_SOURCE
from .callback import render_page
from .checks_index import CHECKS, CheckResult, result_of
from .fetch_gaps import FetchReport
from .namespaces import UNASSIGNED_ACCOUNT
from .navigation import PAGE_NAMES, page_name
from .overview import OVERVIEW_CACHE_SECONDS, Overview
from .page_times import UTC_NOTE, instant_of
from .plural import plural
from .pull import STARLING_CONNECTION
from .web_gaps import verdict_sentence
from .web_sections import HookTimer

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .connections import ConnectionStore
    from .web import WebConfig

_esc = html.escape


@lru_cache(maxsize=1)
def _served_routes() -> frozenset[str]:
    import inspect

    from .web import ConnectionHandler

    source = inspect.getsource(ConnectionHandler._dispatch_get)
    return frozenset(re.findall(r'route == "(/[a-z-]+)"', source))


def dispatcher_serves(route: str) -> bool:
    """Whether the GET dispatcher answers `route`, so a link is offered only to a page that exists.

    Read from the dispatcher's own source because it is a literal chain of comparisons and
    there is no table to ask; a page another change adds is linked the day it is served.
    """
    return route in _served_routes()


# ------------------------------------------------------------------------------------ shared


def _chip(label: str, tone: str) -> str:
    css = {"ok": "pill pill-ok", "bad": "pill pill-bad", "warn": "pill pill-warn"}.get(
        tone, "pill"
    )
    return f'<span class="{css}">{_esc(label)}</span>'


def _row(
    href: str,
    name: str,
    sentence: str,
    *,
    chip: str = "",
    more: str = "",
    tone: str = "",
) -> str:
    """One row of a hub: the page's name as its link, a chip if there is a state, one sentence.

    A `tone` of "bad" or "warn" gives the row a rail; any other leaves it plain.
    """
    rail = f" hub-row-{tone}" if tone in ("bad", "warn") else ""
    return (
        f'<li class="hub-row{rail}"><div class="hub-head">'
        f'<a class="tap hub-name" href="{_esc(href)}">{_esc(name)}</a>{chip}</div>'
        f'<p class="hub-line">{sentence}</p>{more}</li>'
    )


def _rows(rows: list[str]) -> str:
    return f'<ul class="hub-rows">{"".join(rows)}</ul>'


def _formerly(route: str) -> str:
    formerly = PAGE_NAMES[route].formerly
    return f'<p class="muted formerly">Formerly called {_esc(formerly)}.</p>' if formerly else ""


def _unread(what: str) -> str:
    return f'<p class="muted">{_esc(what)} could not be read just now.</p>'


def _stamp(moment: str) -> str:
    """A recorded instant in the house form (`page_times`); the page says its zone once."""
    return _esc(instant_of(moment))


# ------------------------------------------------------------------------------------ Checks


def render_checks(overview: Overview | None, *, review_flags_linked: bool = False) -> bytes:
    """One row per check, each with the result the Overview last found."""
    rows = []
    for spec in CHECKS:
        found: CheckResult = result_of(spec, overview)
        more = ""
        if review_flags_linked and spec.route == "/review-report":
            more = (
                '<p class="hub-more">'
                '<a class="tap" href="/review-flags">Resolve the flags</a></p>'
            )
        rows.append(
            _row(
                spec.route,
                page_name(spec.route),
                _esc(found.sentence),
                chip=_chip(found.state, found.tone),
                more=more,
                tone=found.tone,
            )
        )
    if overview is None:
        read = "The checks on Today could not be read just now."
    else:
        at = overview.generated_at.astimezone(UTC).strftime("%H:%M")
        read = (
            f"Read at {at} from the checks on Today, and reused for up to "
            f"{OVERVIEW_CACHE_SECONDS} seconds."
        )
    body = (
        '<p class="lede">Is the data healthy? Each row is one check, with what the checks '
        "on Today last found. Open a row for the detail.</p>"
        + _formerly("/checks")
        + _rows(rows)
        + f'<p class="muted hub-age">{_esc(read)} '
        '<a class="tap" href="/checks?fresh=1">Check again</a></p>'
    )
    return render_page(page_name("/checks"), body, wide=True, body_class="hub-page")


# ------------------------------------------------------------------------------------ Bring in


def _connection_line(
    name: str, expires: str, days: int | None, answered: str | None
) -> str:
    heard = f"last answered {_stamp(answered)}" if answered else "has never answered"
    if days is None:
        consent = "no consent expiry recorded"
    else:
        consent = f"consent expires {_esc(expires)} ({days} days)"
    return f"<li><strong>{_esc(name)}</strong> - {heard}; {consent}</li>"


@dataclass(frozen=True)
class BankFeed:
    """What this process can say of the household's own bank's feed, read with its own API.

    `configured` is whether the bank's access token resolved when the process started; a token
    supplied later is not seen until it restarts. `answered` is the instant the feed last had an
    ask land, or None when none has. `accounts` are the names of the accounts its rows are held
    under, which is every account the feed has ever delivered a row for.
    """

    configured: bool
    answered: str | None
    accounts: tuple[AccountShown, ...]


#: How many account names are listed before the line says a count alone.
_NAMED_ACCOUNTS = 3


def bank_feed_of(
    *,
    configured: bool,
    answered: str | None,
    held: dict[tuple[str, str], list[str]],
    names: AccountsShown,
) -> BankFeed:
    """The feed's state from what the page's hooks give: who fed which account, and the names."""
    refs = sorted({account for account, source in held if source == BANK_SOURCE})
    return BankFeed(
        configured=configured, answered=answered, accounts=tuple(names.of(ref) for ref in refs)
    )


def _feed_row(feed: BankFeed | None) -> str:
    """The bank's own feed as a connection in its own right, quiet where it is not set up.

    It is the way data most reliably comes in, and is reached with a token the owner holds
    rather than a bank sign-in, so it is neither among the aggregator's connections nor a
    fault when absent: a deployment without it says so and carries on.
    """
    name = "The bank's own feed"
    if feed is None:
        return _row("/attempts", name, "Not available on this instance.")
    if feed.configured:
        heard = (
            f"Last answered {_stamp(feed.answered)}." if feed.answered else "Has never answered."
        )
        said = [_esc(f"Read directly from the bank, not through the aggregator. {heard}")]
    else:
        said = [
            _esc("Not configured here: the bank's access token is not set, so nothing is read.")
        ]
        if feed.answered:
            said.append(_esc(f"It last answered {_stamp(feed.answered)}."))
    if feed.accounts:
        if len(feed.accounts) <= _NAMED_ACCOUNTS:
            shown = ", ".join(account.inline() for account in feed.accounts)
            said.append(f"Feeds {_esc(plural(len(feed.accounts), 'account'))}: {shown}.")
        else:
            said.append(_esc(f"Feeds {plural(len(feed.accounts), 'account')}."))
    elif feed.configured:
        said.append("No account is fed yet.")
    chip = "" if not feed.configured or feed.answered else _chip("no answer yet", "warn")
    return _row("/attempts", name, " ".join(said), chip=chip)


def _connected_line(feed: BankFeed | None, aggregator: int) -> str:
    """What is connected, in two terms: the bank's own feed, and the aggregator's banks."""
    parts = []
    if feed is not None and feed.configured:
        parts.append("1 bank's own feed")
    if aggregator:
        parts.append(f"{plural(aggregator, 'bank')} through the aggregator")
    if not parts:
        return ""
    return f'<p class="hub-age">Connected: {_esc(" and ".join(parts))}.</p>'


def _connections_row(
    store: ConnectionStore | None,
    answered: dict[str, str],
    *,
    now: datetime,
) -> str:
    try:
        connections = sorted(store, key=lambda c: c.connection_id) if store is not None else []
    except Exception:
        return _row("/connections", "Connect a bank", _esc("The connections could not be read."))
    if not connections:
        return _row(
            "/connections",
            "Connect a bank",
            "No bank is connected. A bank's own sign-in links its accounts here.",
        )
    dated = [
        (expiry, days)
        for connection in connections
        if (expiry := connection.consent_expires_on()) is not None
        and (days := connection.consent_days_remaining(now=now)) is not None
    ]
    count = f"{plural(len(connections), 'bank')} connected through the aggregator"
    chip = ""
    sentence = count + "."
    if dated:
        soonest, days = min(dated, key=lambda pair: pair[0])
        sentence = f"{count}. The soonest consent expires {soonest.isoformat()} ({days} days)."
        if consent_rung(days) is not None:
            chip = _chip("reconnect soon", "warn")
    lines = "".join(
        _connection_line(
            connection.connection_id,
            expiry.isoformat() if (expiry := connection.consent_expires_on()) else "",
            connection.consent_days_remaining(now=now),
            answered.get(connection.connection_id),
        )
        for connection in connections
    )
    return _row(
        "/connections",
        "Connect a bank",
        _esc(sentence),
        chip=chip,
        more=f'<ul class="hub-list">{lines}</ul>',
    )


def _statements_row(kept: Callable[[], list[dict[str, object]]] | None) -> str:
    if kept is None:
        return _row("/statements", "Statements kept", "Not available on this instance.")
    try:
        entries = kept()
    except Exception:
        return _row("/statements", "Statements kept", "The kept statements could not be read.")
    if not entries:
        return _row("/statements", "Statements kept", "No statement has been kept yet.")
    unassigned = sum(1 for e in entries if e["account_ref"] == UNASSIGNED_ACCOUNT)
    unread = sum(1 for e in entries if not e.get("parser") or e.get("refusal"))
    parts = [f"{len(entries)} kept"]
    parts.append(f"{unassigned} waiting for an account")
    parts.append(f"{unread} not read")
    waiting = bool(unassigned or unread)
    return _row(
        "/statements",
        "Statements kept",
        _esc(", ".join(parts) + "."),
        chip=_chip("waiting", "warn") if waiting else _chip("all filed", "ok"),
    )


def _fetch_row(report: Callable[[], FetchReport] | None) -> str:
    """The row that leads the hub: what is still to fetch, in the page's own verdict sentence."""
    name = page_name("/gaps")
    if report is None:
        return ""
    try:
        found = report()
    except Exception:
        return _row("/gaps", name, "What is still to fetch could not be read just now.")
    waiting = bool(found.gaps)
    return _row(
        "/gaps",
        name,
        _esc(verdict_sentence(found)),
        chip=_chip("to fetch", "warn") if waiting else _chip("nothing due", "ok"),
        tone="warn" if waiting else "",
    )


def _count_connections(store: ConnectionStore | None) -> int:
    try:
        return len(list(store)) if store is not None else 0
    except Exception:
        return 0


def render_bring_in(
    store: ConnectionStore | None,
    *,
    last_answered: Callable[[], dict[str, str]] | None = None,
    kept_statements: Callable[[], list[dict[str, object]]] | None = None,
    bank_feed: Callable[[dict[str, str]], BankFeed] | None = None,
    fetch_report: Callable[[], FetchReport] | None = None,
    now: datetime | None = None,
) -> bytes:
    """Every way data enters, each with its state, each linking to the page that does it.

    `bank_feed` is given what each connection last answered, since the feed's own answer is among
    them, and returns what is known of the feed; a hook that fails leaves the row saying the feed
    could not be read and not the whole page.
    """
    moment = now or datetime.now(UTC)
    answered: dict[str, str] = {}
    if last_answered is not None:
        try:
            answered = last_answered()
        except Exception:
            answered = {}
    feed: BankFeed | None = None
    unread_feed = False
    if bank_feed is not None:
        try:
            feed = bank_feed(answered)
        except Exception:
            unread_feed = True
    # What is still to fetch leads, since the owner opens this page to find out; then the bank's
    # own feed, the most direct way in, which the line above names first.
    rows = [
        _fetch_row(fetch_report),
        _row(
            "/attempts", "The bank's own feed", _esc("The bank's feed could not be read just now.")
        )
        if unread_feed
        else _feed_row(feed),
        _connections_row(store, answered, now=moment),
        _row(
            "/import",
            "Import an export file",
            "A bank's CSV or QIF export. The preview checks it against the account before "
            "anything is stored.",
        ),
        _row(
            "/statement-shape",
            "Upload a statement",
            "A PDF statement. It is kept as evidence and its layout shown with every value "
            "masked, then you say whose it is.",
        ),
        _statements_row(kept_statements),
        _row(
            "/coverage-timeline",
            page_name("/coverage-timeline"),
            "Where each source reaches, day by day, and where to fetch next.",
        ),
    ]
    shows_instant = bool(answered) or (feed is not None and feed.answered is not None)
    body = (
        '<p class="lede">Every way data reaches obdi, and where each stands.</p>'
        + _connected_line(feed, _count_connections(store))
        + _rows(rows)
        + (f'<p class="muted hub-age">{_esc(UTC_NOTE)}</p>' if shows_instant else "")
    )
    return render_page(page_name("/bring-in"), body, wide=True, body_class="hub-page")


# ------------------------------------------------------------------------------------ Diagnostics


#: (route, name, what it is for). The pages that explain a fault, in the order a fault is
#: followed: what arrived, what was asked, when.
EVIDENCE_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        "/artefacts",
        "Raw artefacts",
        "Every payload a provider sent, newest first. Everything else derives from them.",
    ),
    (
        "/attempts",
        "Fetch attempts",
        "What was asked of each provider and what it answered, refusals included.",
    ),
    (
        "/fetch-timeline",
        "Fetch timeline",
        "Every ask drawn as a bar over the history it asked about.",
    ),
    (
        "/spaces",
        "Historical Spaces",
        "Starling Spaces that once moved money and are no longer listed by the bank.",
    ),
)


def _statistics_rows(
    held: Callable[[], list[str]] | None, names: Callable[[], AccountsShown] | None
) -> str:
    """Field statistics for every account that holds rows, one link each, folded."""
    if held is None:
        return ""
    try:
        refs = sorted(held())
    except Exception:
        return _unread("The accounts")
    named = AccountsShown()
    if names is not None:
        try:
            named = names()
        except Exception:
            named = AccountsShown()
    if not refs:
        return ""
    items = "".join(
        f'<li><a class="tap" href="/account?ref={quote(ref, safe="")}">'
        f"{named.of(ref).as_name()}</a></li>"
        for ref in refs
    )
    return (
        f'<details class="diag-accounts"><summary>Field statistics for each of {len(refs)} '
        f'accounts</summary><ul class="hub-list">{items}</ul></details>'
    )


def _repairs(rebuild_available: bool, forget_available: bool) -> str:
    """The two administrative repairs, set apart, each behind its own confirmation."""
    if not (rebuild_available or forget_available):
        return ""
    confirm = (
        '<label class="tick"><input type="checkbox" name="confirm" value="yes" required> '
        "<span>I understand</span></label>"
    )
    forms = []
    if rebuild_available:
        forms.append(
            '<form class="diag-repair" method="post" action="/rebuild-derived">'
            "<h3>Rebuild from raw</h3>"
            "<p>Wipes the derived transactions and replays every raw artefact through the "
            "current rules; the raw artefacts, your categories, and your review decisions "
            "are kept.</p>"
            + confirm
            + '<button class="button danger" type="submit">Rebuild from raw</button></form>'
        )
    if forget_available:
        forms.append(
            '<form class="diag-repair" method="post" action="/forget-actual-bindings">'
            "<h3>Forget Actual account links</h3>"
            "<p>Forgets which Actual account each account maps to; the next push creates them "
            "again by name, reusing any that still exist.</p>"
            + confirm
            + '<button class="button danger" type="submit">Forget Actual account links</button>'
            "</form>"
        )
    return (
        '<section class="diag-danger" aria-labelledby="repairs"><h2 id="repairs">Repairs</h2>'
        '<p class="muted">Each asks you to confirm before it does anything.</p>'
        + "".join(forms)
        + "</section>"
    )


def render_diagnostics(
    *,
    rebuild_available: bool = False,
    forget_available: bool = False,
    rebuild_status: Callable[[], dict[str, object]] | None = None,
    rebuild_busy_note: Callable[[], str | None] | None = None,
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None = None,
    starling_probe_available: bool = False,
    probe_suggestions: Callable[[], list[object]] | None = None,
    held_accounts: Callable[[], list[str]] | None = None,
    account_names: Callable[[], AccountsShown] | None = None,
) -> bytes:
    from . import web

    evidence = _rows(
        [_row(route, name, _esc(what)) for route, name, what in EVIDENCE_ROWS]
    )
    history = web._rebuild_history_html(recent_rebuilds) if rebuild_available else ""
    probe = web._probe_section_html(starling_probe_available, probe_suggestions)
    body = (
        web._rebuild_running_banner(rebuild_status, rebuild_busy_note)
        + '<p class="lede">These pages exist to explain a fault. Nothing here is needed while '
        "Today and Checks say all is well.</p>"
        + _formerly("/diagnostics")
        + evidence
        + _statistics_rows(held_accounts, account_names)
        + _repairs(rebuild_available, forget_available)
        + (web._rebuild_status_line(rebuild_status) if rebuild_available else "")
        + history
        + (
            '<details class="oneoff">'
            "<summary>One-off experiment: Starling changesSince probe</summary>"
            f"{probe}</details>"
            if probe
            else ""
        )
    )
    return render_page(page_name("/diagnostics"), body, wide=True, body_class="hub-page")


# ------------------------------------------------------------------------------------ Accounts


#: The pages an account list leads on to, beside the accounts themselves.
ACCOUNT_LINKS: tuple[tuple[str, str, str], ...] = (
    ("/coverage", "Coverage by source", "what history each source holds for each account"),
    ("/gaps", "What to fetch next", "the statements and exports still to fetch, with dates"),
    ("/coverage-timeline", "Coverage timeline", "where each source reaches, by day, as a chart"),
    ("/spaces", "Spaces", "pots recovered from the bank's feed, to declare or leave"),
    ("/review", "Categorise", "payments that have no category yet"),
)


def accounts_links_html() -> str:
    """Where the accounts list leads on to, so none of these pages is reached by address alone."""
    items = "".join(
        f'<li><a class="tap" href="{_esc(href)}">{_esc(label)}</a> '
        f'<span class="muted">- {_esc(what)}</span></li>'
        for href, label, what in ACCOUNT_LINKS
    )
    return f'<h2>More about accounts</h2><ul class="hub-list">{items}</ul>'


# ------------------------------------------------------------------------------------ handler


class DestinationPages:
    """The hub routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _checks_page(self, fresh: bool) -> None:
        config, timer = self.bound_config, HookTimer()
        hook = timer.wrap("overview", config.overview)
        overview: Overview | None = None
        if hook is not None:
            try:
                overview = hook(fresh)
            except Exception:
                overview = None
        page = render_checks(
            overview, review_flags_linked=dispatcher_serves("/review-flags")
        )
        timer.report("/checks")
        self._respond(200, page)

    def _bring_in_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        sources = timer.wrap("source_connections", config.source_connections)
        names = timer.wrap("account_names", config.account_names)
        configured = config.starling_probe is not None
        fetch = timer.wrap("fetch_gaps", config.fetch_gaps)

        def feed(answered: dict[str, str]) -> BankFeed:
            return bank_feed_of(
                configured=configured,
                answered=answered.get(STARLING_CONNECTION),
                held=sources() if sources is not None else {},
                names=names() if names is not None else AccountsShown(),
            )

        page = render_bring_in(
            config.connection_store,
            last_answered=timer.wrap("connection_last_answered", config.connection_last_answered),
            kept_statements=timer.wrap("kept_statements", config.kept_statements),
            bank_feed=feed,
            fetch_report=(
                None if fetch is None else lambda: fetch(datetime.now(UTC).date())
            ),
        )
        timer.report("/bring-in")
        self._respond(200, page)

    def _diagnostics_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        page = render_diagnostics(
            rebuild_available=config.rebuild_derived is not None,
            forget_available=config.forget_actual is not None,
            rebuild_status=timer.wrap("rebuild_status", config.rebuild_status),
            rebuild_busy_note=timer.wrap("rebuild_busy_note", config.rebuild_busy_note),
            recent_rebuilds=timer.wrap("recent_rebuilds", config.recent_rebuilds),
            starling_probe_available=config.starling_probe is not None,
            probe_suggestions=timer.wrap("probe_suggestions", config.probe_suggestions),
            held_accounts=timer.wrap("held_accounts", config.held_accounts),
            account_names=timer.wrap("account_names", config.account_names),
        )
        timer.report("/diagnostics")
        self._respond(200, page)
