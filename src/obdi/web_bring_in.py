"""Bring in: one place to put files in, and what is still wanted, account by account.

THE QUESTION is "what do I fetch and where does it go". The page answers it in the order a person
asks it: an upload target that takes statements (PDF) and exports (CSV, QIF) together, one muted
line that says every source was looked at, and then the files wanted, GROUPED BY ACCOUNT, each
account with the same sentence and bar it has on Today and the lanes for its statements and
exports beneath, so the gap can be seen where it lies.

WHAT IS WANTED is `fetch_gaps`' to say and the owner's decisions (`fetch_marks`) have already cut
from it; `bring_in` turns it into files. Nothing is decided here: no verdict, no calculation.
Each file has a quiet "Set aside" link to the form that says what the store holds for the period
before the owner confirms (`web_set_aside`), and the decisions already made are folded at the foot
with their Undo (`web_marks`).

AN UPLOAD never says which account it is for: nothing a bank prints names an account the store
knows. So a file is read in at once only where the owner scoped the upload to an account
(`/bring-in?account=`). Otherwise it is kept (a statement, before anyone decides whose it is) or
held (an export, which is previewed against its account before anything is stored), and the
results page asks for the account of that file alone, through the doors that already do the
asking: `/statement-assign` and `/upload-preview`. What a file settled is said in the trust
sentence's terms, once the standing is read again.

Every first view is masked: dates, counts, names, and words. The answer to an upload is a page
served in reply to a POST, and it shows no figure either: a refusal's digits are masked.
"""

from __future__ import annotations

import contextlib
import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import quote

from .account_names import AccountsShown, code_html
from .bring_in import (
    BALANCE_KINDS,
    UploadKind,
    WantedFile,
    by_account,
    files_wanted,
    newly_lockable,
    settled_sentence,
    upload_kind,
    wanted_heading,
)
from .callback import render_page
from .connections import Connection, ConnectionStore
from .coverage_timeline import EXPORT, STATEMENT, AccountTimeline
from .fetch_gaps import FetchReport, GapKind
from .fetch_marks import AGGREGATOR
from .fetch_reasons import gap_lines
from .namespaces import UNASSIGNED_ACCOUNT
from .overview import AccountOverview, Overview
from .page_times import UTC_NOTE, instant_of, span_phrase
from .plural import plural
from .pull import STARLING_CONNECTION
from .rebuild_hold import RebuildInProgress
from .standing_data import AccountStanding
from .todo import account_page, wanted_days
from .trust import Trust, month_marks, trust_of
from .trust_bar import bar_html, source_lane_html
from .web_marks import (
    MARKS_STYLE_TAG,
    contradicted_html,
    folded_html,
    mark_query,
    offers_html,
    scope_lines_html,
    verdict_clauses,
)
from .web_overview import _age_words, _whole_dates

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .statement_shape import ShapeReport
    from .web import WebConfig

_esc = html.escape

#: How big an export may be: bank exports are small, and a file this large is not one.
EXPORT_LIMIT = 5 * 1024 * 1024
#: How big a whole upload may be, so a batch of statements is the ordinary case and a mistake is
#: still refused before it is read.
UPLOAD_LIMIT = 200 * 1024 * 1024

_LANES = ((STATEMENT, "Statements"), (EXPORT, "Exports"))


# ------------------------------------------------------------------------------ What an upload did


class Outcome(StrEnum):
    #: Read in to the account the upload was scoped to.
    PLACED = "placed"
    #: A statement kept, waiting to be told whose it is.
    KEPT = "kept"
    #: An export held, waiting to be told which account to check it against.
    HELD = "held"
    #: A statement kept whose reading in raises a doubt the owner must answer.
    CONFIRM = "confirm"
    #: Already held, under an account.
    ALREADY = "already"
    #: Not read in, and why.
    REFUSED = "refused"


@dataclass(frozen=True)
class FileResult:
    """What became of one file."""

    filename: str
    kind: UploadKind
    outcome: Outcome
    #: The account it was read in to, or was scoped to.
    account: str = ""
    #: A kept statement's id, or an export's held token.
    artefact: int = 0
    token: str = ""
    #: Why it was refused, with every digit masked.
    note: str = ""


@dataclass(frozen=True)
class UploadResults:
    files: tuple[FileResult, ...]
    #: What the files settled, one sentence per account that was read in to.
    settled: tuple[str, ...] = ()
    #: The accounts that have days to lock in after this and had none before.
    lockable: tuple[str, ...] = ()
    #: The account chooser for a file waiting for one: markup, since one component draws it.
    picker: str = ""


# ----------------------------------------------------------------------------------- What it reads


@dataclass(frozen=True)
class Evidence:
    """The one muted line that every source was looked at, and what it opens to."""

    summary: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class BringInData:
    today: date
    #: None where what is wanted could not be worked out, with `unread` saying why.
    report: FetchReport | None
    unread: str
    names: AccountsShown
    trust: Mapping[str, Trust] = field(default_factory=dict)
    timelines: Mapping[str, AccountTimeline] = field(default_factory=dict)
    #: The banks that fed an account, as a connection names them: a secondary label.
    banks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    evidence: Evidence = Evidence("")
    #: The account an upload is scoped to, "" for any.
    scoped: str = ""
    results: UploadResults | None = None
    #: The notice an upload that could not start is answered with.
    notice: str = ""
    #: Kept statements waiting for an account, and how many are kept in all (None: unreadable).
    kept: int | None = None
    kept_waiting: int = 0

    @property
    def decided(self) -> list[str]:
        """What the owner's decisions took out of the list, for the one quiet sentence."""
        if self.report is None:
            return []
        return verdict_clauses(
            self.report.set_aside,
            self.report.out_of_scope,
            len(self.report.marks.contradicted),
        )


# ------------------------------------------------------------------------------------- Drawing


def _ref_anchor(ref: str) -> str:
    return "account-" + quote(ref, safe="")


def _target_html(data: BringInData) -> str:
    scoped = ""
    hidden = ""
    if data.scoped:
        shown = data.names.of(data.scoped)
        scoped = (
            f'<p class="bi-scoped">For {shown.as_name()}. '
            '<a class="tap" href="/bring-in">Any account</a></p>'
        )
        hidden = f'<input type="hidden" name="account" value="{_esc(data.scoped)}">'
    return (
        '<form id="upload" method="post" action="/bring-in" enctype="multipart/form-data">'
        f"{scoped}{hidden}"
        '<input type="file" name="file" aria-label="Statements and exports" multiple required '
        'accept=".pdf,.csv,.qif,application/pdf,text/csv">'
        '<button class="button" type="submit">Read these files</button>'
        "<p>Statements (PDF) and exports (CSV or QIF), several at once. "
        + (
            "Each is read in to this account."
            if data.scoped
            else "You say which account each is for."
        )
        + "</p></form>"
    )


def _evidence_html(evidence: Evidence, data: BringInData) -> str:
    if not evidence.summary:
        return ""
    lines = list(evidence.lines)
    if data.decided:
        lines.append(_esc("Set aside by you: " + "; ".join(data.decided) + "."))
    lines.append(
        '<a class="tap bi-door" href="/statement-shape">A statement, on its own</a>'
        '<a class="tap bi-door" href="/import">An export, on its own</a>'
    )
    lines.append(_esc(UTC_NOTE))
    items ="".join(f"<li>{line}</li>" for line in lines)
    return (
        f'<details class="evidence"><summary>{_esc(evidence.summary)}</summary>'
        f"<ul>{items}</ul></details>"
    )


def _kept_line(data: BringInData) -> str:
    if not data.kept_waiting:
        return ""
    return (
        f'<p class="bi-notice">{plural(data.kept_waiting, "kept statement")} '
        f'{"is" if data.kept_waiting == 1 else "are"} waiting for an account. '
        '<a class="tap" href="/statements">Give '
        f'{"it" if data.kept_waiting == 1 else "them"} one</a></p>'
    )


def _strip_html(
    trust: Trust | None,
    timeline: AccountTimeline | None,
    files: Sequence[WantedFile],
    today: date,
) -> str:
    """The trust bar, then one lane each for the statements and the exports held and wanted."""
    rows = ""
    if trust is not None:
        rows += f'<span class="lane first">Trust</span>{bar_html(trust, today)}'
    held: dict[str, list[tuple[date, date]]] = {STATEMENT: [], EXPORT: []}
    if timeline is not None:
        for lane in timeline.lanes:
            if lane.kind in held:
                held[lane.kind].extend((run.first, run.last) for run in lane.runs)
    for kind, name in _LANES:
        wanted = [
            (item.first, item.last) for item in files if item.export == (kind == EXPORT)
        ]
        if held[kind] or wanted:
            rows += f'<span class="lane">{name}</span>{source_lane_html(held[kind], wanted, today)}'
    return f'<div class="bi-strip">{rows}</div>' if rows else ""


def _axis_html(today: date) -> str:
    names = "".join(
        f'<span style="left:{left:.2f}%">{_esc(name)}</span>'
        for name, left in month_marks(today)[::2]
    )
    return (
        f'<div class="bi-strip"><span></span><span class="axis" aria-hidden="true">{names}</span>'
        "</div>"
    )


def _file_html(item: WantedFile, today: date) -> str:
    what = "Export" if item.export else "Statement"
    days = f"{item.first.isoformat()} to {item.last.isoformat()}"
    why = _esc(item.words)
    if item.since is not None:
        head, age = _age_words(item.since, today)
        aged = f' <span class="age">{_esc(age)}</span>' if age else ""
        if item.kind is GapKind.NEWER_STATEMENT:
            why += f" since {head}{aged}"
        elif age:
            why += f" &middot;{aged}"
    href = mark_query(item.account, item.source, item.first, item.last)
    css = "bi-file guess" if item.guess else "bi-file"
    return (
        f'<li class="{css}"><p class="bi-what"><b>{what}</b> '
        f'<span class="mono">{_whole_dates(_esc(days))}</span> '
        f"{span_phrase(item.first, item.last)}</p>"
        f'<p class="bi-why">{_whole_dates(why)}</p>'
        f'<a class="tap bi-aside" href="{_esc(href)}">Set aside&hellip;</a></li>'
    )


def _account_html(
    ref: str, files: Sequence[WantedFile], data: BringInData
) -> str:
    shown = data.names.of(ref)
    trust = data.trust.get(ref)
    bank = data.banks.get(ref, ())
    label = (
        f'<span class="muted">{_esc(", ".join(bank))}</span>' if bank else ""
    )
    said = (
        f'<p class="bi-trust">{_whole_dates(_esc(trust.short))}</p>'
        if trust is not None and trust.short
        else ""
    )
    upload = (
        f'<a class="tap bi-upload" href="/bring-in?account={quote(ref, safe="")}#upload" '
        f'aria-label="Upload for {_esc(shown.name)}">Upload</a>'
    )
    return (
        f'<section class="bi-account" id="{_esc(_ref_anchor(ref))}" '
        f'aria-label="{_esc(shown.name)}">'
        f'<h3 class="bi-who"><a class="tap" href="{_esc(account_page(ref))}">{shown.as_name()}</a>'
        f"{label}{upload}</h3>{said}"
        f"{_strip_html(trust, data.timelines.get(ref), files, data.today)}"
        f'<ul class="bi-files">{"".join(_file_html(item, data.today) for item in files)}</ul>'
        "</section>"
    )


def _wanted_html(data: BringInData, *, still: bool) -> str:
    if data.report is None:
        said = data.unread or "What is wanted could not be worked out just now."
        return f'<p class="warn">{_esc(said)}</p>'
    files = files_wanted(data.report)
    if not files:
        return "" if still else '<p class="quiet-ok bi-clear">Nothing is wanted.</p>'
    groups = by_account(files)
    return (
        f"<h2>{_esc(wanted_heading(files, still=still))}</h2>{_axis_html(data.today)}"
        + "".join(_account_html(ref, items, data) for ref, items in groups.items())
    )


def _mask(text: str) -> str:
    """A refusal's words with every digit masked: it can state a discrepancy in figures."""
    return re.sub(r"\d", "9", text)[:300]


_FILE_SAYS = {
    Outcome.KEPT: "kept, waiting for an account",
    Outcome.HELD: "held, waiting for an account to check it against",
    Outcome.CONFIRM: "kept, and needs your confirmation before it is read in",
}


def _says(result: FileResult, names: AccountsShown) -> str:
    if result.outcome is Outcome.PLACED:
        return f"read in to {names.of(result.account).as_name()}"
    if result.outcome is Outcome.ALREADY:
        return f"already held, under {names.of(result.account).as_name()}"
    if result.outcome is Outcome.REFUSED:
        return f"not read in: {_esc(result.note)}"
    if result.outcome is Outcome.HELD and result.account:
        return f"held, ready to check against {names.of(result.account).as_name()}"
    return _esc(_FILE_SAYS[result.outcome])


def _ask_html(result: FileResult, picker: str, names: AccountsShown) -> str:
    """The question a file waiting for an account asks, with its control, for that file alone."""
    name = code_html(result.filename)
    if result.outcome is Outcome.CONFIRM:
        what = "Confirm which account this statement is for"
        why = (
            f"{name} may not be {names.of(result.account).as_name()}'s. It is kept; nothing "
            "is read in until you have seen why."
        )
        form = (
            '<form class="todo-form one" method="post" action="/statement-assign">'
            f'<input type="hidden" name="artefact" value="{result.artefact}">'
            f'<input type="hidden" name="account" value="{_esc(result.account)}">'
            '<button class="button" type="submit">See why</button></form>'
        )
    elif result.kind is UploadKind.STATEMENT:
        what = "Say which account this statement is for"
        why = f"{name} is kept; nothing is read in until you say."
        form = (
            '<form class="todo-form one" method="post" action="/statement-assign">'
            f'<input type="hidden" name="artefact" value="{result.artefact}">'
            f'{picker}<button class="button" type="submit">Read it in to this account</button>'
            "</form>"
        )
    else:
        what = "Say which account this export is for"
        why = f"{name} is held; nothing is stored until you have seen it checked."
        chooser = (
            f'<input type="hidden" name="account" value="{_esc(result.account)}">'
            if result.account
            else picker
        )
        label = (
            f"Check it against {names.of(result.account).name}"
            if result.account
            else "Check it against this account"
        )
        form = (
            '<form class="todo-form one" method="post" action="/upload-preview">'
            f'<input type="hidden" name="token" value="{_esc(result.token)}">'
            f'{chooser}<button class="button" type="submit">{_esc(label)}</button></form>'
        )
    return (
        '<li class="todo soon lead"><div class="todo-text">'
        f'<p class="todo-what">{_esc(what)}</p><p class="todo-why">{why}</p></div>{form}</li>'
    )


def _reasons_html(data: BringInData) -> str:
    """Why each file is wanted, in full, folded: the evidence behind the few words on each line."""
    if data.report is None:
        return ""
    items = []
    for outlook in data.report.accounts:
        if outlook.space_of:
            continue
        for gap in outlook.gaps:
            if gap.kind in BALANCE_KINDS:
                continue
            dates, action, why = gap_lines(gap)
            items.append(
                f"<li><b>{data.names.of(gap.account).as_name()}</b> "
                f'<span class="mono bi-range">{_esc(dates)}</span> '
                f"{_esc(action)} {_esc(why)}</li>"
            )
    if not items:
        return ""
    return (
        '<details class="evidence"><summary>Why each is wanted</summary>'
        f"<ul>{''.join(items)}</ul></details>"
    )


def _results_html(data: BringInData) -> str:
    results = data.results
    if results is None:
        return ""
    names = data.names
    settled = "".join(f'<p class="ok bi-settled">{_esc(text)}</p>' for text in results.settled)
    rows = []
    for ref in results.lockable:
        shown = names.of(ref)
        rows.append(
            '<li class="todo offer"><div class="todo-text">'
            f'<p class="todo-what">Lock in {shown.as_name()}</p>'
            '<p class="todo-why">It has days that add up and are not locked in. Locking in '
            "is done on its page, with its transactions in view.</p></div>"
            f'<a class="button secondary" href="{_esc(account_page(ref))}">Lock in</a></li>'
        )
    rows += [
        _ask_html(item, results.picker, names)
        for item in results.files
        if item.outcome in (Outcome.KEPT, Outcome.HELD, Outcome.CONFIRM)
    ]
    held = "".join(
        f"<li>{code_html(item.filename)}: {_says(item, names)}.</li>" for item in results.files
    )
    return (
        f"<h2>{_esc(plural(len(results.files), 'file'))} received</h2>{settled}"
        + (f'<ul class="todos bi-ask">{"".join(rows)}</ul>' if rows else "")
        + f'<details class="evidence"><summary>What each file held</summary><ul>{held}</ul>'
        "</details>"
    )


def render_bring_in(data: BringInData) -> bytes:
    """The page: the target, what an upload settled, the one evidence line, then what is wanted."""
    after = data.results is not None
    notice = f'<p class="bi-notice">{_esc(data.notice)}</p>' if data.notice else ""
    wanted = _wanted_html(data, still=after)
    drop = (
        f"{_results_html(data)}{notice}{_target_html(data)}{_kept_line(data)}"
        + (wanted if not after and wanted.startswith('<p class="quiet-ok') else "")
        + _evidence_html(data.evidence, data)
    )
    list_ = "" if wanted.startswith('<p class="quiet-ok') else wanted
    links = (
        '<p class="muted bi-links"><a class="tap" href="/connections">Bank connections</a>'
        + (
            f' &middot; <a class="tap" href="/statements">{_esc(plural(data.kept, "file"))} kept'
            "</a>"
            if data.kept
            else ""
        )
        + "</p>"
    )
    return render_page(
        "Bring in",
        MARKS_STYLE_TAG
        + _decisions_html(data, above=True)
        + '<div class="bi">'
        + f'<div class="bi-drop">{drop}</div>'
        + f'<section class="bi-wanted" aria-label="Wanted">{list_}{_reasons_html(data)}{links}'
        "</section></div>"
        + _decisions_html(data, above=False),
        wide=True,
        body_class="bring-page",
    )


def _decisions_html(data: BringInData, *, above: bool) -> str:
    """What the owner has decided about what is wanted, from `web_marks`' own words.

    A mark the store now contradicts is said ABOVE the list, where it cannot be missed. The rest
    is folded beneath it: how far back he keeps, what the aggregator can still be asked for, and
    each decision with its evidence and its Undo.
    """
    report = data.report
    if report is None:
        return ""
    marks, names = report.marks, data.names
    if above:
        return contradicted_html(marks, names, report.today)
    offers = offers_html(marks, names)
    return (
        scope_lines_html(marks, names, report.today, report.first_known_balance)
        + (
            '<details class="evidence"><summary>What the aggregator can still be asked for'
            f"</summary>{offers}</details>"
            if offers
            else ""
        )
        + folded_html(
            marks,
            report.set_aside,
            report.out_of_scope,
            names,
            [o.account for o in report.accounts],
            report.today,
        )
    )


# --------------------------------------------------------------------------------- What it gathers


def source_lines(
    store: ConnectionStore | None,
    answered: Mapping[str, str],
    *,
    feed_configured: bool,
    now: datetime,
) -> Evidence:
    """The line that every source was looked at, and each source beneath it.

    "Looked at today" is said only where every source has answered today; its time is the
    stalest source's last answer, the moment from which every one had been looked at. Otherwise
    the line says that not every source has, and the sources say which.
    """
    sources: list[tuple[str, datetime | None]] = []
    lines: list[str] = []
    try:
        connections: list[Connection] = (
            sorted(store, key=lambda c: c.connection_id) if store is not None else []
        )
    except Exception:
        connections = []
        lines.append("The bank connections could not be read just now.")
    for connection in connections:
        moment = _instant(answered.get(connection.connection_id))
        sources.append((connection.connection_id, moment))
        heard = (
            f"last answered {_esc(instant_of(answered.get(connection.connection_id, '')))}"
            if moment
            else "has never answered"
        )
        expires = connection.consent_expires_on()
        consent = f"; consent lasts until {expires.isoformat()}" if expires else ""
        lines.append(
            f"{code_html(connection.connection_id)}, through the aggregator: {heard}{consent}."
        )
    if feed_configured:
        raw = answered.get(STARLING_CONNECTION)
        moment = _instant(raw)
        sources.append((STARLING_CONNECTION, moment))
        lines.append(
            "The bank's own feed: "
            + (f"last answered {_esc(instant_of(raw or ''))}." if moment else "has never answered.")
        )
    if not sources:
        return Evidence("No bank is connected", tuple(lines))
    today = now.astimezone(UTC).date()
    times = [moment for _, moment in sources]
    if all(moment is not None and moment.astimezone(UTC).date() == today for moment in times):
        stale = min(moment for moment in times if moment is not None)
        return Evidence(
            f"Every source looked at today at {stale.astimezone(UTC).strftime('%H:%M')}",
            tuple(lines),
        )
    missing = [
        name
        for name, moment in sources
        if moment is None or moment.astimezone(UTC).date() != today
    ]
    return Evidence(f"{plural(len(missing), 'source')} not looked at today", tuple(lines))


def _instant(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def quiet_lines(report: FetchReport | None, names: AccountsShown) -> tuple[str, ...]:
    """What needs nothing: when the next statement of each is expected, and the balances stated
    by hand. Said in the evidence, never as a list of what is fine."""
    if report is None:
        return ()
    lines: list[str] = []
    coming = [
        f"{names.of(o.account).as_name()} about {o.next_expected.isoformat()}"
        for o in report.accounts
        if not o.gaps and o.next_expected is not None
    ]
    if coming:
        lines.append("Next statement: " + "; ".join(coming) + ".")
    for outlook in report.accounts:
        if outlook.balance_only:
            lines.append(f"{names.of(outlook.account).as_name()}: you state its balance by hand.")
    return tuple(lines)


def bank_labels(
    held: Mapping[tuple[str, str], list[str]], refs: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """The banks an account's transactions came through the aggregator from, by the name the
    connection was given: a secondary label, where a connection tells it."""
    wanted = set(refs)
    found: dict[str, set[str]] = {}
    for (account, source), connections in held.items():
        if account in wanted and source.startswith(AGGREGATOR):
            found.setdefault(account, set()).update(connections)
    return {ref: tuple(sorted(names)) for ref, names in found.items()}


def trusts(
    overview: Overview | None, report: FetchReport | None, today: date
) -> dict[str, Trust]:
    """Each account's trust, as Today's list says it, for the accounts a file is wanted for."""
    if overview is None:
        return {}
    wanted = wanted_days(report)
    by_ref: dict[str, AccountOverview] = {a.ref: a for a in overview.accounts}
    return {
        ref: trust_of(
            first=by_ref[ref].first,
            newest=by_ref[ref].newest,
            standing=by_ref[ref].standing,
            wanted=wanted.get(ref, ()),
            today=today,
        )
        for ref in {item.account for item in files_wanted(report)}
        if ref in by_ref
    }


# ------------------------------------------------------------------------------------- The routes


class BringInPages:
    """The Bring in routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _account_names(self) -> AccountsShown:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def picker_account_options(self) -> dict[str, str]:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _read_shape(
        self, payload: bytes, filename: str, *, mask: bool, columns: bool = True
    ) -> ShapeReport:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _stash(self, payload: bytes, filename: str) -> str:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _redirect(self, location: str) -> None:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    # -- reading ----------------------------------------------------------------------------

    def _standings(self) -> dict[str, AccountStanding]:
        hook = self.bound_config.account_standings
        if hook is None:
            return {}
        try:
            return dict(hook())
        except Exception:
            return {}

    def _data(
        self,
        *,
        scoped: str = "",
        results: UploadResults | None = None,
        notice: str = "",
    ) -> BringInData:
        config = self.bound_config
        today = datetime.now(UTC).date()
        now = datetime.now(UTC)
        names = self._account_names()
        report: FetchReport | None = None
        unread = ""
        hook = config.fetch_gaps
        if hook is None:
            unread = "What is wanted is not available on this instance."
        else:
            try:
                report = hook(today)
            except RebuildInProgress as paused:
                unread = paused.hold.sentence()
            except Exception:
                unread = "What is wanted could not be worked out just now."
        overview: Overview | None = None
        if config.overview is not None and report is not None:
            try:
                overview = config.overview(False)
            except Exception:
                overview = None
        refs = sorted({item.account for item in files_wanted(report)})
        timelines: dict[str, AccountTimeline] = {}
        compact = config.coverage_timeline_compact
        if compact is not None:
            for ref in refs:
                try:
                    found = compact(ref, today)
                except Exception:
                    found = None
                if found is not None:
                    timelines[ref] = found
        answered: dict[str, str] = {}
        if config.connection_last_answered is not None:
            try:
                answered = config.connection_last_answered()
            except Exception:
                answered = {}
        held: dict[tuple[str, str], list[str]] = {}
        if config.source_connections is not None:
            try:
                held = config.source_connections()
            except Exception:
                held = {}
        evidence = source_lines(
            config.connection_store,
            answered,
            feed_configured=config.starling_probe is not None,
            now=now,
        )
        extra = quiet_lines(report, names)
        evidence = Evidence(evidence.summary or "Sources", (*evidence.lines, *extra))
        kept, waiting = self._kept_counts()
        return BringInData(
            today=today,
            report=report,
            unread=unread,
            names=names,
            trust=trusts(overview, report, today),
            timelines=timelines,
            banks=bank_labels(held, refs),
            evidence=evidence,
            scoped=scoped,
            results=results,
            notice=notice,
            kept=kept,
            kept_waiting=waiting,
        )

    def _kept_counts(self) -> tuple[int | None, int]:
        hook = self.bound_config.kept_statements
        if hook is None:
            return None, 0
        try:
            entries = hook()
        except Exception:
            return None, 0
        waiting = sum(
            1
            for e in entries
            if e["account_ref"] == UNASSIGNED_ACCOUNT
            and e["parser"]
            and not e.get("refusal")
            and not e.get("sections")
        )
        return len(entries), waiting

    # -- GET ----------------------------------------------------------------------------------

    def _bring_in_page(self, params: Mapping[str, list[str]]) -> None:
        scoped = (params.get("account", [""])[0] or "").strip()
        if scoped and scoped not in self._account_names():
            scoped = ""
        self._respond(200, render_bring_in(self._data(scoped=scoped)))

    def _gaps_redirect(self, params: Mapping[str, list[str]]) -> None:
        """The old address of What to fetch next, now the wanted list of Bring in."""
        ref = (params.get("ref", [""])[0] or "").strip()
        anchor = f"#{_ref_anchor(ref)}" if ref else ""
        self._redirect(f"/bring-in{anchor}")

    # -- POST ---------------------------------------------------------------------------------

    def _bring_in_post(
        self,
        content_type: str,
        length: int,
        read_body: Callable[[], bytes],
    ) -> None:
        from .web import _parse_multipart_files

        if length > UPLOAD_LIMIT:
            self._respond(
                413,
                render_page(
                    "Too large",
                    "<p>Even a batch of statements is not this big. Upload them in smaller "
                    'groups.</p><p><a class="button" href="/bring-in">Back to Bring in</a></p>',
                ),
            )
            return
        try:
            files, fields = _parse_multipart_files(content_type, read_body())
        except Exception as exc:
            self._respond(
                400,
                self._answer(self._data(notice=f"The upload could not be read: {_mask(str(exc))}")),
                no_store=True,
            )
            return
        if not files:
            self._respond(
                400, self._answer(self._data(notice="No file was chosen.")), no_store=True
            )
            return
        account = (fields.get("account") or "").strip()
        if account and account not in self._account_names():
            self._respond(
                400,
                self._answer(self._data(notice="That is not an account obdi knows.")),
                no_store=True,
            )
            return
        before = self._standings()
        results = [self._place(payload, filename, account) for payload, filename in files]
        after = self._standings()
        names = self._account_names()
        placed = sorted({r.account for r in results if r.outcome is Outcome.PLACED})
        settled = tuple(
            text
            for ref in placed
            if (text := settled_sentence(names.of(ref).name, before.get(ref), after.get(ref)))
        )
        picker = ""
        if any(r.outcome in (Outcome.KEPT, Outcome.HELD) and not r.account for r in results):
            from .web import account_picker

            picker = account_picker(self.picker_account_options())
        summary = UploadResults(
            tuple(results), settled, newly_lockable(before, after), picker
        )
        self._respond(
            200, self._answer(self._data(scoped=account, results=summary)), no_store=True
        )

    def _answer(self, data: BringInData) -> bytes:
        return render_bring_in(data)

    def answer_standings(self) -> dict[str, AccountStanding]:
        """The accounts' standings now, for a door outside this page that settles one of its
        files and answers with this page (`answer_settled`)."""
        return self._standings()

    def answer_settled_words(self, account: str, before: dict[str, AccountStanding]) -> str:
        """What a file settled about `account`, in Bring in's own words, for an answer page that
        is not this one but was reached from it (an import's result)."""
        return settled_sentence(
            self._account_names().of(account).name,
            before.get(account),
            self._standings().get(account),
        )

    def answer_settled(
        self, artefact: int, account: str, before: dict[str, AccountStanding]
    ) -> None:
        """Answer a kept statement given its account, from this page's own form, with this page:
        what it settled in the results at the top and what is still wanted beneath, so the next
        file is one press away and the old answer page is not where the person ends up."""
        names = self._account_names()
        filename = f"statement {artefact}"
        hook = self.bound_config.kept_statements
        if hook is not None:
            with contextlib.suppress(Exception):
                for entry in hook():
                    if int(str(entry["id"])) == artefact:
                        filename = str(entry.get("origin") or filename)
        after = self._standings()
        sentence = settled_sentence(names.of(account).name, before.get(account), after.get(account))
        summary = UploadResults(
            (FileResult(filename, UploadKind.STATEMENT, Outcome.PLACED, account=account,
                        artefact=artefact),),
            (sentence,) if sentence else (),
            newly_lockable(before, after),
        )
        self._respond(200, self._answer(self._data(results=summary)), no_store=True)

    def _place(self, payload: bytes, filename: str, account: str) -> FileResult:
        name = filename or "upload"
        if upload_kind(name, payload) is UploadKind.STATEMENT:
            return self._place_statement(payload, name, account)
        return self._hold_export(payload, name, account)

    def _place_statement(self, payload: bytes, name: str, account: str) -> FileResult:
        config = self.bound_config
        kind = UploadKind.STATEMENT
        keeper = config.keep_statement
        if keeper is None:
            return FileResult(name, kind, Outcome.REFUSED, note="Statements are not kept here.")
        shape = self._read_shape(payload, name, mask=True, columns=False)
        if not (shape.readable and shape.line_count):
            return FileResult(
                name,
                kind,
                Outcome.REFUSED,
                note="It could not be read as a PDF with text in it, so it was not kept.",
            )
        artefact, was_new = keeper(payload, name)
        if not artefact:
            return FileResult(name, kind, Outcome.REFUSED, note="It could not be kept.")
        if not was_new:
            held_under = self._held_under(artefact)
            if held_under:
                return FileResult(
                    name, kind, Outcome.ALREADY, account=held_under, artefact=artefact
                )
        if not account:
            return FileResult(name, kind, Outcome.KEPT, artefact=artefact)
        review = config.review_kept_statement
        if review is not None:
            try:
                doubt = review(artefact, account)
            except Exception:
                doubt = None
            if doubt is not None:
                return FileResult(
                    name, kind, Outcome.CONFIRM, account=account, artefact=artefact
                )
        assign = config.assign_kept_statement
        if assign is None:
            return FileResult(name, kind, Outcome.KEPT, account=account, artefact=artefact)
        try:
            outcome = assign(artefact, account)
        except Exception as exc:
            return FileResult(
                name, kind, Outcome.REFUSED, account=account, artefact=artefact,
                note=_mask(str(exc)),
            )
        if " read by " not in outcome:
            return FileResult(
                name, kind, Outcome.REFUSED, account=account, artefact=artefact,
                note=_mask(outcome),
            )
        return FileResult(name, kind, Outcome.PLACED, account=account, artefact=artefact)

    def _held_under(self, artefact: int) -> str:
        """The account a statement already held is filed under, "" where it waits for one."""
        hook = self.bound_config.kept_statements
        if hook is None:
            return ""
        try:
            entries = hook()
        except Exception:
            return ""
        for entry in entries:
            if int(str(entry["id"])) == artefact and entry["account_ref"] != UNASSIGNED_ACCOUNT:
                return str(entry["account_ref"])
        return ""

    def _hold_export(self, payload: bytes, name: str, account: str) -> FileResult:
        kind = UploadKind.EXPORT
        if self.bound_config.preview_upload is None:
            return FileResult(name, kind, Outcome.REFUSED, note="Exports are not read here.")
        if len(payload) > EXPORT_LIMIT:
            return FileResult(
                name, kind, Outcome.REFUSED, note="Bank exports are small; this is not one."
            )
        return FileResult(
            name, kind, Outcome.HELD, account=account, token=self._stash(payload, name)
        )


__all__ = [
    "BringInData",
    "BringInPages",
    "Evidence",
    "FileResult",
    "Outcome",
    "UploadResults",
    "render_bring_in",
]
