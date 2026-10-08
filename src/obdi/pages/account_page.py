"""An account's page, as far as trust and the things to do go: the sentence, the strip, and the
rows of what to do, with the one control each.

READING AND DRAWING ARE SEPARATE. `read_account` gathers what the page needs from the same
sources Today reads (the overview's account, the files still to fetch, the compact coverage
timeline), so the sentence here is the one Today says. Everything else in this module draws from
that reading and holds no store, no figure, and no decision of its own: `trust` decides what is
said, `todo` what is to be done, `trust_bar` how a bar is drawn.

AN ACCOUNT THE OVERVIEW CANNOT NAME (the hook is not wired, or the account is not in it) is read
from the ledger alone: its first and newest transactions are taken as the first day of the oldest
month and the last day of the newest, which can only make a waiting stretch start a few days
late or early, and its things to do are none. The production deployment always has the overview.

NO FIGURE. A date, a count, a name: the page this is drawn into is masked on a GET.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, timedelta

from ..core.addresses import account_address
from ..core.logs import say
from ..core.plural import plural
from ..ingest.rebuild_hold import RebuildInProgress
from ..read.account_names import AccountShown, AccountsShown
from ..read.coverage_timeline import (
    ASK_HOLE,
    KIND_NAMES,
    LANE_ORDER,
    AccountTimeline,
    kind_of_source,
)
from ..read.fetch_gaps import FetchReport
from ..read.ledger import Ledger
from ..read.overview import HOUSEKEEPING, AccountOverview, Overview
from ..read.party_coverage import PartyStated, stretch_words
from ..read.todo import Todo, account_page, build_todos, grouped, wanted_days
from ..verify.protection import ProtectionView
from ..verify.standing_data import (
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    verification_of,
)
from ..verify.trust import Trust, month_marks, trust_of
from .trust_bar import bar_html, ends_html, own_life, party_lane_html, source_lane_html
from .web_accounts import submit_button
from .web_overview import OPEN_TODO_LIMIT, _whole_dates, todo_row_html

_esc = html.escape

#: The id inside the locking-in fold that a to-do about a locked stretch leads to.
LOCKING_ANCHOR = "locking"

#: Short names for the lanes of the strip, which share a narrow label column.
_LANE_NAMES = {**KIND_NAMES, "feed": "Feed", "statement": "Statements", "typed": "Typed"}

#: The strip's last lane, after the sources: where the transactions state their other party.
PARTY_LANE = "Party stated"

#: The to-dos that are the account's own answer to "what stops it adding up", so the hold the
#: standing names is not said a second time beside them.
_FAULT_KINDS = frozenset(
    {"statement-fault", "agreement-lapsed", "balance", "known-balances-disagree"}
)


@dataclass(frozen=True)
class AccountReading:
    """What an account's page says about how far it can be trusted, and what raises that."""

    trust: Trust
    standing: AccountStanding | None
    todos: tuple[Todo, ...]
    timeline: AccountTimeline | None
    #: Sentences for what could not be read just now, said on the page and never swallowed.
    unread: tuple[str, ...] = ()
    #: The day the next statement is due where Bring in holds one as expected and it has not
    #: passed (`AccountOutlook.due`): the days since the last statement cannot be tested before
    #: it, so the trust sentence says so instead of warning.
    next_due: date | None = None


def _month_start(month: str) -> date | None:
    return date(int(month[:4]), int(month[5:7]), 1) if month else None


def _month_end(month: str) -> date | None:
    start = _month_start(month)
    if start is None:
        return None
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return following - timedelta(days=1)


def closed_on(archive: object) -> date | None:
    """The day an archived account closed, from the ledger's archive note, where the note says
    the account is archived and dates it; None for any other account."""
    if archive is None or getattr(archive, "state", "") != "archived":
        return None
    try:
        return date.fromisoformat(str(getattr(archive, "closed", "")))
    except ValueError:
        return None


def read_account(config: object, ledger: Ledger, today: date) -> AccountReading:
    """The reading for the account `ledger` is of, as of `today`.

    `config` is the web configuration, asked for hooks by name so a deployment that wires fewer
    of them still renders the page. A hook that raises is said on the page and in the log: a page
    without its things to do must never look like one with nothing to do.
    """
    ref = ledger.ref
    unread: list[str] = []
    overview: Overview | None = None
    hook = getattr(config, "overview", None)
    if hook is not None:
        try:
            overview = hook(False)
        except Exception as fault:
            say("account_page.overview.fault", kind=type(fault).__name__)
            unread.append("What this account needs could not be worked out just now.")
    own = _own(overview, ref)
    report: FetchReport | None = None
    fetch = getattr(config, "fetch_gaps", None)
    if fetch is not None and overview is not None and overview.rebuilding is None:
        try:
            report = fetch(today)
        except RebuildInProgress:
            report = None
        except Exception as fault:
            say("account_page.fetch.fault", kind=type(fault).__name__)
            unread.append("The files still to fetch could not be worked out just now.")
    standing = own.standing if own is not None else _standing_of(ledger)
    first = own.first if own is not None else _month_start(ledger.oldest_month)
    newest = own.newest if own is not None else _newest_of(ledger, today)
    wanted = wanted_days(report).get(ref, [])
    trust = trust_of(
        first=first,
        newest=newest,
        standing=standing,
        wanted=wanted,
        today=today,
        closed=closed_on(ledger.archive),
    )
    todos: tuple[Todo, ...] = ()
    if overview is not None:
        everyone = grouped(build_todos(overview, report, _name_in(overview)))
        todos = tuple(t for t in everyone if t.account == ref or ref in t.accounts)
    timeline = _timeline(config, ref, today, unread)
    outlook = next((o for o in report.accounts if o.account == ref), None) if report else None
    due = outlook.due(today) if outlook is not None else None
    return AccountReading(trust, standing, todos, timeline, tuple(unread), due)


def _name_in(overview: Overview) -> Callable[[str], str]:
    shown = AccountsShown(AccountShown.named(a.ref, a.label) for a in overview.accounts)
    return lambda ref: shown.of(ref).name


def _own(overview: Overview | None, ref: str) -> AccountOverview | None:
    if overview is None:
        return None
    return next((a for a in overview.accounts if a.ref == ref), None)


def _standing_of(ledger: Ledger) -> AccountStanding | None:
    """The account's standing from its own ledger: the agreement and the lock, as Today's reading
    of the account would carry them."""
    agreement = ledger.standing
    if agreement is None:
        return None
    lock = ledger.protection
    state = lock.state if lock is not None else "none"
    through = lock.through if lock is not None and state in ("intact", "broken") else None
    return AccountStanding(agreement, through, state == "broken")


def _newest_of(ledger: Ledger, today: date) -> date | None:
    end = _month_end(ledger.newest_month)
    return None if end is None else min(end, today)


def _timeline(
    config: object, ref: str, today: date, unread: list[str]
) -> AccountTimeline | None:
    hook = getattr(config, "coverage_timeline_compact", None)
    if hook is None:
        return None
    try:
        found: AccountTimeline | None = hook(ref, today)
    except Exception as fault:
        say("account_page.timeline.fault", kind=type(fault).__name__)
        unread.append("The timeline by source could not be built just now.")
        return None
    return found


# ------------------------------------------------------------------------------------- The head


def head_html(shown: AccountShown, *, sent: bool, archived: str = "") -> str:
    """Under the name, one muted line: the reference as code where the name is not it, whether
    the account is sent to Actual, and that it is archived where it is."""
    bits = []
    if archived:
        # Plain words in, a pill out: the caller once passed the Accounts page's ready-made pill
        # and the markup was escaped onto the page as text.
        bits.append(f'<span class="pill pill-quiet">{_esc(archived)}</span>')
    if shown.labelled:
        bits.append(shown.code())
    bits.append("sent to Actual" if sent else "not sent to Actual")
    return f'<p class="meta">{" &middot; ".join(bits)}</p>'


# -------------------------------------------------------------------------- The trust sentence


def _waiting_marked(text: str, due: date | None = None) -> str:
    """The sentence escaped, with the clause that asks something of the reader set apart, since an
    old date is the one thing in a quiet line that is not quiet.

    Where the next statement is `due` and has not passed, that clause asks nothing: the days
    since the last statement cannot be tested before it is issued, so it is replaced by when it
    is due, in the ordinary colour.
    """
    phrase = f"{NOTHING_TO_CHECK_AGAINST.capitalize()} since"
    at = text.find(phrase)
    if at < 0:
        return _whole_dates(_esc(text))
    if due is not None:
        end = text.find(". ", at)
        rest = "" if end < 0 else text[end + 1 :]
        return _whole_dates(
            _esc(f"{text[:at]}Next statement due about {due.isoformat()}.{rest}")
        )
    return (
        _whole_dates(_esc(text[:at]))
        + f'<span class="age">{_whole_dates(_esc(text[at:]))}</span>'
    )


def trust_html(reading: AccountReading, *, held_transactions: int, span: tuple[str, str]) -> str:
    """The trust sentence in full: ordinary where all is well, and the one sentence that needs the
    reader set large where the transactions do not add up, a locked stretch has changed, or
    nothing is held or can test them."""
    trust, standing = reading.trust, reading.standing
    text = trust.sentence
    broken = standing is not None and standing.protection_broken
    verdict = verification_of(standing)
    if trust.nothing_held:
        return f'<p class="trust none">{_esc(text)}</p>'
    if broken:
        stop = text.find(". ") + 1 or len(text)
        head, sub = text[:stop], text[stop:].strip()
        return _headline("bad", head, sub, reading.next_due)
    if verdict == DOES_NOT_ADD_UP:
        at = text.find(DOES_NOT_ADD_UP.capitalize())
        head, sub = (text[at:], text[:at].strip()) if at >= 0 else (text, "")
        return _headline("bad", head, sub, reading.next_due)
    if trust.adds_up_to is None and trust.locked_to is None:
        held = (
            f"{plural(held_transactions, 'transaction')} held, from {span[0]} to {span[1]}."
            if held_transactions
            else ""
        )
        return _headline("none", text, held, reading.next_due)
    return f'<p class="trust">{_waiting_marked(text, reading.next_due)}</p>'


def _headline(kind: str, head: str, sub: str, due: date | None = None) -> str:
    said = f'<p class="trust {kind}">{_whole_dates(_esc(head))}'
    if sub:
        said += f'<span class="sub">{_waiting_marked(sub, due)}</span>'
    return said + "</p>"


# -------------------------------------------------------------------------------- The strip


def _own_life(
    reading: AccountReading, closed: date, today: date, opened: date | None = None
) -> tuple[date, date] | None:
    """`trust_bar.own_life` over the days this account's timeline says a source holds, from
    the day it opened where that is stated."""
    held = (
        [run.first for lane in reading.timeline.lanes for run in lane.runs]
        if reading.timeline is not None
        else []
    )
    return own_life(reading.trust, closed, today, held, opened=opened)


def strip_html(
    reading: AccountReading,
    ref: str,
    today: date,
    closed: date | None = None,
    opened: date | None = None,
) -> str:
    """The trust lane over one lane per source, on the shared twelve months, wanted files dashed.

    The small form of the coverage timeline, and a link to the full one: the whole strip is the
    link, named for a reader who cannot see it. The trust lane is `trust_bar`'s, and the source
    lanes are the lanes of the timeline's own model, merged by the way in (several statement
    readers are one lane of statements).

    An account CLOSED before the shared twelve months begin has nothing in them, so its strip is
    drawn by the same bars over its own life, from its first held day to the day it closed, with
    the two dates under it and a line saying so. One closed inside the twelve months keeps them.
    """
    span = None if closed is None else _own_life(reading, closed, today, opened)
    if span is None:
        axis = "".join(
            f'<span style="left:{left:.2f}%">{_esc(name)}</span>'
            for name, left in month_marks(today)[::2]
        )
        axis_row = f'<span></span><span class="axis" aria-hidden="true">{axis}</span>'
    else:
        axis_row = ""
    rows = (
        f'{axis_row}<span class="lane first">Trust</span>{bar_html(reading.trust, today, span)}'
    )
    timeline = reading.timeline
    if timeline is not None:
        by_kind: dict[str, list[tuple[date, date]]] = {}
        for lane in timeline.lanes:
            if lane.runs:
                by_kind.setdefault(lane.kind, []).extend((run.first, run.last) for run in lane.runs)
        for kind in (*LANE_ORDER, *sorted(set(by_kind) - set(LANE_ORDER))):
            if kind not in by_kind:
                continue
            wanted = [
                (gap.first, gap.last)
                for gap in timeline.gaps
                if gap.kind != ASK_HOLE and kind_of_source(gap.source) == kind
            ]
            name = _LANE_NAMES.get(kind, kind)
            rows += (
                f'<span class="lane">{_esc(name)}</span>'
                f"{source_lane_html(by_kind[kind], wanted, today, span)}"
            )
        party = timeline.party
        if party is not None and party.drawn:
            rows += (
                f'<span class="lane">{PARTY_LANE}</span>'
                f"{party_lane_html(party.stated_runs, party.described_runs, today, span)}"
            )
    href = account_address("timeline", ref)
    ends = said = ""
    if span is not None:
        ends = "<span></span>" + ends_html(span, "ends")
        said = (
            f'<p class="muted">Closed {span[1].isoformat()}; the bars span its whole life.</p>'
        )
    return (
        f'<a class="tap strip" href="{href}">'
        '<span class="visually-hidden">The full timeline, source by source</span>'
        f"{rows}{ends}</a>{said}"
    )


def party_notes_html(party: PartyStated | None) -> str:
    """The sentence for each stretch of months named by the description only, in the fold that
    says what the bars show; nothing for an account whose transactions state their party."""
    if party is None:
        return ""
    return "".join(
        f'<p class="muted party-note">{_esc(stretch_words(stretch, askable=party.askable))}</p>'
        for stretch in party.stretches
    )


# --------------------------------------------------------------------------- Things to do


def _local_control(todo: Todo, ref: str) -> str | None:
    """Where the to-do's control goes when the place is this very page: the section on it, so the
    press does not load the page again. None where it leads elsewhere."""
    if todo.kind == "protection-broken":
        return f"#{LOCKING_ANCHOR}"
    base = account_page(ref)
    href = todo.control.href
    if href == base or href.startswith(base + "#"):
        return href[len(base) :] or "#opening"
    return None


def _balance_form(todo: Todo, ref: str, month: str) -> str:
    """The form that confirms a balance for a day: the page's own "state a balance", worded as
    confirming. The day is the one the to-do names; the amount field is empty on every rendering."""
    day = f' value="{todo.since.isoformat()}"' if todo.since is not None else ""
    return (
        '<form class="todo-form" method="post" action="/ledger-anchor">'
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
        '<input type="hidden" name="currency" value="GBP">'
        f'<label>On this day<input type="date" name="day" required{day}></label>'
        '<label>The balance was, in pounds and pence'
        '<input name="amount" inputmode="decimal" autocomplete="off" required></label>'
        + submit_button("Confirm this balance")
        + "</form>"
    )


def _row(todo: Todo, ref: str, month: str, today: date, *, lead: bool) -> str:
    def unnamed(ref_: str) -> AccountShown:
        return AccountShown(ref_)

    # A sentence an alert wrote leads with the account's reference, which the page is about.
    todo = replace(todo, why=todo.why.removeprefix(f"{ref}: "))

    css = "lead"
    if todo.kind == "confirm-balance":
        return todo_row_html(
            todo, unnamed, today, lead=lead, named=False, control="",
            extra=_balance_form(todo, ref, month), css=css,
        )
    local = _local_control(todo, ref)
    button = "button" if lead else "button secondary"
    control = (
        f'<a class="{button}" href="{_esc(local)}">{_esc(todo.control.label)}</a>'
        if local is not None
        else None
    )
    return todo_row_html(todo, unnamed, today, lead=lead, named=False, control=control, css=css)


def hold_is_said(reading: AccountReading, hold: tuple[str, str, str] | None) -> bool:
    """Whether the standing's own sentence about what stops the account adding up is already on
    the page, as the thing to do (`todos_html`) or inside the message of the one that says it
    with that sentence: the known balances then do not say it again."""
    kinds = {t.kind for t in reading.todos}
    return (hold is not None and not kinds & _FAULT_KINDS) or "agreement-lapsed" in kinds


def hold_row_html(sentence: str, href: str, label: str) -> str:
    """What stops the account adding up, as a to-do, where no to-do already says it."""
    return (
        '<li class="todo now lead"><div class="todo-text">'
        '<p class="todo-what">Find what stops it adding up</p>'
        f'<p class="todo-why">{_whole_dates(_esc(sentence))}</p></div>'
        f'<a class="button" href="{_esc(href)}">{_esc(label)}</a></li>'
    )


def todos_html(
    reading: AccountReading,
    ref: str,
    month: str,
    today: date,
    *,
    hold: tuple[str, str, str] | None,
    lock: str,
) -> str:
    """This account's things to do, grouped as Today groups them, each with its control.

    `hold` is the standing's own explanation of what stops the account adding up (sentence, where
    to read it, what the control says), said as a to-do only where no to-do of the account already
    is one; `lock` is the offer to lock in, which is quiet and comes last.
    """
    todos = list(reading.todos)
    rows: list[str] = []
    if hold is not None and not any(t.kind in _FAULT_KINDS for t in todos):
        rows.append(hold_row_html(*hold))
    urgent = [t for t in todos if t.urgency != HOUSEKEEPING]
    easy = [t for t in todos if t.urgency == HOUSEKEEPING]
    room = max(0, OPEN_TODO_LIMIT - len(urgent) - len(rows))
    opened, folded = [*urgent, *easy[:room]], easy[room:]
    rows += [
        _row(todo, ref, month, today, lead=not rows and index == 0)
        for index, todo in enumerate(opened)
    ]
    body = "".join(rows) + lock
    out = f'<ul class="todos">{body}</ul>' if body else ""
    if folded:
        more = "".join(_row(todo, ref, month, today, lead=False) for todo in folded)
        out += (
            f"<details><summary>{len(folded)} more when convenient</summary>"
            f'<ul class="todos">{more}</ul></details>'
        )
    for sentence in reading.unread:
        out += f'<p class="warn">{_esc(sentence)}</p>'
    return out


# ------------------------------------------------------------------------------- Locking in


def _months(first: date, last: date) -> str:
    one, other = f"{first.year:04d}-{first.month:02d}", f"{last.year:04d}-{last.month:02d}"
    return one if one == other else f"{one} to {other}"


def lock_offer_html(protection: ProtectionView | None, form: str) -> str:
    """The offer to lock in, as a quiet row: how many transactions, which months, that values can
    be read first, and what locking gives. Said only where `protection` offers a day, which is
    where the account adds up further than it is locked (`protection.tested_days`); `form` is the
    one button, which asks before it acts."""
    if protection is None or not protection.offer:
        return ""
    newest = protection.offer[-1]
    count, first, last = protection.offer_rows, protection.offer_first, protection.offer_last
    covers = (
        f"{plural(count, 'transaction')}, {_months(first, last)}, would be locked in. "
        if count and first is not None and last is not None
        else ""
    )
    return (
        '<li class="todo offer lead"><div class="todo-text">'
        f'<p class="todo-what">Lock in to <span class="nowrap">{newest.isoformat()}</span></p>'
        f'<p class="todo-why">{covers}Show values to read them first. Once locked in, a '
        "change to them is reported loudly and never applied quietly.</p></div>"
        f"{form}</li>"
    )


def cannot_lock_yet_html(protection: ProtectionView | None, adds_up_to: date | None) -> str:
    """Why an account that adds up past its lock cannot be locked further yet: what tests the
    days is a statement's own listing, and a lock records a balance by date. Said only where
    `protection` has worked that out (`ProtectionView.listing_only`)."""
    if protection is None or adds_up_to is None or not protection.listing_only:
        return ""
    return (
        '<p class="next">It adds up to '
        f'<span class="nowrap">{adds_up_to.isoformat()}</span>, but only by what its statement '
        "lists, and locking in records a balance by date: it cannot be locked in yet.</p>"
    )


