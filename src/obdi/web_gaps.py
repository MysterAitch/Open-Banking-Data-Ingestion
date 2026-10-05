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

from .account_names import merged_names, name_html, name_text
from .callback import render_page
from .fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from .fetch_marks import (
    CLAIM_KINDS,
    KINDS,
    MarkKind,
    MarkRefused,
    MarkWorld,
    Standing,
    gather_evidence,
    judge,
    make_mark,
    read_mark,
    remove_mark,
    scopes_in,
    set_scope,
)
from .navigation import page_name
from .plural import agree, plural
from .rebuild_hold import RebuildInProgress
from .statement_span import STATEMENT_SOURCES, HoleReason
from .store import Store
from .web_marks import (
    MARKS_STYLE_TAG,
    contradicted_html,
    folded_html,
    form_html,
    gap_actions_html,
    mark_query,
    offers_html,
    scope_lines_html,
    span_words,
    verdict_clauses,
)
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
    decided = verdict_clauses(
        report.set_aside, report.out_of_scope, len(report.marks.contradicted)
    )
    if not things:
        said = f"Nothing to fetch for {plural(rest, 'account')}"
        said += "".join(f"; {clause}" for clause in decided) + "."
        if report.next_expected is not None:
            said += f" The next statement is expected about {report.next_expected.isoformat()}."
        return said
    said = f"{plural(things, 'thing')} to fetch for {plural(needing, 'account')}"
    if rest:
        said += f"; {plural(rest, 'account')} {agree(rest, 'needs')} nothing"
    said += "".join(f"; {clause}" for clause in decided)
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
        earlier = (gap.earlier_closing or gap.first_day - timedelta(days=1)).isoformat()
        later = (gap.later_closing or gap.last_day + timedelta(days=1)).isoformat()
        apart = (
            (gap.later_closing - gap.earlier_closing).days
            if gap.later_closing and gap.earlier_closing
            else (gap.last_day - gap.first_day).days + 2
        )
        between = f"Fetch the statement closing between {earlier} and {later}."
        end = (
            f" Where the gap ends is inferred: the missing statement is expected to have "
            f"closed about {gap.last_day.isoformat()}."
            if gap.last_day_inferred
            else ""
        )
        if gap.reason is HoleReason.BALANCES_DIFFER:
            spacing = (
                f" The two statements close {apart} days apart and the statements held "
                f"usually close about a month apart, which agrees. {_inferred(gap)}{end}"
                if gap.probably
                else ""
            )
            return (
                span,
                between if gap.probably else f"Fetch the statement covering {span}.",
                f"The statement closing {later} opens on a balance that is not the one the "
                f"statement closing {earlier} ended on, so something lies between them that "
                f"neither lists, starting {first}.{spacing}",
            )
        if gap.reason is HoleReason.UNLISTED_ROWS:
            return (
                span,
                f"Fetch the statement covering {span}.",
                f"Another source holds {plural(gap.unlisted_rows, 'payment')} dated in these "
                f"days that no statement held lists. {_inferred(gap)}{end}".rstrip(),
            )
        if gap.reason is HoleReason.BALANCES_MEET_NET_NIL:
            return (
                span,
                between,
                f"The later statement opens on the balance the earlier one closed on, but they "
                f"close {apart} days apart and the statements held usually close about a month "
                "apart. A statement between them would have to net to nil, which cannot be "
                f"ruled out from the balances. {_inferred(gap)}{end}".rstrip(),
            )
        if gap.basis is Basis.STATED:
            return (
                span,
                f"Fetch the statement covering {span}.",
                f"The statement after it says its period begins on {after}, and the one before "
                f"closed on {before}, so no statement held covers the days between.",
            )
        return (
            span,
            between,
            f"Those two statements close {apart} days apart, "
            f"and the statements held usually close about a month apart. {_inferred(gap)}{end}",
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
    if gap.split_from is not None:
        was = span_words(*gap.split_from)
        action = f"Fetch what covers {dates}."
        why = f"This is what remains of {was} after the part you set aside."
    note = f'<p class="gaps-why">{_esc(gap.reminder)}</p>' if gap.reminder else ""
    return (
        f'<li class="gaps-item gaps-{basis}">'
        f'<p class="gaps-head"><span class="gaps-kind">{_esc(_KIND_WORDS[gap.kind])}</span> '
        f"{source} {basis_note}</p>"
        f'<p class="gaps-range mono">{_esc(dates)}</p>'
        f'<p class="gaps-do">{_esc(action)}</p>'
        f'<p class="gaps-why">{_esc(why)}</p>{note}{flag}{links}'
        f"{gap_actions_html(ref, gap.source, gap.first_day, gap.last_day)}</li>"
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


def _quiet_line(
    outlook: AccountOutlook, names: Mapping[str, str], decided: frozenset[str] = frozenset()
) -> str:
    ref = outlook.account
    if outlook.space_of:
        said = (
            f"a Space of {name_html(outlook.space_of, names)}: no statement exists for a "
            f"Space; it is tested with {name_html(outlook.space_of, names)} as a whole."
        )
    elif ref in decided:
        said = "everything missing is set aside by your decision."
    elif outlook.balance_only:
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
    body = MARKS_STYLE_TAG + f'<p class="lede gaps-verdict">{_esc(verdict_sentence(report))}</p>'
    body += contradicted_html(report.marks, names, report.today)
    body += scope_lines_html(report.marks, names, report.today, report.first_known_balance)
    body += "".join(_account_html(outlook, names, report.today) for outlook in report.needing)
    quiet = [outlook for outlook in report.accounts if not outlook.gaps]
    decided = frozenset(item.account for item in report.set_aside)
    if quiet:
        body += (
            '<h2 class="gaps-quiet-head">Needs nothing</h2>'
            f'<ul class="gaps-quiet">{"".join(_quiet_line(o, names, decided) for o in quiet)}</ul>'
        )
    body += offers_html(report.marks, names)
    body += folded_html(
        report.marks,
        report.set_aside,
        report.out_of_scope,
        names,
        [o.account for o in report.accounts],
        report.today,
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

    def _gaps_names(self) -> dict[str, str]:
        config = self.bound_config
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
        return merged_names(labels, declared)

    def _gaps_page(self) -> None:
        config = self.bound_config
        hook = config.fetch_gaps
        if hook is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        names = self._gaps_names()
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

    # -- the owner's decisions about what is still to fetch ---------------------------------

    def _marks_page(self, status: int, title: str, body: str) -> None:
        self._respond(
            status,
            render_page(title, MARKS_STYLE_TAG + body, body_class="gaps-page"),
            no_store=True,
        )

    def _marks_refused(self, why: str, values: Mapping[str, str]) -> None:
        again = mark_query(
            values.get("account", ""),
            values.get("source", ""),
            _try_date(values.get("first", "")),
            _try_date(values.get("last", "")),
        )
        self._marks_page(
            409,
            "Not set aside",
            f'<p class="bad"><strong>{_esc(why)}</strong></p>'
            "<p>Nothing was changed.</p>"
            f'<p><a class="button" href="{_esc(again)}">Change it</a></p>{_BACK_TO_GAPS}',
        )

    def _marks_rebuilding(self, paused: RebuildInProgress) -> None:
        self._marks_page(
            503, "Set a period aside", f'<p class="lede">{_esc(paused.hold.sentence())}</p>'
        )

    def _marks_world(self, today: date) -> tuple[MarkWorld, object] | None:
        read = self.bound_config.fetch_marks_read
        if read is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return None
        try:
            return read(today)
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return None

    def _gaps_mark_form(self, params: Mapping[str, list[str]]) -> None:
        values = {
            key: (params.get(key, [""])[0] or "").strip()
            for key in ("account", "source", "first", "last", "kind", "note", "review_on")
        }
        self._render_mark_form(values, datetime.now(UTC).date())

    def _render_mark_form(self, values: Mapping[str, str], today: date, *, why: str = "") -> None:
        found = self._marks_world(today)
        if found is None:
            return
        world = found[0]
        names = self._gaps_names()
        account, source = values.get("account", ""), values.get("source", "")
        first, last = _try_date(values.get("first", "")), _try_date(values.get("last", ""))
        previews: dict[MarkKind, tuple[Standing, str, object]] = {}
        if account and last is not None and (account in world.rows or account in world.declared):
            for kind in CLAIM_KINDS:
                evidence = gather_evidence(
                    world, account, source, first, last, statement_sources=STATEMENT_SOURCES
                )
                standing, reason = judge(
                    kind, source, first, last, evidence, statement_sources=STATEMENT_SOURCES
                )
                previews[kind] = (standing, reason, evidence)
        body = (f'<p class="bad"><strong>{_esc(why)}</strong></p>' if why else "") + form_html(
            account=account,
            source=source,
            first=values.get("first", ""),
            last=values.get("last", ""),
            kind=values.get("kind", ""),
            note=values.get("note", ""),
            review_on=values.get("review_on", ""),
            today=today,
            names=names,
            accounts=sorted((set(world.rows) | set(world.declared)) - world.spaces),
            sources=sorted({src for rows in world.rows.values() for _, src in rows}),
            previews=previews,  # type: ignore[arg-type]
            parsed_last=last,
            parsed_first=first,
        )
        self._marks_page(200, page_name("/gaps-mark"), body + _BACK_TO_GAPS)

    def _gaps_mark_post(self, form: Mapping[str, list[str]]) -> None:
        values = {
            key: (form.get(key, [""])[0] or "").strip()
            for key in (
                "account", "source", "first", "last", "kind", "note", "review_on", "origin", "step"
            )
        }
        today = datetime.now(UTC).date()
        if values["step"] == "check" or not values["kind"]:
            self._render_mark_form(values, today)
            return
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        try:
            first = _need_date(values["first"], optional=True)
            last = _need_date(values["last"])
            review_on = _need_date(values["review_on"], optional=True)
        except MarkRefused as bad:
            self._marks_refused(str(bad), values)
            return
        if last is None:
            self._marks_refused("A last day is needed.", values)
            return
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            made = make_mark(
                store, world,
                account=values["account"], source=values["source"], kind=values["kind"],
                first_day=first, last_day=last, note=values["note"], review_on=review_on,
                origin=values["origin"] or "owner", now=stamp, today=today,
                statement_sources=STATEMENT_SOURCES,
            )
            reading = read_mark(made, world, today, statement_sources=STATEMENT_SOURCES)
            meaning = KINDS[made.kind]
            said = (
                f"Set aside as {meaning.label.lower()}: {made.account}, "
                f"{span_words(made.first_day, made.last_day)}."
            )
            if made.kind is MarkKind.KNOWN_GAP:
                return (
                    said + " The gap is no longer listed to fetch, and the data is still missing."
                )
            if reading.standing is Standing.CONTRADICTED:
                return (
                    said + " What is held disagrees with it, so the gap stays in the list until "
                    "you change or remove this mark."
                )
            return said

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), values)
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._gaps_names()
        self._marks_page(
            200,
            "Period set aside",
            _ok_html(sentence, names),
        )

    def _gaps_mark_undo_post(self, form: Mapping[str, list[str]]) -> None:
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        text = (form.get("id", [""])[0] or "").strip()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            if not text.isdigit():
                raise MarkRefused("That is not a mark this page gave you.")
            removed = remove_mark(store, int(text), stamp)
            return (
                f"Removed the mark: {KINDS[removed.kind].label.lower()} for {removed.account}, "
                f"{span_words(removed.first_day, removed.last_day)}. Whatever it set aside is "
                "listed again."
            )

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), {})
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._gaps_names()
        self._marks_page(
            200,
            "Mark removed",
            _ok_html(sentence, names),
        )

    def _gaps_scope_post(self, form: Mapping[str, list[str]]) -> None:
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        account = (form.get("account", [""])[0] or "").strip()
        mode = (form.get("mode", [""])[0] or "").strip()
        months_text = (form.get("months", [""])[0] or "").strip()
        first_text = (form.get("first", [""])[0] or "").strip()
        today = datetime.now(UTC).date()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            who = account or "every account"
            if mode == "clear":
                store.clear_record_scope(account)
                return f"You now keep all of the past for {who}."
            if mode not in ("rolling", "fixed"):
                raise MarkRefused("Choose how far back you keep: a number of months or a day.")
            months = None
            first = None
            if mode == "rolling":
                if not months_text.isdigit():
                    raise MarkRefused("Say how many months to keep, as a whole number.")
                months = int(months_text)
            else:
                first = _need_date(first_text, what="A first day")
            set_scope(store, world, account=account, first_day=first, months=months, now=stamp)
            kept = scopes_in(store)[account]
            return f"You now keep {who} {kept.describe(today)}; earlier days are not looked for."

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), {})
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._gaps_names()
        self._marks_page(
            200,
            "How far back you keep",
            _ok_html(sentence, names),
        )


_BACK_TO_GAPS = '<p><a class="button" href="/gaps">Back to what to fetch next</a></p>'


def _ok_html(sentence: str, names: Mapping[str, str]) -> str:
    """The one sentence that says what happened, with account labels as every page writes them,
    and the way back."""
    return f'<p class="ok"><strong>{_esc(name_text(sentence, names))}</strong></p>{_BACK_TO_GAPS}'


def _try_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _need_date(text: str, *, optional: bool = False, what: str = "A last day") -> date | None:
    """A day written like 2026-10-05, or None where it is optional and empty."""
    if not text and optional:
        return None
    found = _try_date(text)
    if found is None:
        raise MarkRefused(
            "A day must be written like 2026-10-05." if text else f"{what} is needed."
        )
    return found
