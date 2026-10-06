"""The Connections page's body: is everything connected, and when did each last answer?

TWO DIRECTIONS, one page. Where data goes out is the budgeting tool, in the Actual page's own
verdict; where it comes from is each bank connection and the bank's own feed, with the accounts
each feeds, when it last answered, and when its consent ends. The files a person brings in
himself are Bring in's, so they are one line and a link.

SILENCE WHEN FINE. A page where every consent has time and Actual agrees asks for nothing: no
press and no list of things to do. Each bank's own line does carry a quiet Reconnect at any time,
because renewing early keeps the connection continuous, as cycling a certificate before it
expires does. What needs him is a thing-to-do row at the top with
its one control (`web_overview.todo_row_html`, the row Today draws): a consent that is running out
or has ended, a scheduler that has failed. Actual's verdict is said where it belongs, in its own
section, in the tone the Actual page gives it, with the press that page offers beneath it.

WHAT IS KEPT BUT FOLDED. Adding a bank, renaming a connection, fetching now, extending history,
and what the pulls have learnt are controls and facts for the occasions that want them, behind one
fold that opens by itself where something in it needs a look (an account the pulls have stopped
reaching, a reconnect that drifted). The scheduler's steps and the two detail pages (the fetch
timeline and the attempts) are the evidence fold.

NO FIGURE, NO DESCRIPTION. Names, dates, counts, and states only, as every GET.
"""

from __future__ import annotations

import contextlib
import html
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, TypeVar
from urllib.parse import quote

from .account_names import AccountShown, AccountsShown
from .alerts import consent_rung
from .bank_balances import BANK_SOURCE
from .connections import Connection, ConnectionStore
from .navigation import account_address
from .overview import NOW, SOON
from .page_times import UTC_NOTE, date_with_age
from .plural import plural
from .pull import STARLING_CONNECTION
from .todo import Control, Todo
from .web_overview import actual_line, serial, todo_row_html
from .web_scheduler import SECTION_ID, scheduler_section

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import ExtendableAccount

_esc = html.escape

_Got = TypeVar("_Got")
_Fallback = TypeVar("_Fallback")

#: How many accounts a source names before the rest are counted.
_NAMED_ACCOUNTS = 4

#: A consent this near its end is a thing to do now and not soon: the last rung of the alert ladder.
_URGENT_DAYS = 3

_TIMES = f'<p class="muted">{UTC_NOTE}</p>'


@dataclass(frozen=True)
class Hooks:
    """What the page reads and which presses it offers, each None where this instance has none."""

    bank_authorisation: bool = True
    rename_available: bool = False
    starling_status: Callable[[], dict[str, object] | None] | None = None
    provider_knowledge: Callable[[], list[dict[str, object]]] | None = None
    extendables: Callable[[], list[ExtendableAccount]] | None = None
    backfill_status: Callable[[], dict[str, object]] | None = None
    fetch_now_available: bool = False
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None
    actual_status: Callable[[], list[dict[str, object]]] | None = None
    actual_queue: Callable[[], list[dict[str, object]]] | None = None
    actual_heartbeat: Callable[[], str] | None = None
    actual_configured: Callable[[], bool] | None = None
    push_available: bool = False
    audit_available: bool = False
    align_available: bool = False
    last_answered: Callable[[], dict[str, str]] | None = None
    source_connections: Callable[[], dict[tuple[str, str], list[str]]] | None = None
    account_names: Callable[[], AccountsShown] | None = None


def _read(read: Callable[[], _Got] | None, fallback: _Fallback) -> _Got | _Fallback:
    """What a hook gives, or `fallback` where there is no hook or it raised: one part of the page
    failing never takes the page with it."""
    if read is None:
        return fallback
    with contextlib.suppress(Exception):
        return read()
    return fallback


# ------------------------------------------------------------------------------------ consent


def consent_words(connection: Connection, now: datetime) -> tuple[str, str]:
    """What a connection's consent is doing, and the tone it is said in.

    The tone is the alert ladder's: `bad` once it has ended, `warn` from the first rung, `muted`
    while there is time, so a quiet page is a page where every consent has time.
    """
    expiry = connection.consent_expires_on()
    days = connection.consent_days_remaining(now=now)
    if expiry is None or days is None:
        return "no consent expiry recorded", "muted"
    if connection.consent_expired(now=now):
        return f"expired {date_with_age(expiry, now.date())}", "bad"
    ahead = (expiry - now.date()).days
    when = "today" if ahead <= 0 else f"in {plural(ahead, 'day')}"
    return f"expires {expiry.isoformat()} ({when})", "warn" if consent_rung(days) else "muted"


def _reconnect_todo(connection: Connection, now: datetime) -> Todo | None:
    """The thing to do for a consent that is running out or has ended; None while it has time."""
    words, tone = consent_words(connection, now)
    if tone == "muted":
        return None
    days = connection.consent_days_remaining(now=now)
    urgent = tone == "bad" or (days is not None and days <= _URGENT_DAYS)
    name = connection.connection_id
    return Todo(
        kind="consent",
        title=f"Renew the consent for {name}",
        account=None,
        why=f"Consent {words}",
        since=None,
        urgency=NOW if urgent else SOON,
        control=Control(f"Reconnect {name}", _reconnect_href(name)),
    )


# ------------------------------------------------------------------------------------ sources


def _day_of(raw: str | None) -> date | None:
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).date()
    return None


def _fed_by(
    held: dict[tuple[str, str], list[str]],
    names: AccountsShown,
    *,
    connection: str | None = None,
    source: str | None = None,
) -> str:
    """The accounts a source feeds, by name, as a clause; the names run out into a count."""
    refs = sorted(
        {
            account
            for (account, held_source), connections in held.items()
            if (source is None or held_source == source)
            and (connection is None or connection in connections)
        }
    )
    if not refs:
        return "Feeds no account yet"
    named = [
        f'<a href="{_esc(account_address("ledger", ref))}">{names.of(ref).as_name()}</a>'
        for ref in refs[:_NAMED_ACCOUNTS]
    ]
    if len(refs) > _NAMED_ACCOUNTS:
        named.append(f"{len(refs) - _NAMED_ACCOUNTS} more")
    return f"Feeds {serial(named)}"


def _answered(answered: dict[str, str] | None, name: str, today: date) -> str:
    """When it last answered; nothing at all where the page could not read when."""
    if answered is None:
        return ""
    day = _day_of(answered.get(name))
    return f"last answered {date_with_age(day, today)}" if day else "has never answered"


def _reconnect_href(name: str) -> str:
    """The address that renews a connection under its own name; a name that does not already
    exist would make a second connection to the same bank, and an unencoded ampersand or hash
    would truncate it."""
    return f"/connect?name={quote(name, safe='')}"


def _source_row(
    name: str, clauses: list[str], tone: str = "muted", *, reconnect: str | None = None
) -> str:
    """One source's line. `reconnect` is the connection's own name where it can be renewed at any
    time, because renewing early keeps the connection continuous and costs nothing."""
    said = "; ".join(clause for clause in clauses if clause)
    renew = (
        f'<a class="tap" href="{_esc(_reconnect_href(reconnect))}">Reconnect</a>'
        if reconnect is not None
        else ""
    )
    return (
        '<li class="source">'
        f'<p class="source-name"><strong>{name}</strong>{renew}</p>'
        f'<p class="source-state {tone}">{said}.</p></li>'
    )


def _sources_html(store: ConnectionStore, hooks: Hooks, now: datetime) -> str:
    today = now.date()
    answered = _read(hooks.last_answered, None) if hooks.last_answered is not None else None
    held = _read(hooks.source_connections, None)
    names = _read(hooks.account_names, AccountsShown())
    rows: list[str] = []
    for connection in sorted(_read(lambda: list(store), []), key=lambda c: c.connection_id):
        consent, tone = consent_words(connection, now)
        clauses = [
            _fed_by(held, names, connection=connection.connection_id) if held is not None else "",
            _answered(answered, connection.connection_id, today),
            f"consent {consent}",
        ]
        rows.append(
            _source_row(
                _esc(connection.connection_id), clauses, tone, reconnect=connection.connection_id
            )
        )
    feed = _read(hooks.starling_status, None)
    if feed:
        clauses = [
            _fed_by(held, names, source=BANK_SOURCE) if held is not None else "",
            _answered(answered, STARLING_CONNECTION, today),
            "no consent clock",
        ]
        rows.append(_source_row("The bank's own feed", clauses))
    none = "" if rows else "<p>No bank is connected through the aggregator.</p>"
    return (
        '<section class="conn-in"><h2>Where data comes from</h2>'
        f'{none}<ul class="sources">{"".join(rows)}</ul>'
        '<p class="muted conn-files">The statements and exports you bring in yourself are on '
        '<a class="tap" href="/bring-in">Bring in</a>.</p></section>'
    )


# ------------------------------------------------------------------------------------ out


def _out_html(hooks: Hooks, now: datetime) -> str:
    """The budgeting tool: the Actual page's verdict in its own words and tone, when it was last
    pushed to and audited, and the one press that page offers for the state it is in."""
    from .actual_verdict import Press, moment, newest
    from .web_actual import _press_form, current_verdict
    from .web_prune import align_section, counts_from_audit

    link = '<a class="tap" href="/actual">Open the Actual page</a>'
    if hooks.actual_status is None:
        line = actual_line(None, now)
        return (
            '<section class="conn-out"><h2>Where data goes out</h2>'
            f'<p><strong>Actual</strong>: {_esc(line.sentence)} {link}</p></section>'
        )
    read = _read(hooks.actual_status, None)

    def results() -> list[dict[str, object]]:
        if read is None:
            raise RuntimeError("the results could not be read")
        return read

    line = actual_line(
        results,
        now,
        queue=hooks.actual_queue,
        heartbeat=hooks.actual_heartbeat,
        configured=hooks.actual_configured,
    )
    verdict = current_verdict(
        results, hooks.actual_queue, hooks.actual_heartbeat, hooks.actual_configured, now
    )
    configured = _read(hooks.actual_configured, True)

    def last(kind: str) -> date | None:
        found = newest(read or [], kind, ok=True)
        stamp = moment(found.get("finished_at")) if found else None
        return stamp.date() if stamp else None

    pushed, audited = last("push"), last("audit")
    today = now.date()
    history = "; ".join(
        (
            f"Last pushed {date_with_age(pushed, today)}" if pushed else "Never pushed",
            f"last audited {date_with_age(audited, today)}" if audited else "never audited",
        )
    )
    press = ""
    if configured:
        if verdict.press is Press.PUSH and hooks.push_available:
            press = _press_form("/push-actual", "Push to Actual now", primary=True, enabled=True)
        elif verdict.press is Press.AUDIT and hooks.audit_available:
            press = _press_form("/audit-actual", "Audit Actual now", primary=True, enabled=True)
        elif verdict.press is Press.ALIGN and hooks.align_available:
            audit = newest(read or [], "audit", ok=True)
            if audit is not None:
                press = align_section(counts_from_audit(audit) or [], primary=True)
    tone = {"bad": "bad", "warn": "warn"}.get(str(verdict.tone), "")
    return (
        '<section class="conn-out"><h2>Where data goes out</h2>'
        f'<p class="out-verdict {tone}"><strong>Actual</strong>: {_esc(line.sentence)}</p>'
        f'<p class="muted">{_esc(history)}. {link}</p>{press}</section>'
    )


# ------------------------------------------------------------------------------------ the folds


def _rename_forms(store: ConnectionStore) -> str:
    out = []
    for connection in sorted(_read(lambda: list(store), []), key=lambda c: c.connection_id):
        name = _esc(connection.connection_id)
        out.append(
            f"<details><summary>Rename {name}</summary>"
            '<form method="post" action="/rename-connection">'
            f'<input type="hidden" name="old_name" value="{name}">'
            f'<label class="muted">New name<input name="new_name" value="{name}"></label>'
            '<button class="button secondary" type="submit">Rename</button></form></details>'
        )
    if not out:
        return ""
    return (
        '<p class="muted">A name is obdi\'s label, not the bank\'s, and a rename moves it '
        f'everywhere at once.</p>{"".join(out)}'
    )


def _manage_html(store: ConnectionStore, hooks: Hooks) -> str:
    from . import web

    accounts = _read(hooks.extendables, [])
    knowledge = _read(hooks.provider_knowledge, [])
    extend = web._extend_rows(lambda: accounts, fetch_now=hooks.fetch_now_available)
    learnt = web._knowledge_rows(lambda: knowledge)
    # An account the pulls have stopped reaching, and a reconnect that drifted, are the two
    # things in here that are faults. The fold opens for them and stays shut otherwise.
    needs_a_look = 'pill-bad">stale' in extend or any(
        row.get("fact") == "reconnect_drift" for row in knowledge
    )
    fetch = (
        web._fetch_now_rows(store, hooks.starling_status, hooks.backfill_status)
        if hooks.fetch_now_available
        else ""
    )
    return (
        f'<details class="manage"{" open" if needs_a_look else ""}>'
        "<summary>Add a bank, rename, fetch now, extend history</summary>"
        f"{web._add_a_bank_section(hooks.bank_authorisation)}"
        f"{_rename_forms(store) if hooks.rename_available else ''}"
        f"{fetch}{extend}{learnt}</details>"
    )


def _scheduler_say(hooks: Hooks, now: datetime) -> tuple[str, bool]:
    """The scheduler's one sentence and whether it warrants a look, from its own status."""
    from .scheduler_status import read_scheduler, strip_sentence

    record = _read(hooks.scheduler_heartbeat, None)
    if record is None:
        return "No scheduled cycle recorded", False
    sentence, warn = strip_sentence(read_scheduler(record, now))
    return sentence[:1].upper() + sentence[1:], warn


def _evidence_html(hooks: Hooks, now: datetime, warn_sentence: tuple[str, bool]) -> str:
    sentence, warn = warn_sentence
    return (
        f'<details class="evidence"{" open" if warn else ""}>'
        f"<summary>{_esc(sentence)}</summary>"
        f"{scheduler_section(hooks.scheduler_heartbeat, now)}"
        '<ul class="keylist">'
        '<li><a class="tap" href="/fetch-timeline">The fetch timeline</a> - every ask drawn as a '
        "bar over the days it asked about.</li>"
        '<li><a class="tap" href="/attempts">The fetch attempts</a> - every request made to a '
        "bank, and what came back.</li></ul>"
        f"{_TIMES}</details>"
    )


# ------------------------------------------------------------------------------------ the page


def connections_body(store: ConnectionStore, hooks: Hooks, now: datetime) -> str:
    """Everything between the heading and the foot of the Connections page."""
    from . import web

    banners = web._credential_banner(hooks.bank_authorisation) + web._backfill_running_banner(
        hooks.backfill_status
    )
    todos = [
        todo
        for connection in sorted(_read(lambda: list(store), []), key=lambda c: c.connection_id)
        if (todo := _reconnect_todo(connection, now)) is not None
    ]
    scheduler = _scheduler_say(hooks, now)
    if scheduler[1]:
        todos.append(
            Todo(
                kind="scheduler",
                title="Look at the scheduled fetch",
                account=None,
                why=scheduler[0],
                since=None,
                urgency=SOON,
                control=Control("See the scheduler", f"#{SECTION_ID}"),
            )
        )
    todos.sort(key=lambda todo: todo.urgency)
    rows = "".join(
        todo_row_html(todo, AccountShown, now.date(), lead=index == 0, named=False)
        for index, todo in enumerate(todos)
    )
    need = (
        f'<section class="conn-need" aria-label="What needs you">{banners}'
        + (f'<ul class="todos">{rows}</ul>' if rows else "")
        + "</section>"
        if todos or banners
        else ""
    )
    return (
        f"{need}{_out_html(hooks, now)}{_sources_html(store, hooks, now)}"
        f"{_manage_html(store, hooks)}{_evidence_html(hooks, now, scheduler)}"
    )
