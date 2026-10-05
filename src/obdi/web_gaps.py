"""What to fetch next: the files the owner still has to fetch by hand, account by account.

The gaps are `fetch_gaps`' to work out; this page only says them. Each gap is a date range he can
type into a bank's site, in the imperative, with the reason it matters beneath and the source
that will read the file where the statements already held say so. A gap that is a fact the store
holds is drawn with a solid rail and the word "stated"; one inferred from the rhythm of the
statements held is drawn with a dashed rail and the word "inferred", so the two are told apart
without reading the sentence.

ONE VERDICT SENTENCE (`verdict_sentence`) heads the page and is the Bring in hub's line for it,
so the two never say different things. Served on a GET, so dates, account names, source names, and
counts only: no amount, no description.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import quote

from .account_names import merged_names, name_html
from .callback import render_page
from .fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from .navigation import page_name
from .plural import agree, plural
from .rebuild_hold import RebuildInProgress
from .window_control import BETWEEN

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

#: The days either side of a gap that the coverage timeline's window is widened by, so the
#: gap is seen with what holds it in, not at the very edge of the chart.
TIMELINE_MARGIN_DAYS = 14

#: The line the pages about what is held (coverage, kept statements) carry to send a reader who
#: is looking for a missing file to the page that lists them.
FETCH_NEXT_LINE = (
    '<p class="muted">Missing a statement or an export? '
    '<a class="tap" href="/gaps">What to fetch next</a> lists them, account by account, '
    "with the dates to ask for.</p>"
)

_EXPORT_KINDS = frozenset({GapKind.EXPORT_STOPS, GapKind.EXPORT_MONTHS})
_BALANCE_KINDS = frozenset({GapKind.NO_BALANCE, GapKind.AUTOMATIC_ONLY, GapKind.ONE_BALANCE})

_KIND_WORDS = {
    GapKind.NEWER_STATEMENT: "Statement",
    GapKind.HOLE_BETWEEN: "Statement",
    GapKind.NOTHING_BEFORE: "Statement",
    GapKind.FLAG_SETTLE: "Statement",
    GapKind.NO_BALANCE: "Statement or balance",
    GapKind.AUTOMATIC_ONLY: "Statement or balance",
    GapKind.ONE_BALANCE: "Statement",
    GapKind.EXPORT_STOPS: "Export",
    GapKind.EXPORT_MONTHS: "Export",
}


def verdict_sentence(report: FetchReport) -> str:
    """The page's one-sentence verdict, which the Bring in hub repeats."""
    if not report.accounts:
        return "No account holds rows yet, so there is nothing to fetch."
    things = len(report.gaps)
    needing = len(report.needing)
    rest = len(report.accounts) - needing
    if not things:
        said = f"Nothing to fetch for {plural(rest, 'account')}."
        if report.next_expected is not None:
            said += f" The next statement is expected about {report.next_expected.isoformat()}."
        return said
    said = f"{plural(things, 'thing')} to fetch for {plural(needing, 'account')}"
    if rest:
        said += f"; {plural(rest, 'account')} {agree(rest, 'needs')} nothing"
    return said + "."


def _ledger(ref: str, anchor: str = "") -> str:
    return f"/ledger?ref={quote(ref, safe='')}{anchor}"


def _timeline_link(ref: str, gap: FetchGap, today: date) -> str:
    """The coverage timeline over the gap and a margin either side (never past today), where
    that page is served."""
    from . import web_destinations

    if not web_destinations.dispatcher_serves("/coverage-timeline"):
        return ""
    first = gap.first_day - timedelta(days=TIMELINE_MARGIN_DAYS)
    last = min(gap.last_day + timedelta(days=TIMELINE_MARGIN_DAYS), today)
    href = (
        f"/coverage-timeline?ref={quote(ref, safe='')}&window={BETWEEN}"
        f"&window_from={first.isoformat()}&window_to={last.isoformat()}"
    )
    return f'<a class="tap gaps-timeline" href="{_esc(href)}">See it on the coverage timeline</a>'


def _closings(days: tuple[date, ...]) -> str:
    named = [day.isoformat() for day in days]
    if len(named) <= 2:
        return " and ".join(named)
    return f"{', '.join(named[:-1])}, and {named[-1]}"


def _inferred(gap: FetchGap) -> str:
    """The sentence that says a count of statements is a guess, and from what."""
    if not gap.probably:
        return ""
    count = plural(gap.probably, "statement")
    return (
        f"Probably {count} {agree(gap.probably, 'is')} missing here, closing about "
        f"{_closings(gap.closings)}. That is inferred from how regularly the statements held "
        "arrive."
    )


def _lines(gap: FetchGap) -> tuple[str, str, str]:
    """(the dates to type, what to do, why), each a sentence or phrase of plain text."""
    first, last = gap.first_day.isoformat(), gap.last_day.isoformat()
    span = f"{first} to {last}"
    before = (gap.first_day - timedelta(days=1)).isoformat()
    kind = gap.kind
    if kind is GapKind.NEWER_STATEMENT:
        held_to = gap.rows_to.isoformat() if gap.rows_to else last
        why = f"Rows are held to {held_to} with no known balance since {before}."
        if gap.probably:
            why += " " + _inferred(gap).replace("missing here", "waiting")
        return span, f"Fetch every statement after {before}.", why
    if kind is GapKind.HOLE_BETWEEN:
        after = (gap.last_day + timedelta(days=1)).isoformat()
        if gap.basis is Basis.STATED:
            return (
                span,
                f"Fetch the statement covering {span}.",
                f"The statement after it says its period begins on {after}, and the one before "
                f"closed on {before}, so no statement held covers the days between.",
            )
        return (
            span,
            f"Fetch the statement closing between {before} and {after}.",
            f"Those two statements close {(gap.last_day - gap.first_day).days + 2} days apart, "
            f"and the statements held usually close about a month apart. {_inferred(gap)}",
        )
    if kind is GapKind.EXPORT_STOPS:
        held = f" other sources hold rows to {gap.rows_to.isoformat()}." if gap.rows_to else ""
        return (
            span,
            f"Export from {first} to today.",
            f"The export last covers {before};{held or ' other sources hold later rows.'}",
        )
    if kind is GapKind.EXPORT_MONTHS:
        return (
            span,
            f"Export {span}.",
            "Another source holds rows in these months and the export holds none.",
        )
    if kind is GapKind.NO_BALANCE:
        return (
            span,
            f"Fetch a statement covering {span}, or state a balance.",
            "No known balance: these rows cannot be verified.",
        )
    if kind is GapKind.AUTOMATIC_ONLY:
        return (
            span,
            f"Fetch a statement covering {span}, or state a balance.",
            "Only the bank's feed and the aggregator supply this account and no known balance "
            "is held: these rows cannot be verified.",
        )
    if kind is GapKind.ONE_BALANCE:
        return (
            span,
            f"Fetch the statement before these rows, closing on or before {before}, "
            "or the one after them.",
            "Only one known balance is held, so it sets the opening balance and nothing tests "
            "the rows yet.",
        )
    if kind is GapKind.NOTHING_BEFORE:
        return (
            span,
            f"Fetch an earlier statement, closing on or before {before}.",
            f"Rows from {first} to {last} have no known balance before them, so they cannot "
            "be tested.",
        )
    return (
        gap.first_day.isoformat(),
        "Fetch a statement that settles the review flag.",
        gap.why + " The flag is on the review flags page.",
    )


def _gap_html(ref: str, gap: FetchGap, today: date) -> str:
    dates, action, why = _lines(gap)
    basis = "inferred" if gap.basis is Basis.INFERRED else "stated"
    if gap.kind is GapKind.NEWER_STATEMENT and gap.probably:
        basis_note = '<span class="pill">stated</span> <span class="pill">count inferred</span>'
    else:
        basis_note = f'<span class="pill">{basis}</span>'
    source = (
        f'<span class="gaps-source">read as <span class="mono">{_esc(gap.source)}</span></span>'
        if gap.source
        else ""
    )
    flag = (
        '<p class="gaps-links"><a class="tap" href="/review-flags">Open the review flags</a></p>'
        if gap.kind is GapKind.FLAG_SETTLE
        else ""
    )
    timeline = _timeline_link(ref, gap, today)
    links = f'<p class="gaps-links">{timeline}</p>' if timeline else ""
    return (
        f'<li class="gaps-item gaps-{basis}">'
        f'<p class="gaps-head"><span class="gaps-kind">{_esc(_KIND_WORDS[gap.kind])}</span> '
        f"{source} {basis_note}</p>"
        f'<p class="gaps-range mono">{_esc(dates)}</p>'
        f'<p class="gaps-do">{_esc(action)}</p>'
        f'<p class="gaps-why">{_esc(why)}</p>{flag}{links}</li>'
    )


def _actions(ref: str, outlook: AccountOutlook) -> str:
    kinds = {gap.kind for gap in outlook.gaps}
    links = [f'<a class="tap" href="{_esc(_ledger(ref))}">Its page</a>']
    if kinds - _EXPORT_KINDS:
        links.append('<a class="tap" href="/statement-shape">Upload a statement</a>')
    if kinds & _EXPORT_KINDS:
        links.append('<a class="tap" href="/import">Import an export</a>')
    if kinds & _BALANCE_KINDS:
        links.append(f'<a class="tap" href="{_esc(_ledger(ref, "#opening"))}">State a balance</a>')
    return f'<p class="gaps-actions">{" ".join(links)}</p>'


def _account_html(outlook: AccountOutlook, names: Mapping[str, str], today: date) -> str:
    ref = outlook.account
    items = "".join(_gap_html(ref, gap, today) for gap in outlook.gaps)
    return (
        f'<section class="gaps-account" aria-label="{_esc(names.get(ref) or ref)}">'
        f'<h2 class="gaps-name">{name_html(ref, names)}</h2>'
        f'<ul class="gaps-list">{items}</ul>{_actions(ref, outlook)}</section>'
    )


def _quiet_line(outlook: AccountOutlook, names: Mapping[str, str]) -> str:
    ref = outlook.account
    if outlook.balance_only:
        said = (
            "its balances are stated by hand, so there is no file to fetch. "
            f'<a class="tap" href="{_esc(_ledger(ref, "#opening"))}">State a balance</a>'
        )
    elif outlook.next_expected is not None:
        said = f"next statement expected about {outlook.next_expected.isoformat()}."
    else:
        said = (
            f"{plural(outlook.statements, 'statement')} held and none due yet."
            if outlook.statements
            else "nothing is due."
        )
    return (
        f'<li class="gaps-quiet-item"><a class="tap" href="{_esc(_ledger(ref))}">'
        f"{name_html(ref, names)}</a> <span class=\"muted\">- {said}</span></li>"
    )


def render_gaps(
    report: FetchReport, names: Mapping[str, str], *, rebuilding: str = ""
) -> bytes:
    """The page: the verdict, each account that needs something, then those that need nothing."""
    title = page_name("/gaps")
    if rebuilding:
        return render_page(title, f'<p class="lede">{_esc(rebuilding)}</p>')
    body = f'<p class="lede gaps-verdict">{_esc(verdict_sentence(report))}</p>'
    body += "".join(_account_html(outlook, names, report.today) for outlook in report.needing)
    quiet = [outlook for outlook in report.accounts if not outlook.gaps]
    if quiet:
        body += (
            '<h2 class="gaps-quiet-head">Needs nothing</h2>'
            f'<ul class="gaps-quiet">{"".join(_quiet_line(o, names) for o in quiet)}</ul>'
        )
    body += (
        '<p class="muted gaps-notes">A stated gap is something the store holds the evidence for. '
        "An inferred one is a guess from how regularly the statements held arrive, and is only "
        "made from three or more statements a month apart. Today is "
        f"{report.today.isoformat()}.</p>"
    )
    return render_page(title, body, body_class="gaps-page")


class GapPages:
    """The route, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _gaps_page(self) -> None:
        config = self.bound_config
        hook = config.fetch_gaps
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        labels: dict[str, str] = {}
        if config.display_labels is not None:
            try:
                labels = config.display_labels()
            except Exception:
                labels = {}
        declared = []
        if config.declared_accounts is not None:
            try:
                declared = config.declared_accounts()
            except Exception:
                declared = []
        names = merged_names(labels, declared)
        today = datetime.now(UTC).date()
        try:
            report = hook(today)
        except RebuildInProgress as paused:
            self._respond(
                200,
                render_gaps(FetchReport((), today), names, rebuilding=paused.hold.sentence()),
            )
            return
        self._respond(200, render_gaps(report, names))
