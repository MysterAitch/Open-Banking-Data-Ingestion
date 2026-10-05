"""The words and markup for what the owner has set aside on the What to fetch next page.

Served on a GET, so dates, account names, source names, counts, and the owner's own note only:
no amount and no description. THE NOTE is words he typed about a period, like an account's label,
and is shown as typed (escaped, and capped at `fetch_marks.NOTE_LIMIT`); the form says to write
words and not figures, because a figure typed there would be shown to anyone who can open the
page.

THE KINDS ARE TOLD APART in every sentence here by reading `fetch_marks.KINDS`, which is the one
statement of what each means. A known gap asserts nothing and shows no evidence; the kinds that
make a claim show what the store holds BEFORE the owner confirms.
"""

from __future__ import annotations

import html
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from urllib.parse import urlencode

from .account_names import name_html
from .fetch_marks import (
    AGGREGATOR,
    CLAIM_KINDS,
    KINDS,
    NOTE_LIMIT,
    ORIGIN_SOURCE,
    Evidence,
    MarkKind,
    MarkSet,
    NotAsked,
    OutOfScope,
    ReachOffer,
    Reading,
    Scope,
    SetAside,
    Standing,
)
from .plural import agree, plural

_esc = html.escape

#: The kinds in the order they are offered: those that make a claim the store can weigh first.
FORM_KINDS = (*CLAIM_KINDS, MarkKind.KNOWN_GAP, MarkKind.OTHER)

_STANDING_WORDS = {
    Standing.SUPPORTED: "supported by what is held",
    Standing.CONTRADICTED: "contradicted by what is held",
    Standing.SATISFIED: "no longer needed",
    Standing.UNTESTED: "nothing held can test it",
}


def source_words(source: str) -> str:
    """A source as the Bring in page names it: the aggregator, the bank's own feed, or the
    export's own name."""
    if not source:
        return "any statement source"
    if source.startswith(AGGREGATOR):
        return "the aggregator"
    if source.startswith("starling-feed"):
        return "the bank's own feed"
    return source


def origin_words(origin: str) -> str:
    return "from the source's own answer" if origin == ORIGIN_SOURCE else "your decision"


def span_words(first: date | None, last: date) -> str:
    if first is None:
        return f"everything up to {last.isoformat()}"
    return f"{first.isoformat()} to {last.isoformat()}"


def _listed(by_source: Sequence[tuple[str, int]]) -> str:
    counts: Counter[str] = Counter()
    for source, count in by_source:
        counts[source_words(source)] += count
    return " and ".join(f"{name} lists {plural(n, 'row')}" for name, n in sorted(counts.items()))


def _rows_span(ev: Evidence) -> str:
    if ev.first_row is None or ev.last_row is None:
        return ""
    return f", from {ev.first_row.isoformat()} to {ev.last_row.isoformat()}"


def evidence_text(
    kind: MarkKind,
    source: str,
    first: date | None,
    last: date,
    ev: Evidence,
    standing: Standing,
    reason: str,
    *,
    marked: bool,
) -> str:
    """What the store says about a claim, in plain sentences with counts and dates only.

    `marked` is whether the owner has already made it, which changes only the tense: before it,
    "would disagree"; after, "you marked".
    """
    if kind is MarkKind.KNOWN_GAP or kind is MarkKind.OTHER:
        return ""
    named = source_words(source)
    if standing is Standing.SATISFIED:
        if reason == "source-provides":
            return f"{named} lists {plural(ev.own_rows, 'row')} in this period after all."
        return (
            "A statement is held for part of this period now, so there was something to fetch "
            "after all."
        )
    if kind is MarkKind.NOTHING_TO_FETCH:
        if standing is Standing.CONTRADICTED:
            lead = (
                "You marked this period as having no transactions; "
                if marked
                else "Saying there were no transactions would disagree with what is held: "
            )
            return f"{lead}{_listed(ev.by_source)} in it{_rows_span(ev)}."
        text = "No source lists a row in this period."
        if ev.chain is True:
            text += (
                " The statement after it opens on the balance the one before closed on, which "
                "fits a period with nothing in it and does not prove it."
            )
        elif ev.chain is False:
            text += (
                " The statements either side do not join: the one after opens on a different "
                "balance from the one before's close, so something may have moved."
            )
        return text
    if kind is MarkKind.BEFORE_HISTORY:
        before = (last + timedelta(days=1)).isoformat()
        if standing is Standing.CONTRADICTED:
            lead = (
                f"You marked everything before {before} as before {named}'s history; "
                if marked
                else f"Saying {named}'s history begins at {before} would disagree with what "
                "is held: "
            )
            own = ev.own_rows
            return (
                f"{lead}{named} lists {plural(own, 'row')} before it"
                f"{_rows_span(ev)}."
            )
        reach = ev.reach
        if reason == "not-asked":
            asked = (
                f"It has only been asked back to {reach.asked_back_to.isoformat()}."
                if reach is not None and reach.asked_back_to is not None
                else "It has never been asked."
            )
            return (
                f"{named} has not been asked for days this early, so nothing yet shows it "
                f"holds none. {asked}"
            )
        if reason == "asked-empty" and reach is not None:
            said = []
            if reach.boundary is not None:
                said.append(
                    f"The provider itself says its history is cut at {reach.boundary.isoformat()}."
                )
            if reach.asked_back_to is not None:
                said.append(
                    f"It was asked for days back to {reach.asked_back_to.isoformat()} and lists "
                    f"no row before {before}."
                )
            return " ".join(said)
        return f"{named} lists no row before {before}."
    if kind is MarkKind.NOT_OPEN:
        if standing is Standing.CONTRADICTED:
            if reason == "declared-open":
                opened = "the beginning" if ev.declared_opened is None else (
                    ev.declared_opened.isoformat()
                )
                closed = "" if ev.declared_closed is None else (
                    f" to {ev.declared_closed.isoformat()}"
                )
                return (
                    f"The account is declared open from {opened}{closed}, which reaches into "
                    "this period."
                )
            lead = (
                "You marked the account as not open in this period; "
                if marked
                else "Saying the account was not open would disagree with what is held: "
            )
            return f"{lead}{_listed(ev.by_source)} in it{_rows_span(ev)}."
        if reason == "declared-dates":
            closed = "" if ev.declared_closed is None else f" to {ev.declared_closed.isoformat()}"
            opened = "the beginning" if ev.declared_opened is None else (
                ev.declared_opened.isoformat()
            )
            return (
                f"The account is declared open from {opened}{closed}; this period is outside "
                "that."
            )
        if reason == "before-first-row":
            return "The account holds no row dated before this period ends."
        if reason == "after-last-row":
            return "The account holds no row dated after this period begins."
        return (
            "No opening or closing day is declared and the account holds rows either side, so "
            "nothing held can test this."
        )
    # What remains is NO_LONGER_PROVIDED: the others that reach the period are named.
    if ev.others:
        named_others = sorted({source_words(o) for o in ev.others})
        verb = "still lists" if len(named_others) == 1 else "still list"
        return (
            f"{' and '.join(named_others)} {verb} rows in this period, so it can still be "
            "had from there."
        )
    return "No other source lists a row in this period."


def changed_text(reading: Reading) -> str:
    """Whether what a mark covers differs from what it covered when it was made."""
    if not reading.changed:
        return ""
    import json

    try:
        then = json.loads(reading.mark.evidence).get("rows", 0)
    except (ValueError, AttributeError):
        then = 0
    now = reading.evidence.rows
    return (
        "What this covers has changed since you marked it: "
        f"then {plural(int(then), 'row')} {agree(int(then), 'was')} listed in it, now "
        f"{plural(now, 'row')}."
    )


def verdict_clauses(
    set_aside: Sequence[SetAside],
    out_of_scope: Sequence[OutOfScope],
    contradicted: int,
) -> list[str]:
    """The clauses after the count of things to fetch: each kind set aside, by how many."""
    counts = Counter(item.kind for item in set_aside)
    clauses = [KINDS[kind].counted(counts[kind]) for kind in KINDS if counts[kind]]
    if out_of_scope:
        clauses.append(f"{len(out_of_scope)} before your record begins")
    if contradicted:
        clauses.append(
            f"{plural(contradicted, 'mark')} {agree(contradicted, 'is')} contradicted by what "
            "is held"
        )
    return clauses


def hidden(**fields: str) -> str:
    return "".join(
        f'<input type="hidden" name="{_esc(name)}" value="{_esc(value)}">'
        for name, value in fields.items()
    )


def mark_query(
    account: str, source: str, first: date | None, last: date | None, **more: str
) -> str:
    params = {
        "account": account,
        "source": source,
        "first": first.isoformat() if first else "",
        "last": last.isoformat() if last else "",
        **more,
    }
    return "/gaps-mark?" + urlencode({k: v for k, v in params.items() if v})


def _undo_form(mark_id: int, label: str) -> str:
    return (
        f'<form method="post" action="/gaps-mark-undo" class="gaps-undo">'
        f'{hidden(id=str(mark_id))}<button class="tap" type="submit">{_esc(label)}</button></form>'
    )


def _outside_record(reading: Reading, scope: Scope | None, today: date) -> str:
    if scope is None or reading.mark.last_day >= scope.starts(today):
        return ""
    return (
        "This period is before the day your record begins, so it is kept and does nothing until "
        "your record reaches back to it."
    )


def contradicted_html(marks: MarkSet, names: Mapping[str, str], today: date) -> str:
    """Contradicted marks, where they cannot be missed: beside the gap each one failed to hide."""
    items = []
    for reading in marks.contradicted:
        mark = reading.mark
        said = evidence_text(
            mark.kind, mark.source, mark.first_day, mark.last_day, reading.evidence,
            reading.standing, reading.reason, marked=True,
        )
        items.append(
            '<li class="gaps-contradicted">'
            f"<p>{name_html(mark.account, names)}: <strong>{_esc(KINDS[mark.kind].label)}</strong> "
            f'for <span class="mono">{_esc(span_words(mark.first_day, mark.last_day))}</span>.</p>'
            f"<p>{_esc(said)} The gap stays in the list until you change or remove the mark.</p>"
            f"{_undo_form(mark.id, 'Remove this mark')}</li>"
        )
    if not items:
        return ""
    return f'<ul class="gaps-contradictions">{"".join(items)}</ul>'


def _mark_item(
    reading: Reading,
    names: Mapping[str, str],
    scope: Scope | None,
    today: date,
    set_aside: Sequence[SetAside],
) -> str:
    mark = reading.mark
    said = evidence_text(
        mark.kind, mark.source, mark.first_day, mark.last_day, reading.evidence,
        reading.standing, reading.reason, marked=True,
    )
    note = (
        f'<p class="gaps-note">{_esc(mark.note)}</p>' if mark.note else ""
    )
    taken = sum(1 for s in set_aside if s.reading is not None and s.reading.mark.id == mark.id)
    effect = (
        f"It has taken {plural(taken, 'thing')} out of the list."
        if taken
        else "It takes nothing out of the list at the moment."
    )
    later = (
        f" Look again on {mark.review_on.isoformat()}."
        if mark.review_on is not None
        else ""
    )
    quiet = " ".join(
        part
        for part in (changed_text(reading), _outside_record(reading, scope, today))
        if part
    )
    standing = (
        ""
        if not KINDS[mark.kind].claims
        else f'<span class="pill gaps-{reading.standing.value}">'
        f"{_esc(_STANDING_WORDS[reading.standing])}</span>"
    )
    return (
        f'<li class="gaps-mark gaps-mark-{reading.standing.value}">'
        f'<p class="gaps-head"><span class="gaps-kind">{_esc(KINDS[mark.kind].label)}</span> '
        f'{standing} <span class="pill">{_esc(origin_words(mark.origin))}</span></p>'
        f"<p>{name_html(mark.account, names)} "
        f'<span class="gaps-source">({_esc(source_words(mark.source))})</span></p>'
        f'<p class="gaps-range mono">{_esc(span_words(mark.first_day, mark.last_day))}</p>'
        f"{note}"
        + (f'<p class="gaps-why">{_esc(said)}</p>' if said else "")
        + (f'<p class="gaps-why">{_esc(quiet)}</p>' if quiet else "")
        + f'<p class="gaps-why">Made {_esc(mark.made_at[:10])}. {_esc(effect)}{_esc(later)}</p>'
        + _undo_form(mark.id, "Undo")
        + "</li>"
    )


def _scope_line(scope: Scope, account: str, names: Mapping[str, str], today: date,
                known: Mapping[str, date]) -> str:
    who = "every account" if not account else name_html(account, names)
    start = scope.starts(today)
    said = (
        f"You keep {who} {_esc(scope.describe(today))}; earlier days are not looked for. "
        "Rows before it are still held and still tested."
    )
    first_known = known.get(account) if account else None
    if first_known is not None and first_known < start:
        said += (
            f" Its first known balance, {first_known.isoformat()}, is before that day and "
            "still carries the rows after it."
        )
    return f'<li class="gaps-scope-line">{said}</li>'


def scope_lines_html(
    marks: MarkSet, names: Mapping[str, str], today: date, known: Mapping[str, date]
) -> str:
    lines = "".join(
        _scope_line(scope, account, names, today, known)
        for account, scope in sorted(marks.scopes.items())
    )
    return f'<ul class="gaps-scope-lines muted">{lines}</ul>' if lines else ""


def _out_of_scope_line(item: OutOfScope, names: Mapping[str, str]) -> str:
    if item.left_on is not None:
        said = (
            f"left your {item.scope.months} months on {item.left_on.isoformat()} unfilled"
        )
    else:
        said = f"is before {item.scope.starts(item.last_day).isoformat()}, where your record begins"
    return (
        f'<li class="gaps-quiet-item">{name_html(item.account, names)} '
        f'<span class="mono">{_esc(span_words(item.first_day, item.last_day))}</span> '
        f'<span class="muted">- {_esc(said)}</span></li>'
    )


def scope_form_html(names: Mapping[str, str], accounts: Sequence[str]) -> str:
    options = '<option value="">Every account (the household default)</option>' + "".join(
        f'<option value="{_esc(ref)}">{_esc(names.get(ref) or ref)} ({_esc(ref)})</option>'
        if names.get(ref) and names.get(ref) != ref
        else f'<option value="{_esc(ref)}">{_esc(ref)}</option>'
        for ref in accounts
    )
    return (
        '<form method="post" action="/gaps-scope" class="gaps-form">'
        '<p class="muted">Choose how much of the past you keep. Days before it are not looked '
        "for, and no gap is reported there. Rows before it are still held and still tested, "
        "and widening it later brings every gap back that it hid.</p>"
        f'<label class="gaps-field">For <select name="account">{options}</select></label>'
        '<fieldset class="gaps-choice"><legend>How far back</legend>'
        '<label class="tick"><input type="radio" name="mode" value="rolling" checked>'
        '<span>The last <input type="number" name="months" min="1" max="600" '
        'inputmode="numeric" class="gaps-short"> months, moving on each day</span></label>'
        '<label class="tick"><input type="radio" name="mode" value="fixed">'
        '<span>From a fixed day <input type="date" name="first"></span></label>'
        '<label class="tick"><input type="radio" name="mode" value="clear">'
        "<span>Keep everything (remove the limit)</span></label></fieldset>"
        '<p><button class="button" type="submit">Save how far back I keep</button></p></form>'
    )


def folded_html(
    marks: MarkSet,
    set_aside: Sequence[SetAside],
    out_of_scope: Sequence[OutOfScope],
    names: Mapping[str, str],
    accounts: Sequence[str],
    today: date,
) -> str:
    """The folded section: every decision with its evidence and its Undo, and what was cut."""
    count = len(set_aside) + len(out_of_scope)
    items = "".join(
        _mark_item(r, names, marks.scope_for(r.mark.account), today, set_aside)
        for r in marks.readings
    )
    derived = [s for s in set_aside if s.reading is None]
    derived_html = "".join(
        f'<li class="gaps-quiet-item">{name_html(s.account, names)} '
        f'<span class="mono">{_esc(span_words(s.first_day, s.last_day))}</span> '
        '<span class="muted">- before the account opened, by its declared dates</span></li>'
        for s in derived
    )
    cut = "".join(_out_of_scope_line(o, names) for o in out_of_scope)
    return (
        f'<details class="gaps-aside"><summary>Set aside by your decision ({count})</summary>'
        + (f'<ul class="gaps-list">{items}</ul>' if items else "")
        + (f'<ul class="gaps-quiet">{derived_html}</ul>' if derived_html else "")
        + (
            f'<h3 class="gaps-sub">Before your record begins</h3><ul class="gaps-quiet">{cut}</ul>'
            if cut
            else ""
        )
        + '<h3 class="gaps-sub">How far back you keep</h3>'
        + scope_form_html(names, accounts)
        + '<p class="gaps-links"><a class="tap" href="/gaps-mark?new=1">'
        "Flag a gap the page has not found</a></p></details>"
    )


def offers_html(marks: MarkSet, names: Mapping[str, str]) -> str:
    """The aggregator's own answers, as marks ready to accept, and where it has not been asked."""
    if not marks.offers and not marks.not_asked:
        return ""
    parts = []
    for offer in marks.offers:
        parts.append(_offer(offer, names))
    for unasked in marks.not_asked:
        parts.append(_not_asked(unasked, names))
    return (
        '<section class="gaps-reach"><h2 class="gaps-quiet-head">The aggregator\'s reach</h2>'
        f"{''.join(parts)}</section>"
    )


def _offer(offer: ReachOffer, names: Mapping[str, str]) -> str:
    reach = offer.reach
    basis = []
    if reach.boundary is not None:
        basis.append(f"the provider says its history is cut at {reach.boundary.isoformat()}")
    if reach.asked_back_to is not None and reach.asked_back_to < offer.first_row:
        basis.append(
            f"a request for days back to {reach.asked_back_to.isoformat()} came back with no rows"
        )
    return (
        '<form method="post" action="/gaps-mark" class="gaps-offer">'
        f"<p>{name_html(offer.account, names)}: the aggregator's history begins "
        f"{offer.first_row.isoformat()}; {_esc(' and '.join(basis))}. "
        f"Mark everything before {offer.first_row.isoformat()} as before its history?</p>"
        + hidden(
            account=offer.account, source=offer.source, last=offer.last_day.isoformat(),
            kind=MarkKind.BEFORE_HISTORY.value, origin=ORIGIN_SOURCE, step="mark",
        )
        + '<button class="button secondary" type="submit">'
        f"Mark everything before {offer.first_row.isoformat()}</button></form>"
    )


def _not_asked(item: NotAsked, names: Mapping[str, str]) -> str:
    return (
        f'<p class="muted gaps-reach-line">{name_html(item.account, names)}: the aggregator\'s '
        f"history begins {item.first_row.isoformat()}. It has not been asked for earlier days, "
        "so nothing is offered.</p>"
    )


def gap_actions_html(account: str, gap_source: str, gap_first: date, gap_last: date) -> str:
    """The two ways to set a gap aside: acknowledge it in one tap, or say something about it."""
    source = "" if gap_source in _statement_sources() else gap_source
    return (
        '<div class="gaps-decide">'
        '<form method="post" action="/gaps-mark" class="gaps-ack">'
        + hidden(
            account=account, source=source, first=gap_first.isoformat(),
            last=gap_last.isoformat(), kind=MarkKind.KNOWN_GAP.value, step="mark",
        )
        + '<button class="tap" type="submit">Acknowledge this gap</button></form>'
        f'<a class="tap" href="{_esc(mark_query(account, source, gap_first, gap_last))}">'
        "Nothing to fetch for this period...</a></div>"
    )


def _statement_sources() -> frozenset[str]:
    from .fetch_gaps import STATEMENT_SOURCES

    return STATEMENT_SOURCES


def reading_for(kind: MarkKind, previews: Mapping[MarkKind, tuple[Standing, str, Evidence]]) -> str:
    return previews[kind][0].value if kind in previews else ""


def form_html(
    *,
    account: str,
    source: str,
    first: str,
    last: str,
    kind: str,
    note: str,
    review_on: str,
    today: date,
    names: Mapping[str, str],
    accounts: Sequence[str],
    sources: Sequence[str],
    previews: Mapping[MarkKind, tuple[Standing, str, Evidence]],
    parsed_last: date | None,
    parsed_first: date | None,
) -> str:
    """The form, with what the store says about the period beside each kind that makes a claim."""
    if account:
        who = f"<p class=\"lede\">{name_html(account, names)}, {_esc(source_words(source))}.</p>"
        chosen = hidden(account=account, source=source)
    else:
        options = "".join(
            f'<option value="{_esc(ref)}">{_esc(names.get(ref) or ref)}</option>'
            for ref in accounts
        )
        sources_options = '<option value="">Any statement source</option>' + "".join(
            f'<option value="{_esc(s)}">{_esc(source_words(s))} ({_esc(s)})</option>'
            if source_words(s) != s
            else f'<option value="{_esc(s)}">{_esc(s)}</option>'
            for s in sources
        )
        who = ""
        chosen = (
            f'<label class="gaps-field">Account <select name="account">{options}</select></label>'
            f'<label class="gaps-field">Source <select name="source">{sources_options}</select>'
            "</label>"
        )
    radios = []
    picked = kind if kind in {k.value for k in FORM_KINDS} else FORM_KINDS[0].value
    for item in FORM_KINDS:
        meaning = KINDS[item]
        evidence_line = ""
        if item in previews and parsed_last is not None:
            standing, reason, ev = previews[item]
            text = evidence_text(
                item, source, parsed_first, parsed_last, ev, standing, reason, marked=False
            )
            if text:
                evidence_line = (
                    f'<span class="gaps-evidence gaps-{standing.value}">{_esc(text)}</span>'
                )
        radios.append(
            f'<label class="gaps-kindrow"><input type="radio" name="kind" value="{item.value}"'
            f'{" checked" if item.value == picked else ""}>'
            f"<span><strong>{_esc(meaning.label)}</strong> "
            f'<span class="gaps-asserts">{_esc(meaning.asserts)}</span>'
            f"{evidence_line}</span></label>"
        )
    return (
        f"{who}"
        '<p class="muted">Setting a period aside answers "should I go and fetch something?" It '
        "changes nothing about what is verified: a period with no statement still has no known "
        "balance.</p>"
        '<form method="post" action="/gaps-mark" class="gaps-form">'
        f"{chosen}"
        '<div class="gaps-dates">'
        f'<label class="gaps-field">First day<input type="date" name="first" value="{_esc(first)}">'
        "</label>"
        f'<label class="gaps-field">Last day<input type="date" name="last" value="{_esc(last)}" '
        f'max="{today.isoformat()}" required></label></div>'
        '<p class="muted">Leave the first day empty to reach back for ever. The last day cannot '
        "be after today.</p>"
        f'<fieldset class="gaps-choice"><legend>What is true of this period</legend>'
        f'{"".join(radios)}</fieldset>'
        f'<label class="gaps-field">Note, in your words (needed for Other)'
        f'<textarea name="note" rows="2" maxlength="{NOTE_LIMIT}">{_esc(note)}</textarea></label>'
        '<p class="muted">Write words, not figures: this page shows the note as typed.</p>'
        '<label class="gaps-field">Look again on (a known gap only)'
        f'<input type="date" name="review_on" value="{_esc(review_on)}" '
        f'min="{(today + timedelta(days=1)).isoformat()}"></label>'
        '<p><button class="button secondary" type="submit" name="step" value="check">'
        "Check what is held for these dates</button></p>"
        '<p><button class="button" type="submit" name="step" value="mark">'
        "Set this period aside</button></p></form>"
    )


__all__ = [
    "FORM_KINDS",
    "changed_text",
    "contradicted_html",
    "evidence_text",
    "folded_html",
    "form_html",
    "gap_actions_html",
    "mark_query",
    "offers_html",
    "scope_lines_html",
    "source_words",
    "verdict_clauses",
]
