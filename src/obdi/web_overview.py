"""Today's markup: a verdict, one muted line of evidence, what to do, then the accounts.

THE PAGE IS SILENT WHEN THINGS ARE FINE. The first screen holds the verdict, one muted line that
says the checks ran and opens to the detail, and the first thing to do with its control. A day
with nothing to do says so in one line and stops. Reassurance is not removed, it is folded: the
evidence line names how many checks ran, so an empty list is never mistakable for checks that
never ran, and what was looked at, when, and what the machinery is doing is one tap away.

TWO MODELS FEED IT, and this module only draws them. `todo` gathers everything waiting for the
owner into one list of things to do, each with its one control. `trust` works out, for each
account, the stretches of its history on one shared scale and the one sentence that says how far
it can be trusted; `trust_bar` draws them. Nothing here decides a verdict, a calculation, or a
route.

NO FIGURES. This is served by GET, and no GET shows a monetary value. The account rows hold
counts and dates, and `Overview` carries nothing else.

EVERY TIME PRINTED IS UTC, and the evidence fold says so once (`TIMES_NOTE`), not on each time.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from urllib.parse import quote

from .account_names import AccountShown
from .agreement import Standing
from .fetch_gaps import FetchReport
from .overview import (
    ALERT_CONDITIONS,
    ARCHIVED,
    EMPTY,
    HOUSEKEEPING,
    NEVER_ASKED,
    NOW,
    OVERVIEW_CACHE_SECONDS,
    OVERVIEW_CHECKS,
    QUIET,
    REBUILDING,
    SILENT,
    SOON,
    AccountOverview,
    Overview,
)
from .page_times import date_with_age, range_text, span_phrase
from .plural import plural
from .rebuild_hold import RebuildInProgress
from .standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    not_adding_up_sentence,
    verification_of,
)
from .todo import Todo, build_todos, grouped, lockable, wanted_days
from .trust import Trust, trust_of, window_start
from .trust_bar import axis_html, bar_html, ends_html, key_html, own_life

_esc = html.escape

#: Said once, in the evidence fold, so that no time on the page carries a bare "Z".
TIMES_NOTE = "All times are UTC."

#: How many things to do are open before the when-convenient ones fold behind a count. What
#: needs the owner now or soon is never folded.
OPEN_TODO_LIMIT = 3

_SEVERITY_CLASS = {NOW: "now", SOON: "soon", HOUSEKEEPING: ""}


def serial(parts: Sequence[str]) -> str:
    """Parts as prose, with the serial comma from three on."""
    if len(parts) <= 2:
        return " and ".join(parts)
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _clock(moment: datetime, now: datetime) -> str:
    """A time of day, with the date where it is not today's. Always UTC; `TIMES_NOTE` says so."""
    moment = moment.astimezone(UTC)
    if moment.date() == now.astimezone(UTC).date():
        return moment.strftime("%H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


def _age(moment: datetime, now: datetime) -> str:
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes ago"
    return f"{round(seconds / 3600)} hours ago"


# ------------------------------------------------------------------------------------ Verdict


def band_phrase(severity: int, count: int) -> str:
    """What a band says of its count: the one wording the verdict and the fold use."""
    if severity == NOW:
        return f"{plural(count, 'fault')} to look at now"
    if severity == SOON:
        return f"{plural(count, 'thing')} to look at soon"
    return f"{plural(count, 'thing')} when convenient"


@dataclass(frozen=True)
class Verdict:
    sentence: str
    #: "ok" (teal), "warn" (amber), or "bad" (red): the mark's colour and glyph.
    tone: str


def verdict_of(counts: Mapping[int, int], *, not_adding_up: int = 0) -> Verdict:
    """The one sentence, true of the counts beneath it.

    Nothing at all wrong is a positive statement. Faults lead with themselves. Where there are
    none the sentence says so before it counts what is left, so that "4 things when convenient"
    is never read as 4 faults.

    `not_adding_up` is the count of accounts that do not add up. Such an account is a thing to do
    only once it has lagged `overview.STALE_AGREEMENT_DAYS`, so until then there can be no item at
    all while the account's own row says it does not add up; the positive statement would
    contradict the row, and the sentence says the count instead.
    """
    parts = [
        band_phrase(severity, counts[severity])
        for severity in (NOW, SOON, HOUSEKEEPING)
        if counts.get(severity)
    ]
    if not parts and not_adding_up:
        return Verdict(f"No faults. {not_adding_up_sentence(not_adding_up)}", "warn")
    if not parts:
        return Verdict("Everything checked is in order.", "ok")
    if counts.get(NOW):
        return Verdict(f"{serial(parts)}.", "bad")
    return Verdict(f"No faults. {serial(parts)}.", "warn" if counts.get(SOON) else "ok")


def _counts(todos: Iterable[Todo]) -> dict[int, int]:
    counts = {NOW: 0, SOON: 0, HOUSEKEEPING: 0}
    for todo in todos:
        counts[todo.urgency] += 1
    return counts


def _verdict_html(verdict: Verdict, lede: str = "") -> str:
    long = " long" if len(verdict.sentence) > 80 else ""
    return (
        f'<p class="verdict {verdict.tone}{long}" id="verdict">'
        f"<span>{_esc(verdict.sentence)}</span></p>"
        + (f'<p class="verdict-lede bad">{_esc(lede)}</p>' if lede else "")
    )


# ------------------------------------------------------------------- Lines read by the evidence


@dataclass(frozen=True)
class StatusLine:
    label: str
    href: str
    #: The state in a word, and the chip class that gives it its colour and glyph.
    word: str
    css: str
    sentence: str


def _paused(label: str, href: str) -> StatusLine:
    return StatusLine(label, href, "paused", "pill-warn", "Paused while the rebuild runs.")


def data_line(
    overview: Overview | None,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None,
    now: datetime,
) -> StatusLine:
    """How fresh the feeds are: when the last scheduled cycle finished, and how many feeds are
    stale or silent, from the checks the page already ran."""
    href = "/connections"
    if overview is None:
        return StatusLine("Data", href, "unchecked", "pill-warn", "Nothing was checked.")
    if overview.rebuilding is not None:
        return _paused("Data", href)
    from .scheduler_status import read_scheduler

    finished = None
    if scheduler_heartbeat is not None:
        try:
            finished = read_scheduler(scheduler_heartbeat() or {}, now).last_completed
        except Exception:
            finished = None
    cycle = (
        f"Last scheduled cycle finished {_clock(finished, now)}"
        if finished is not None
        else "No scheduled cycle recorded"
    )
    silent = sum(1 for item in overview.attention if item.kind == "silent-feed")
    stale = sum(1 for item in overview.attention if item.kind == "stale-feed")
    if silent or stale:
        said = serial(
            [
                *([f"{plural(silent, 'feed')} silent"] if silent else []),
                *([f"{stale} stale"] if stale else []),
            ]
        )
        return StatusLine(
            "Data", href, "silent" if silent else "stale", "pill-bad" if silent else "pill-warn",
            f"{cycle}; {said}.",
        )
    if finished is None:
        return StatusLine("Data", href, "unknown","pill-warn", f"{cycle}.")
    return StatusLine("Data", href, "current", "pill-ok", f"{cycle}; no feed is stale or silent.")


def _is_counted(account: AccountOverview) -> bool:
    return account.state not in (ARCHIVED, EMPTY)


def verification_counts(accounts: Iterable[AccountOverview]) -> tuple[int, int, int, int]:
    """(counted, adding up, not adding up, nothing to check against) over the live accounts.

    An archived account and one holding no rows are not counted: nothing is expected of them.
    The count is the standing's own state, so this and each row say one thing.
    """
    counted = adding = failing = nothing = 0
    for account in accounts:
        if not _is_counted(account):
            continue
        counted += 1
        verdict = verification_of(account.standing)
        if verdict == ADDS_UP:
            adding += 1
        elif verdict == DOES_NOT_ADD_UP:
            failing += 1
        else:
            nothing += 1
    return counted, adding, failing, nothing


def actual_line(
    actual_status: Callable[[], list[dict[str, object]]] | None,
    now: datetime,
    *,
    queue: Callable[[], list[dict[str, object]]] | None = None,
    heartbeat: Callable[[], str] | None = None,
    configured: Callable[[], bool] | None = None,
) -> StatusLine:
    """Whether Actual agrees with obdi, in the Actual page's own verdict.

    THE VERDICT IS WORKED OUT ONCE, by `web_actual.current_verdict`, and this line says it:
    the headline and its evidence are that page's words, and only the chip's short word is
    chosen here. A second reading of the results on this page said "push failed" of a push
    that a later audit had already read past, where the Actual page said they agree.
    """
    from .web_actual import current_verdict

    href = "/actual"
    if actual_status is None:
        return StatusLine("Actual", href, "not wired", "pill-quiet", "Not wired on this instance.")
    verdict = current_verdict(actual_status, queue, heartbeat, configured, now)
    word, css = _ACTUAL_CHIPS.get(verdict.state.value, ("unknown", "pill-quiet"))
    sentence = f"{verdict.headline}. {verdict.detail}".strip()
    return StatusLine("Actual", href, word, css, sentence)


#: The chip for each state of the Actual page's verdict (`actual_verdict.State`): its short
#: word and the meaning of its colour. A state missing here reads "unknown", which a test
#: forbids for every state the verdict can be in.
_ACTUAL_CHIPS = {
    "agrees": ("agrees with obdi", "pill-ok"),
    "differs": ("differs", "pill-bad"),
    "unchecked": ("not checked", "pill-warn"),
    "nothing-pushed": ("no push", "pill-warn"),
    "push-failed": ("push failed", "pill-bad"),
    "audit-failed": ("audit failed", "pill-bad"),
    "align-stopped": ("stopped", "pill-bad"),
    "request-running": ("working", "pill-quiet"),
    "request-waiting": ("waiting", "pill-quiet"),
    "applier-silent": ("applier silent", "pill-bad"),
    "not-configured": ("not configured", "pill-quiet"),
    "unreadable": ("unreadable", "pill-bad"),
}


# ---------------------------------------------------------------------------------- Things to do


def _age_words(day: date, today: date) -> tuple[str, str]:
    """A date, and how long ago where that is worth saying: ("2026-08-10", "(8 weeks ago)")."""
    head, _, tail = date_with_age(day, today).partition(" (")
    return head, f"({tail}" if tail else ""


def _names_html(todo: Todo, shown: Callable[[str], AccountShown]) -> str:
    refs = (todo.account,) if todo.account is not None else todo.accounts
    return serial([shown(ref).as_name() for ref in refs])


_ISO_DAY = re.compile(r"\d{4}-\d\d-\d\d")


def _whole_dates(escaped: str) -> str:
    """Each date in already-escaped text set so that a line never breaks inside it, at a hyphen."""
    return _ISO_DAY.sub(lambda found: f'<span class="nowrap">{found.group(0)}</span>', escaped)


def todo_row_html(
    todo: Todo,
    shown: Callable[[str], AccountShown],
    today: date,
    *,
    lead: bool,
    named: bool = True,
    control: str | None = None,
    extra: str = "",
    css: str = "",
) -> str:
    """One thing to do as a row, which Today and an account's page both draw.

    `named` is False on the account's own page, where the account is the page. `control` replaces
    the button that follows the to-do's own control (an account's page puts a form there), and
    `extra` is markup after it inside the row.
    """
    parts = []
    names = _names_html(todo, shown) if named else ""
    if names:
        parts.append(f"<b>{names}</b>")
    # A statement wanted says its days and when it fell due; anything else says its one short
    # reason and, where it is old, how old. The long reason is on the account's page and on What
    # to fetch next.
    days = todo.days if todo.kind == "fetch-newer-statement" else None
    if days is not None:
        parts.append(
            f"{days[0].isoformat()} to {days[1].isoformat()} "
            f"{span_phrase(days[0], days[1], todo.files)}"
        )
    else:
        parts.append(_esc(todo.why.rstrip(".")))
    if todo.since is not None:
        day, age = _age_words(todo.since, today)
        aged = f' <span class="age">{_esc(age)}</span>' if age else ""
        if days is not None:
            parts.append(f"due since {day}{aged}")
        else:
            parts[-1] += aged
    classed = ("todo", _SEVERITY_CLASS[todo.urgency], "guess" if todo.guess else "", css)
    classes = " ".join(part for part in classed if part)
    button = "button" if lead else "button secondary"
    if control is None:
        control = (
            f'<a class="{button}" href="{_esc(todo.control.href)}">{_esc(todo.control.label)}</a>'
        )
    # A title that names its days gets their length after it, except a newer statement's, whose
    # days and length are on the line beneath.
    length = (
        f" {span_phrase(todo.days[0], todo.days[1])}"
        if todo.days is not None
        and todo.kind != "fetch-newer-statement"
        and todo.title.endswith(range_text(*todo.days))
        else ""
    )
    return (
        f'<li class="{classes}"><div class="todo-text">'
        f'<p class="todo-what">{_whole_dates(_esc(todo.title))}{length}</p>'
        f'<p class="todo-why">{_whole_dates(" &middot; ".join(parts))}</p></div>'
        f"{control}{extra}</li>"
    )


def _todo_html(todo: Todo, shown: Callable[[str], AccountShown], today: date, *, lead: bool) -> str:
    return todo_row_html(todo, shown, today, lead=lead)


def todos_html(todos: Sequence[Todo], shown: Callable[[str], AccountShown], today: date) -> str:
    """The things to do, most urgent first. What is urgent is always open; the rest are open up to
    `OPEN_TODO_LIMIT` in all, and the others fold behind a count."""
    if not todos:
        return ""
    urgent = [t for t in todos if t.urgency != HOUSEKEEPING]
    easy = [t for t in todos if t.urgency == HOUSEKEEPING]
    room = max(0, OPEN_TODO_LIMIT - len(urgent))
    opened, folded = [*urgent, *easy[:room]], easy[room:]
    rows = "".join(
        _todo_html(todo, shown, today, lead=index == 0) for index, todo in enumerate(opened)
    )
    out = f'<h2 class="visually-hidden">To do</h2><ul class="todos">{rows}</ul>'
    if folded:
        more = "".join(_todo_html(todo, shown, today, lead=False) for todo in folded)
        out += (
            f"<details><summary>{len(folded)} more when convenient</summary>"
            f'<ul class="todos">{more}</ul></details>'
        )
    return out


def _lock_line(
    accounts: Sequence[AccountOverview], shown: Callable[[str], AccountShown]
) -> str:
    """The one quiet line that some accounts have days that add up and are not locked in. Locking
    in is done on an account's page, with its transactions in view, so the line only leads there."""
    offered = [a for a in accounts if a.state != ARCHIVED and lockable(a.standing)]
    if not offered:
        return ""
    if len(offered) == 1:
        only = offered[0]
        target = f"/ledger?ref={quote(only.ref, safe='')}"
        say = (
            f"{shown(only.ref).as_name()} has days that add up and are not locked in. "
            f'<a class="tap" href="{_esc(target)}">Go to its page</a>'
        )
    else:
        say = (
            f"{plural(len(offered), 'account')} have days that add up and are not locked in. "
            '<a class="tap" href="/accounts">Go to the accounts</a>'
        )
    return f'<p class="muted lockline">{say}</p>'


# ------------------------------------------------------------------------------------ Accounts


@dataclass(frozen=True)
class RowReading:
    """Where an account sorts: the one thing of its old chip and clause still used."""

    word: str
    css: str
    clause: str
    #: 0 does not add up, 1 nothing to check against, 2 adds up, 3 quiet or empty, 4 archived.
    group: int


def row_reading(account: AccountOverview) -> RowReading:
    """The state chip and the one short clause, from the standing the page already holds."""
    from .agreement import AGREES, NONE, UNTESTED

    if account.state == ARCHIVED:
        since = f"archived since {account.closed.isoformat()}" if account.closed else "archived"
        return RowReading("archived", "pill-quiet", since, 4)
    if account.state == REBUILDING:
        return RowReading("paused", "pill-warn", "paused while the rebuild runs", 2)
    standing = account.standing
    if account.state == EMPTY:
        return RowReading("empty", "pill-quiet", "declared, no transactions held", 3)
    feed = {SILENT: "; feed silent", NEVER_ASKED: "; provider never asked"}.get(account.state, "")
    if standing is None:
        return RowReading(
            NOTHING_TO_CHECK_AGAINST, "pill-warn", f"known balances not read{feed}", 1
        )
    own = standing.standing.own
    if standing.protection_broken:
        return RowReading("protection broken", "pill-bad", f"protected period has changed{feed}", 0)
    if own.held is not None:
        since = own.held.day.isoformat()
        return RowReading(
            DOES_NOT_ADD_UP, "pill-warn", f"stops adding up at {since}{feed}", 0
        )
    if own.state == NONE:
        return RowReading(NOTHING_TO_CHECK_AGAINST, "pill-warn", f"no known balance{feed}", 1)
    if own.state == UNTESTED or own.through is None:
        return RowReading(
            NOTHING_TO_CHECK_AGAINST,
            "pill-warn",
            f"only one known balance, so the transactions cannot be checked yet{feed}",
            1,
        )
    quiet = 3 if account.state == QUIET else 2
    through = own.through.isoformat()
    adds_up = f"every known balance up to {through}"
    if own.state == AGREES and standing.protected_through is not None:
        return RowReading(
            "protected",
            "pill-ok",
            f"{adds_up}; protected through {standing.protected_through.isoformat()}{feed}",
            quiet,
        )
    return RowReading(ADDS_UP, "pill-ok", f"{adds_up}{feed}", quiet)


def arrange(
    accounts: Sequence[AccountOverview],
) -> list[tuple[AccountOverview, list[AccountOverview]]]:
    """Top-level accounts with their Spaces beneath, the worst-off family first.

    A Space sits under its parent where the parent is on the page; one whose parent is not is a
    top-level account. A family sorts by its worst member, so a held-back Space lifts its family.
    """
    refs = {account.ref for account in accounts}
    children: dict[str, list[AccountOverview]] = {}
    top: list[AccountOverview] = []
    for account in accounts:
        if account.parent is not None and account.parent in refs and account.parent != account.ref:
            children.setdefault(account.parent, []).append(account)
        else:
            top.append(account)

    def key_of(account: AccountOverview) -> tuple[int, str, str]:
        return (row_reading(account).group, account.label.lower(), account.ref)

    families = []
    for parent in top:
        spaces = sorted(children.get(parent.ref, []), key=key_of)
        live = [row_reading(s).group for s in spaces if s.state != ARCHIVED]
        worst = min([row_reading(parent).group, *live]) if parent.state != ARCHIVED else 4
        families.append((worst, parent, spaces))
    families.sort(key=lambda f: (f[0], f[1].label.lower(), f[1].ref))
    return [(parent, spaces) for _, parent, spaces in families]


def _trust_of(
    account: AccountOverview, wanted: Mapping[str, list[tuple[date, date]]], today: date
) -> Trust:
    return trust_of(
        first=account.first,
        newest=account.newest,
        standing=account.standing,
        wanted=wanted.get(account.ref, ()),
        today=today,
        closed=account.closed,
    )


def _family_trust(parent: AccountOverview, today: date) -> Trust:
    """The stretches of the family a Space is tested with: nothing is locked in or wanted for the
    Space itself, so only what the family's agreement establishes is drawn."""
    standing = parent.standing
    if standing is not None and standing.standing.whole is not None:
        standing = replace(
            standing, standing=Standing(standing.standing.whole, None), protected_through=None,
            protection_broken=False,
        )
    elif standing is not None:
        standing = replace(standing, protected_through=None, protection_broken=False)
    return trust_of(
        first=parent.first, newest=parent.newest, standing=standing, wanted=(), today=today
    )


def _flag_html(todo: Todo | None, today: date) -> str:
    """What the account is waiting for, with its age: the one slot that is empty when the account
    asks nothing."""
    if todo is None or not todo.waiting:
        return ""
    said = todo.waiting
    if todo.since is not None:
        _, age = _age_words(todo.since, today)
        if age:
            said += f" {age}"
    bad = " bad" if todo.urgency == NOW else ""
    return f'<span class="a-flag{bad}">{_esc(said)}</span>'


def _row_html(
    account: AccountOverview,
    shown: Callable[[str], AccountShown],
    first_todo: Mapping[str, Todo],
    wanted: Mapping[str, list[tuple[date, date]]],
    today: date,
    *,
    space: bool,
    by_ref: Mapping[str, AccountOverview],
    archived: bool = False,
) -> str:
    """One account's row. An archived one is the same row, over its own life where it closed
    before the shared twelve months begin (`trust_bar.own_life`), with its two end dates under
    the bar, and says when it was archived first."""
    target = _esc(quote(account.ref, safe=""))
    name = shown(account.ref).as_name()
    # A rebuild marks every account that is not archived, so an account that holds nothing is
    # told by its rows, not its state.
    holds_nothing = account.state == EMPTY or (account.state == REBUILDING and account.rows == 0)
    if account.balance_only and holds_nothing:
        # Declared to be tracked by balances stated by hand: holding no transactions is its
        # design. The date of the last one stated is not carried on the overview, so none is said.
        flag, bar, said = "", '<span class="bar" aria-hidden="true"></span>', (
            "Its balance is stated by hand."
        )
    elif account.state == REBUILDING and not holds_nothing:
        # `_accounts_html` says once, above the list, that the checks are paused.
        flag, bar, said = "", '<span class="bar" aria-hidden="true"></span>', ""
    else:
        trust = _trust_of(account, wanted, today)
        flag = _flag_html(first_todo.get(account.ref), today)
        said = trust.short
        parent = by_ref.get(account.parent) if account.parent is not None else None
        if parent is not None and said.startswith(NOTHING_TO_CHECK_AGAINST.capitalize()):
            said = f"A Space of {shown(parent.ref).name}, tested with it and not on its own."
            # What tests a Space is its family, so its bar is the family's stretches: the
            # parent's whole-family agreement where there is one, else the parent's own.
            trust = _family_trust(parent, today)
        span = (
            own_life(trust, account.closed, today, opened=account.opened)
            if archived and account.closed
            else None
        )
        bar = bar_html(trust, today, span)
        if span is not None:
            bar = f'<span class="a-bars">{bar}{ends_html(span, "a-ends")}</span>'
    if archived:
        when = f"Archived {account.closed.isoformat()}." if account.closed else "Archived."
        said = f"{when} {said}".strip()
    return (
        f'<li{" class=space" if space else ""}>'
        f'<a class="tap arow" href="/ledger?ref={target}"><span class="a-name">{name}</span>'
        f'{flag}{bar}<span class="a-trust">{_esc(said)}</span></a></li>'
    )


def _archived_html(
    archived: Sequence[AccountOverview],
    shown: Callable[[str], AccountShown],
    wanted: Mapping[str, list[tuple[date, date]]],
    today: date,
    by_ref: Mapping[str, AccountOverview],
) -> str:
    """The archived accounts folded, each as a live account's row; the shared scale's month names
    are printed once above them where any of them is drawn on it. Closed by default: nothing is
    asked of an archived account."""
    if not archived:
        return ""
    rows = "".join(
        _row_html(a, shown, {}, wanted, today, space=False, by_ref=by_ref, archived=True)
        for a in archived
    )
    shared = any(a.closed is None or a.closed >= window_start(today) for a in archived)
    axis = axis_html(today) if shared else ""
    return (
        f"<details><summary>{plural(len(archived), 'archived account')}</summary>"
        f'{axis}<ul class="alist">{rows}</ul></details>'
    )


#: The way to the accounts page, which has no tab of its own: Today's list is where it is reached.
_MANAGE_ACCOUNTS = (
    '<p class="muted"><a class="tap" href="/accounts">Rename, archive, or declare accounts</a></p>'
)


def _accounts_html(
    overview: Overview,
    todos: Sequence[Todo],
    wanted: Mapping[str, list[tuple[date, date]]],
    shown: Callable[[str], AccountShown],
) -> str:
    today = overview.generated_at.date()
    manage = _MANAGE_ACCOUNTS
    if not overview.accounts:
        return f"<p>No account is held or declared yet.</p>{manage}"
    by_ref = {a.ref: a for a in overview.accounts}
    first_todo: dict[str, Todo] = {}
    for todo in todos:
        if todo.account is not None:
            first_todo.setdefault(todo.account, todo)
    rows = []
    archived: list[AccountOverview] = []
    for parent, spaces in arrange(overview.accounts):
        if parent.state == ARCHIVED:
            archived.append(parent)
        else:
            rows.append(
                _row_html(parent, shown, first_todo, wanted, today, space=False, by_ref=by_ref)
            )
        for space in spaces:
            if space.state == ARCHIVED:
                archived.append(space)
            else:
                rows.append(
                    _row_html(space, shown, first_todo, wanted, today, space=True, by_ref=by_ref)
                )
    paused = (
        '<p class="muted paused">'
        "The checks on these accounts are paused while the rebuild runs.</p>"
        if overview.rebuilding is not None
        else ""
    )
    live = (
        f'{paused}{axis_html(today)}<ul class="alist">{"".join(rows)}</ul>' if rows else ""
    )
    return (
        live
        + '<div class="p-more">'
        f"<details><summary>What the bars show</summary>{key_html()}</details>"
        f"{_archived_html(archived, shown, wanted, today, by_ref)}{manage}</div>"
    )


# ------------------------------------------------------------------------------------ Evidence


def _evidence_html(
    overview: Overview,
    lines: Sequence[StatusLine],
    system_html: str,
    fetch_unread: bool,
    now: datetime,
) -> str:
    """The reassurance, kept and muted: how many checks ran and when, what was looked at, and what
    the machinery is doing. Its summary is never removable: it is what stops silence being
    mistaken for checks that did not run."""
    names = ", ".join((*ALERT_CONDITIONS, *OVERVIEW_CHECKS))
    at = overview.generated_at.astimezone(UTC).strftime("%H:%M")
    if overview.checks_run == overview.checks_total:
        summary = f"{overview.checks_total} checks ran at {at}"
    else:
        summary = (
            f"{overview.checks_run} of {overview.checks_total} checks ran at {at}; "
            "the rest could not run"
        )
    items = [f"<li>{_esc(line.sentence)}</li>" for line in lines]
    # A fact with nothing for a person to do is said here and nowhere else. A flagged transaction
    # is a thing to do (`todo`), and a rebuild in progress is the verdict's to say.
    items.extend(
        f"<li>{_esc(note.message)}</li>"
        for note in overview.notes
        if note.kind not in ("review", "rebuild-running")
    )
    items.append(f"<li>The checks: {_esc(names)}.</li>")
    if fetch_unread:
        items.append("<li>What is still to fetch could not be worked out just now.</li>")
    items.append(
        f"<li>Assembled {_age(overview.generated_at, now)} and reused for up to "
        f'{OVERVIEW_CACHE_SECONDS} seconds. <a class="tap" href="/?fresh=1">Check again</a></li>'
    )
    items.append(f"<li>{TIMES_NOTE}</li>")
    return (
        f'<details class="evidence"><summary>{_esc(summary)}</summary>'
        f"<ul>{''.join(items)}</ul>{system_html}</details>"
    )


def _notice(message: str) -> str:
    return (
        '<ol class="attention"><li class="now">'
        f'<p class="item-message"><span class="pill pill-bad">Checks did not run</span> '
        f"{_esc(message)}</p></li></ol>"
    )


def overview_html(
    load: Callable[[bool], Overview] | None,
    *,
    fresh: bool = False,
    now: datetime | None = None,
    actual_status: Callable[[], list[dict[str, object]]] | None = None,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None,
    system_html: str = "",
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    actual_heartbeat: Callable[[], str] | None = None,
    actual_configured: Callable[[], bool] | None = None,
    fetch: Callable[[date], FetchReport] | None = None,
) -> str:
    """Today's body.

    Neither an unwired hook nor one that raises is allowed to render as an empty list: both say
    that no checks ran, and the verdict says nothing was checked.
    """
    now = now or datetime.now(UTC)
    if load is None:
        return _unchecked(
            "This deployment has no Overview wired, so nothing was checked.", system_html
        )
    try:
        overview = load(fresh)
    except Exception as error:
        return _unchecked(
            f"The overview could not be assembled ({type(error).__name__}), so no checks "
            "ran. The web log has the error.",
            system_html,
        )
    today = overview.generated_at.date()
    report: FetchReport | None = None
    fetch_unread = False
    if fetch is not None and overview.rebuilding is None:
        try:
            report = fetch(today)
        except RebuildInProgress:
            report = None
        except Exception:
            report, fetch_unread = None, True
    accounts_shown = {
        account.ref: AccountShown.named(account.ref, account.label)
        for account in overview.accounts
    }

    def shown(ref: str) -> AccountShown:
        return accounts_shown.get(ref) or AccountShown(ref)

    todos = grouped(build_todos(overview, report, lambda ref: shown(ref).name))
    lede = ""
    if overview.rebuilding is not None:
        verdict = Verdict(overview.rebuilding.sentence(), "warn")
    else:
        verdict = verdict_of(
            _counts(todos), not_adding_up=verification_counts(overview.accounts)[2]
        )
        if overview.checks_run != overview.checks_total:
            lede = (
                f"Only {overview.checks_run} of {overview.checks_total} checks could run, "
                "so this covers only those."
            )
    lines = [
        data_line(overview, scheduler_heartbeat, now),
        actual_line(
            actual_status,
            now,
            queue=actual_queue,
            heartbeat=actual_heartbeat,
            configured=actual_configured,
        ),
    ]
    wanted = wanted_days(report)
    return (
        '<div class="overview home today">'
        '<section class="home-lead" aria-label="What needs you">'
        f"{_verdict_html(verdict, lede)}"
        f"{_evidence_html(overview, lines, system_html, fetch_unread, now)}"
        f"{todos_html(todos, shown, today)}"
        f"{'' if any(t.urgency == NOW for t in todos) else _lock_line(overview.accounts, shown)}"
        "</section>"
        '<section id="accounts" class="home-accounts"><h2>Accounts</h2>'
        f"{_accounts_html(overview, todos, wanted, shown)}</section>"
        "</div>"
    )


def _unchecked(message: str, system_html: str) -> str:
    """Today when no checks ran. The machinery's facts stay: this is when they are wanted."""
    return (
        '<div class="overview home today"><section class="home-lead">'
        f"{_verdict_html(Verdict('Nothing was checked.', 'bad'))}"
        f"{_notice(message)}{system_html}</section>"
        '<section id="accounts" class="home-accounts">'
        f'<p class="muted">No account list is available.</p>{_MANAGE_ACCOUNTS}</section></div>'
    )
