"""The account ledger page, in both of its renderings.

A GET renders MASKED. Showing values takes a POST, answered directly with the
unmasked page and never redirected, so no address a person can bookmark, paste
into a note, or fetch from a script holds a value. The unmasked response is
sent `no-store` so a browser history does not either.

The decision about what a reader may see is not made in this file. Every record
is wrapped in `masking.Disclosed` at the top of `render_ledger`, and from there
down only the wrapped view is in reach, so there is no line below that could
print a value without having been through it. A value field that the page needs
to show unmasked is shown unmasked because the VIEW was built with
`unmasked=True`, and for no other reason.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .account_names import AccountShown, code_html
from .account_page import (
    LOCKING_ANCHOR,
    AccountReading,
    cannot_lock_yet_html,
    head_html,
    hold_is_said,
    lock_offer_html,
    read_account,
    strip_html,
    todos_html,
    trust_html,
)
from .agreement import (
    HELD_MOVEMENT,
    HELD_STATEMENT,
    STRETCH_MEANINGS,
    closed_before_sentence,
    date_difference_sentence,
    held_sentence,
    listing_tested_sentence,
    stretch_sentences,
)
from .balance_anchors import parse_calendar_day
from .balance_chart import OWN
from .balance_meaning import READING_THRESHOLD
from .bank_balances import BANK_SOURCE
from .callback import render_page
from .errors import DataError
from .feed_item_shape import MIN_COMPARABLE, THRESHOLDS, differs
from .join_basis import COUNT_LABELS, count_sentence, how_words, moment_text, word_text
from .ledger import (
    ANCHOR_QUERIES,
    FAMILY_QUERIES,
    FEED_TIME_QUERIES,
    QUERIES_PER_PAGE,
    Ledger,
    LedgerRequestError,
)
from .ledger_scope import (
    DEFAULT_KEY,
    DEFAULTABLE,
    EXTRA_CHIPS,
    FIRST_TAP,
    MONTH_KEY,
    OMITTED,
    is_window_token,
    label_of,
    query_of,
    read_scope,
)
from .logs import say
from .london_clock import london
from .masking import MASKED_TOTAL, Disclosed
from .models import BASIS_ID
from .navigation import account_address, page_name
from .page_words import (
    ACCOUNT_CHECK_HEADING,
    PROTECTION_REMOVED,
    REMOVE_PROTECTION,
    REMOVE_TYPED_TRANSACTION,
    TYPED_TRANSACTION_REMOVED,
)
from .plural import agree, word
from .plural import plural as _plural
from .standing_data import ADDS_UP, DOES_NOT_ADD_UP, verification_of
from .trust_bar import key_html
from .web_accounts import archive_controls, submit_button
from .web_answers import AnswerPages
from .web_balance_chart import structure_summary_html
from .web_standing import _post, _through, line_html
from .window_control import WINDOW_FIELDS, WindowChoice, window_controls

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    # Only the annotation is needed, and importing the handler's module at
    # runtime would close a cycle: web.py composes this module in.
    from .web import WebConfig

_HOME = '<p><a class="tap" href="/">Back to overview</a></p>'

#: The newest this many agreeing known balances are listed on the account page and the rest are a
#: count with a link to the full list (`_listed_anchors`). Reading the deployed main account
#: showed 1,906 balances, all in agreement, listed in full on every load: 5,700 of the page's
#: 6,100 text lines. Ten rows of three lines is about a screenful on a phone, enough to lay
#: against the bank's app; the opening's own balance and every one that differs are always listed.
_SHOWN_AGREEING_ANCHORS = 10

#: Earlier dates a protection can be pressed through that the drop-down offers before the full list.
_OFFERED_DATES = 24

#: The account page's parameter that asks for every known balance, and its one value.
BALANCES_PARAM = "balances"
BALANCES_ALL = "all"

#: The id of the known-balances section, which the held-back box and the full list link into.
OPENING_ANCHOR = "opening"

_NOTHING_SENT = "Nothing in this account is sent to Actual, so there is nothing to compare."

_esc = html.escape


def _seal(unmasked: bool) -> str:
    """The class that tells a masked value from a shown one at a glance.

    Added at this page's own call sites and never inside `masking`, so every other page's bytes
    are what they were. The characters themselves are untouched: the class only lets the
    stylesheet draw a masked figure or text as a sealed slot.
    """
    return "" if unmasked else " sealed"


def _disclosure(
    summary: str, body: str, *, open: bool = False, anchor: str = "", css: str = ""
) -> str:
    """One folded section: a summary that says what it holds, and what it holds.

    `summary` is HTML the caller has already escaped, so it can carry a count in a span.
    """
    ident = f' id="{_esc(anchor)}"' if anchor else ""
    klass = f' class="{_esc(css)}"' if css else ""
    opened = " open" if open else ""
    return f"<details{ident}{klass}{opened}><summary>{summary}</summary>{body}</details>"


def _part(summary: str, body: str) -> str:
    """One part of a fold that holds several: a heading and its body, with no fold of its own, so
    that the page's folds are the five the account page keeps and no more."""
    return f'<section class="part"><h3>{summary}</h3>{body}</section>'


def _url(path: str, **params: str) -> str:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return _esc(f"{path}?{query}")


def _scope(view: Any) -> str:
    """The value of the hidden `month` field every form on the page carries, which says which
    days are on show (`ledger_scope`). A ledger that was not built for a request holds none, and
    is on show by its month."""
    return str(view.scope if view.scope or view.window_words else view.month)


def _count(label: str, value: object) -> str:
    return f"<tr><th>{_esc(label)}</th><td>{_esc(str(value))}</td></tr>"


def _count_html(label: str, value_html: str) -> str:
    """A row of the counts table whose value is markup already (sources set as code)."""
    return f"<tr><th>{_esc(label)}</th><td>{value_html}</td></tr>"


def _pairs(items: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{name}: {number}" for name, number in items) or "none"


def _source_pairs(items: tuple[tuple[str, int], ...]) -> str:
    """`_pairs` for counts by source, whose names are identifiers: markup, set as code."""
    return ", ".join(f"{code_html(name)}: {number}" for name, number in items) or "none"


def _sources_html(sources: Iterable[str]) -> str:
    return ", ".join(code_html(source) for source in sources)


def _flag(text: str, title: str, css: str = "pill-quiet", *, text_is_html: bool = False) -> str:
    shown = text if text_is_html else _esc(text)
    return f'<span class="pill {css}" title="{_esc(title)}">{shown}</span> '


def _differ(differs: bool) -> str:
    """The verb for plural subjects, which is what both sentences use."""
    return "differ" if differs else "agree"


def _direction_word(direction: str) -> str:
    return {"in": "net in", "out": "net out"}.get(direction, "nil")


def _signed(word: str, direction: str, amount: str) -> str:
    """A direction and its figure, or "nil" alone when there is no direction.

    A nil figure printed after "nil" reads as an amount, and on a masked page
    it is a row of nines.
    Every place the page prints a direction beside a figure goes through here.
    """
    return "nil" if direction == "nil" else f"{word} {amount}"


def _words(items: list[str]) -> str:
    """A list in running prose, with the serial comma."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


#: What a folded row is called wherever the page describes one: on the row, in the month's
#: line, and over the closed list at the foot of the month. A folded row is a copy of money
#: counted elsewhere, withheld from Actual so it is not counted twice (`replay.WITHHELD_FOLDED`
#: gives the two cases), so it is history and not a fault.
_COPY_CHIP = "counted elsewhere"
_COPIES_WHY = "held in a Space, or listed by a statement"


def _copies(count: int) -> str:
    """A count of copies with its noun, which names what they are: "3 transactions counted
    elsewhere"."""
    return _plural(count, "transaction counted elsewhere", "transactions counted elsewhere")


def _is_copy(row: Any) -> bool:
    """True for a row folded into another, which no sum counts."""
    return str(row.status) == "folded"


def _row_flags(row: Any) -> str:
    """The pills for what is unusual about a row, or nothing when nothing is.

    A copy is quiet: it is neither unverified nor in disagreement, so the warnings that ask a
    person to look at a counted row are left off it, and its one chip is the status pill.
    """
    flags = ""
    if row.cleared_by:
        flags += _flag(
            f"cleared by {_sources_html(row.cleared_by)}",
            "An authoritative listing of the account lists this row: a statement, an export, "
            "or the bank's own feed. The aggregator alone does not clear a row.",
            "pill-ok",
            text_is_html=True,
        )
    if row.origin == "typed":
        flags += _flag(
            "typed",
            "A person typed this transaction. It is evidence like any other, and "
            "can be removed from the typed transactions list below.",
        )
    elif row.origin == "unitemised":
        flags += _flag(
            "unitemised change",
            "Derived from the account's known balances: the difference between two "
            "of them that no row explains. It is never stored, so restating a "
            "balance changes it.",
        )
    if row.one_source and not _is_copy(row):
        flags += _flag(
            "one source only",
            "More than one source feeds this account and only one of them has "
            "reported this transaction, so no other source confirms it yet.",
            "pill-warn",
        )
    if row.transfer == "confirmed":
        flags += _flag(
            f"transfer with {row.transfer_other_account}",
            "A confirmed internal transfer: the other side is held in that account.",
        )
    elif row.transfer == "claimed":
        flags += _flag(
            "transfer?",
            "The provider calls this an internal transfer but the other side is "
            "not held in any account.",
            "pill-bad",
        )
    if row.review_open:
        flags += _flag(
            "review",
            "An open review flag asks a person to decide about this row.",
            "pill-bad",
        )
        flags += f'<span class="muted">{_esc(row.review_reason)}</span> '
    if row.withheld and not _is_copy(row):
        flags += _flag(
            f"withheld from Actual: {row.withheld}",
            "Actual is not sent this row, for the reason given.",
        )
    if row.unsendable:
        flags += _flag(
            "push would fail",
            f"The push builder refuses this row, which fails the whole push: "
            f"{row.send_refusal}",
            "pill-bad",
        )
    if row.shares_identity:
        flags += _flag(
            "shares identity",
            "Another row in this account has the same content key and occurrence, "
            "so Actual could not tell the two apart.",
            "pill-bad",
        )
    if row.absorbed_ids:
        flags += _flag(
            f"absorbed {row.absorbed_ids} ids",
            f"One source reported this payment under {row.absorbed_ids} different "
            "provider ids outside a pending snapshot, so another payment may have "
            "been folded into this row.",
            "pill-bad",
        )
    return flags


def _status_pill(row: Any) -> str:
    """The row's status as a chip.

    A booked row that a listing clears is told by its "cleared by" chip alone, since a pending
    row is never cleared (`clearing`); its own chip stays in the page for a screen reader and is
    left off the screen, which is what lets a row's date and chips share one line on a phone.
    """
    status = row.status
    if _is_copy(row):
        return (
            f'<span class="pill pill-quiet" title="Actual is not sent this transaction, and no '
            f"sum counts it here: the same payment is already counted, {_esc(_COPIES_WHY)}."
            f'">{_COPY_CHIP}</span>'
        )
    css = {"booked": "pill-ok", "void": "pill-bad"}.get(status, "pill-quiet")
    implied = " visually-hidden" if status == "booked" and row.cleared_by else ""
    return f'<span class="pill {css}{implied}">{_esc(status)}</span>'


def _sighting_line(sighting: Any) -> str:
    """One source's sighting: how it came to be on the row, then everything it stated."""
    how = how_words(sighting)
    stated = ", ".join(
        f"{_esc(moment.field)} {_esc(moment_text(moment))}" for moment in sighting.moments
    )
    tail = f": {stated}" if stated else ""
    if sighting.words:
        tail += f"{'; ' if stated else ': '}says {_esc(word_text(sighting.words))}"
    return (
        f'<p class="muted">{code_html(sighting.source)} - {_esc(how)}{tail}</p>'
        f"{_artefact_html(sighting)}"
    )


def _artefact_html(sighting: Any) -> str:
    """Where the sighting above it came from: the day its capture was made, and a link to the
    artefact's page where the store holds one. A sighting with neither says nothing of it. A
    line of its own, so the sighting's own line keeps what it has always said."""
    parts = []
    if sighting.captured:
        parts.append(f"captured {_esc(sighting.captured)}")
    if sighting.artefact:
        parts.append(
            f'<a class="tap" href="/artefact?id={int(sighting.artefact)}">its artefact</a>'
        )
    return f'<p class="muted t-from">{", ".join(parts)}</p>' if parts else ""


def _facts_html(row: Any) -> str:
    """What a row's fold states beyond its own line, as a definition list: the day the bank
    booked it where that is not the day on the line, where the other leg of a confirmed transfer
    is listed, and where an open review flag is decided.

    The status, sources, and flags (which include whether Actual is withheld the row) and what
    each source stated are the lines beside it; the day the row counted, its amount, and its
    description are the row's own line and are not said again, and nor is a booked day that is the
    same one. Nothing is said of a row Actual is sent as usual: a fact true of every row is noise
    on all of them. A link to the other leg names the month it is dated in and its row's id, which
    opens it (`_OPEN_TARGETED_ROW`).
    """
    facts: list[tuple[str, str]] = []
    if row.booked is not None and row.booked != row.dated:
        facts.append(("Booked", f'<span class="mono">{_esc(row.booked.isoformat())}</span>'))
    if row.transfer == "confirmed" and row.transfer_other_anchor:
        other = AccountShown.named(row.transfer_other_account, row.transfer_other_label)
        address = _url("/ledger", ref=row.transfer_other_account, month=row.transfer_other_month)
        facts.append(
            (
                "Other leg",
                f'<a class="tap" href="{address}#t-{_esc(row.transfer_other_anchor)}">'
                f"{other.as_name()}</a> "
                f'<span class="mono">{_esc(row.transfer_other_month)}</span>',
            )
        )
    if row.review_open:
        facts.append(("Review", '<a class="tap" href="/review-flags">Decide this flag</a>'))
    if not facts:
        return ""
    return (
        '<dl class="t-facts">'
        + "".join(f"<dt>{_esc(name)}</dt><dd>{value}</dd>" for name, value in facts)
        + "</dl>"
    )


def _line_html(row: Any, line: str, more: str) -> str:
    """The row's line, and everything else about it one tap away.

    Times are London time, the clock the rest of the page uses (`_CLOCK_NOTE`); a date is as stated.

    The whole line is the disclosure's own summary, so the row is its own tap target and takes no
    line for a control (a month of fifty rows was a third taller with the disclosure on a line of
    its own). The summary's name is the line followed by "What each source reported", and the line
    carries the row's date, so no two rows' summaries read alike. `more` is what the tap opens:
    the sources and flags, what each source stated, and the notes.
    """
    lines = "".join(_sighting_line(sighting) for sighting in row.sightings)
    return (
        f'<details class="t-more"><summary class="t-row">{line}'
        '<span class="visually-hidden">What each source reported</span>'
        f'</summary><div class="t-extra">{more}{lines}</div></details>'
    )


def _mark_html(row: Any) -> str:
    """The one mark at the end of a transaction's line: cleared (a statement, an export, or the
    bank's own feed lists it), not cleared yet, or counted elsewhere. The word is said to a screen
    reader and as the mark's title, and the sources that cleared it are named in the title. A copy
    carries no mark: it is listed under the disclosure that says what it is, and its chip says so
    again when it is opened."""
    if _is_copy(row):
        return '<span class="mk" aria-hidden="true">&middot;</span>'
    if row.cleared_by:
        named = ", ".join(row.cleared_by)
        return (
            f'<span class="mk c" title="cleared by {_esc(named)}">&#10003;'
            '<span class="visually-hidden">cleared</span></span>'
        )
    return (
        '<span class="mk u" title="not cleared yet">&#9675;'
        '<span class="visually-hidden">not cleared yet</span></span>'
    )


def _joins_html(joins: Any, clock: str = "") -> str:
    """The account's transactions by how sources' reports of them were matched, and the guessed
    ones' dates a click away.

    `clock` is the sentence about the times a transaction states, which is about the same detail
    a transaction's "What each source reported" opens, and so is said here rather than in a
    disclosure of its own.
    """
    counts = dict(joins.by_basis) if joins is not None else {}
    sentence = count_sentence(counts)
    if not sentence:
        return _part("About the times shown", clock) if clock else ""
    guessed = joins.heuristic_days
    listing = (
        f"<details><summary>{len(guessed)} matched by a guess from amount, date, and "
        "description: the dates</summary>"
        "<p>" + ", ".join(_mono(day) for day in guessed) + "</p></details>"
        if guessed
        else '<p class="muted">No transaction was matched by a guess.</p>'
    )
    return _part(
        f"How the sources' reports were matched ({_esc(_joined_gist(counts))})",
        f"<p>{_esc(sentence[0].upper() + sentence[1:])}.</p>{listing}{clock}",
    )


def _joined_gist(counts: dict[str, int]) -> str:
    """The matches worth a glance, said short: those other than by the source's own id."""
    labels = dict(COUNT_LABELS)
    parts = [
        f"{counts[basis]} {label.removeprefix('matched ')}"
        for basis, label in labels.items()
        if counts.get(basis) and basis != BASIS_ID
    ]
    return ", ".join(parts) or "all matched by id"


def _row_rail(row: Any) -> str:
    """The class that gives a row its rail: red where it is flagged as a fault, amber where it
    is unproven (one source lists it, or the sources date it differently), none otherwise.

    A copy is quiet and carries no rail for being listed by one source, which is all a copy of
    another row's money ever is; it keeps a red one only for a fault that is its own.
    """
    if (
        row.unsendable
        or row.shares_identity
        or row.absorbed_ids
        or row.review_open
        or row.transfer == "claimed"
    ):
        return " flagged"
    if _is_copy(row):
        return ""
    return " doubtful" if row.one_source or row.dates_differ else ""


#: Opens the row an address's fragment names, and every fold around it, on load and when the
#: fragment changes. A fragment never reaches the server, so the page cannot open the row itself;
#: and a closed `details` hides its content whatever a `:target` rule says, with no style able to
#: set `open`. A link to the other leg of a transfer (`_facts_html`) lands on it open.
_OPEN_TARGETED_ROW = (
    "<script>(function(){function show(){var t=document.getElementById("
    "decodeURIComponent(location.hash.slice(1)));"
    "if(!t){return}for(var n=t;n;n=n.parentElement){if(n.tagName==='DETAILS'){n.open=true}}"
    "var own=t.querySelector('details');if(own){own.open=true}"
    "t.scrollIntoView()}show();addEventListener('hashchange',show)})();</script>"
)


def _balance_after_html(row: Any, unmasked: bool) -> str:
    """The balance after the row, quieter than its amount; a masked one is the sealed figure.

    A row no balance counts (pending against a bank's figure, or history) holds the column's
    place with nothing in it, so the figures beneath stay in line.
    """
    figure = _esc(row.balance_after)
    seal = _seal(unmasked) if figure else ""
    return f'<span class="t-bal mono nowrap muted{seal}">{figure}</span>'


def _row_html(row: Any, unmasked: bool = True, *, running: bool = False) -> str:
    """One transaction as a list item that wraps instead of scrolling.

    The description and the figure share the first line, the date and time the second, and the
    status, sources, and flags wrap beneath; on a wide screen the same parts sit in columns.
    A seven-column table was wider than a phone, and wider than the page column on
    a desktop, so the flags were the part that sat out of sight.
    """
    seal = _seal(unmasked)
    dates = ""
    if row.dates_differ:
        said = ", ".join(f"{source} {day}" for source, day in row.observed)
        dates = (
            '<p class="warn t-note" title="The sources dated this row '
            f'differently.">dates differ: {_esc(said)}</p>'
        )
    counterparty = (
        f'<p class="muted t-note"><span class="txt{seal}">{_esc(row.counterparty)}</span></p>'
        if row.has_counterparty
        else ""
    )
    currency = "" if row.currency == "GBP" else f" {_esc(row.currency)}"
    figure = _esc(_signed(row.direction, row.direction, row.amount))
    if row.direction != "nil":
        figure += currency
    notes = []
    if row.category:
        notes.append(f"category: {_esc(row.category)}")
    if row.payee:
        notes.append(f"payee: {_esc(row.payee)}")
    annotation = (
        '<p class="muted t-note">'
        + "<br>".join([*notes, f"set by {_esc(row.annotated_by)}"])
        + "</p>"
        if row.annotated_by
        else ""
    )
    sources = "".join(
        f'<span class="pill pill-quiet">{code_html(source)}</span> ' for source in row.sources
    )
    at = f" {_esc(_clock(row.feed_at))}" if row.feed_at is not None else ""
    ident = f' id="t-{_esc(row.anchor)}"' if row.anchor else ""
    line = (
        f'<span class="t-when mono nowrap" title="{_esc(row.dated.isoformat())}{at}">'
        f"{_esc(row.dated.isoformat())}</span>"
        f'<span class="t-desc"><strong class="txt{seal}">{_esc(row.description)}</strong></span>'
        f'<span class="t-fig mono nowrap fig{seal}">{figure}</span>'
        f"{_balance_after_html(row, unmasked) if running else ''}"
        f"{_mark_html(row)}"
    )
    kind = " folded" if _is_copy(row) else ""
    stated = (
        f'<p class="muted t-time">{_esc(row.dated.isoformat())}{at}</p>'
        if row.feed_at is not None
        else ""
    )
    more = (
        f'{stated}<p class="t-chips pills">{_status_pill(row)} {sources}{_row_flags(row)}</p>'
        f"{counterparty}{dates}{annotation}{_facts_html(row)}"
    )
    return f'<li class="txn{kind}{_row_rail(row)}"{ident}>{_line_html(row, line, more)}</li>'


#: The counts that mean something only when they are not zero: the table's label,
#: and the phrase that names the count in the sentence listing the zero ones.
_FLAG_COUNTS = (
    ("multi_source", "Seen by more than one source", "seen by more than one source"),
    (
        "one_source",
        "Seen by one source only, where several feed the account",
        "seen by one source only",
    ),
    ("pending", "Pending", "pending"),
    ("void", "Void", "void"),
    ("transfers_confirmed", "Internal transfers, confirmed", "transfers confirmed"),
    (
        "transfers_claimed",
        "Internal transfers, claimed but unpaired",
        "transfers unpaired",
    ),
    ("review_open", "Open review flags", "open review flags"),
)


def _summary_html(summary: Any, *, bound: bool, span: str = "month") -> str:
    """The counts of what is listed, a `span` of "month" or "window": the always-meaningful rows,
    then only the non-zero flags.

    A zero is not dropped silently.
    The sentence after the table names every flag count that is zero, so a
    reader can tell "nothing of that kind" from "not looked for".
    """
    reasons = _pairs(summary.withheld_by_reason)
    rows = _count(f"Transactions in the {span}", summary.rows) + _count_html(
        "Transactions per source", _source_pairs(summary.per_source)
    )
    zero: list[str] = []
    for field, label, phrase in _FLAG_COUNTS:
        number = getattr(summary, field)
        if number:
            rows += _count(label, number)
        else:
            zero.append(phrase)
    rows += _count("Cleared transactions", summary.cleared)
    rows += _count("Uncleared transactions", summary.uncleared)
    rows += _count("Would be sent to Actual", summary.would_send)
    rows += _count("Withheld from Actual", f"{summary.withheld} ({reasons})")
    if summary.unsendable:
        rows += _count("Refused by the push builder", summary.unsendable)
    else:
        zero.append("refused by the push builder")
    verdict = (
        _NOTHING_SENT
        if not bound
        else f"The two {span} sums {_differ(summary.sums_differ)}."
    )
    return (
        '<div class="scroll"><table>'
        + rows
        + _count(
            "Sum of the transactions counted (void, counted elsewhere, and reversed excluded)",
            _signed(
                _direction_word(summary.store_direction),
                summary.store_direction,
                summary.store_sum,
            ),
        )
        + _count(
            "Sum of what would be sent to Actual",
            _signed(
                _direction_word(summary.sent_direction),
                summary.sent_direction,
                summary.sent_sum,
            ),
        )
        + "</table></div>"
        + (
            f'<p class="muted">None {"in this window" if span == "window" else "this month"}: '
            f"{_words(zero)}.</p>"
            if zero
            else ""
        )
        + f"<p><strong>{_esc(verdict)}</strong></p>"
        + (
            '<p class="warn">The rows are in more than one currency, so these sums '
            "add unlike units.</p>"
            if summary.mixed_currency
            else ""
        )
    )


_BALANCE_WORDS = {"in": "in credit", "out": "overdrawn or owed", "nil": "nil"}

_BASIS_WORDS = {
    "stated": "stated by you",
    "bank": "the bank's own running balance",
    "statement": "a held statement's closing balance",
    "family": "the whole account's known balance, less its Spaces' own transactions",
    "opened": "the day before the account was created, with its feed held from then",
    "export": "the export's own balance, read as the main account's",
    "assumed-nil": "assumed, not shown: a Space's transactions are taken to start from nil",
}


def _balance_word(direction: str) -> str:
    return _BALANCE_WORDS.get(direction, direction)


def _balance_figure(direction: str, amount: str, unmasked: bool) -> str:
    """A balance, a difference, or an opening figure as the page prints it.

    Masked, every one is the same sealed slot with no direction word: a nil used to print as
    the word "nil", which told the reader the figure exactly, and "in credit" or "overdrawn"
    beside a sealed slot told them its sign. Shown, the direction word and the figure, or "nil".
    """
    if not unmasked:
        return f'<span class="mono nowrap fig sealed">{_esc(MASKED_TOTAL)}</span>'
    if direction == "nil":
        return "nil"
    return f'{_esc(_balance_word(direction))} <span class="mono nowrap fig">{_esc(amount)}</span>'


def _anchor_row(
    line: Any, *, balance_only: bool = False, unmasked: bool = True, note: str = ""
) -> str:
    """One known balance as a list item: its verdict first, then when, what, and whence.

    A list and not a table.
    Four columns did not fit a phone: the verdict, which is the reason for
    reading the section, was the column cut off at the right-hand edge.

    On a balance-only account a later STATED balance is followed, never tested,
    so it is not given the verdict of a check it did not pass.
    """
    if line.basis == "assumed-nil":
        role = '<span class="pill pill-quiet">assumed</span>'
        detail = ": the nil the listings' balances are tested against"
    elif line.defines_opening:
        role = '<span class="pill pill-quiet">the opening balance is worked out from this</span>'
        detail = ""
    elif balance_only and line.basis == "stated":
        role = '<span class="pill pill-quiet">followed</span>'
        detail = (
            ": this account is tracked by its known balances, so the balance "
            "follows this one"
        )
    elif line.verdict == "agrees":
        role = f'<span class="pill pill-ok">{ADDS_UP}</span>'
        detail = ": the transactions add up to it"
    else:
        role = '<span class="pill pill-bad">differs</span>'
        detail = " from what the transactions add up to, by " + _balance_figure(
            line.difference_direction, line.difference, unmasked
        )
    basis = _BASIS_WORDS.get(line.basis, line.basis)
    balance = _balance_figure(line.balance_direction, line.balance, unmasked)
    return (
        "<li>"
        f"<p>{role}{detail}</p>"
        f'<p>End of <span class="mono nowrap">{_esc(line.day)}</span>: {balance}</p>'
        f'<p class="muted">{_esc(basis)}</p>'
        + (f'<p class="muted">{_esc(note)}</p>' if note else "")
        + "</li>"
    )


def _date_notes(view: Any) -> dict[str, str]:
    """For each statement closing whose balance by stored date is a different figure from the
    stated one, the sentence that says so, by day. Counts and dates only: never the size."""
    checks = view.statements
    if checks is None:
        return {}
    return {
        s.day.isoformat(): date_difference_sentence(s.day, s.by_date)
        for s in checks.statements
        if s.by_date is not None
    }


#: What each way of comparing the balances stated for one day says (`ledger.SharedDay.figures`).
_FIGURES_SAID = {
    "equal": "The figures are equal.",
    "differ": "The figures differ.",
    "one": "Only one of them is in use.",
}


def _stretches_html(agreement: Any) -> str:
    """Where the transactions stop adding up to the known balances, said in days.

    The sentences are `agreement.stretch_sentences`'s. They are plain lines under the verdict:
    the account's hold above is the one box on the page, and these only locate it. What a failing
    stretch can mean is listed folded, since the arithmetic cannot choose between the causes.
    """
    said = "".join(
        f'<p class="sub">{_esc(sentence)}</p>' for sentence in stretch_sentences(agreement)
    )
    if any(not s.reproduced and not s.conflict and not s.untested for s in agreement.stretches):
        means = "".join(f"<li>{_esc(item)}</li>" for item in STRETCH_MEANINGS)
        said += _part(
            "What a stretch that does not add up can mean",
            '<p class="muted">The balances and the transactions disagree, and the arithmetic '
            f"cannot say which of these is why:</p><ul>{means}</ul>",
        )
    return said


def _balance_form(
    action: str, ref: str, month: str, entry: Any, label: str, *, secondary: bool = True
) -> str:
    """The form that disregards one source's balance for one day, or reads it again.

    It names the balance by day, whoever states it, basis, and which of those under that key (a
    place by figure, `ledger.SharedDayEntry.which`), never by figure: the page is a GET.
    """
    return (
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
        f'<input type="hidden" name="day" value="{_esc(entry.day)}">'
        f'<input type="hidden" name="source" value="{_esc(entry.stating)}">'
        f'<input type="hidden" name="basis" value="{_esc(entry.basis)}">'
        f'<input type="hidden" name="which" value="{_esc(str(entry.which))}">'
        + submit_button(label, secondary=secondary)
        + "</form>"
    )


def _entry_html(entry: Any, ref: str, month: str) -> str:
    basis = _BASIS_WORDS.get(entry.basis, entry.basis)
    if entry.stale:
        pill = '<span class="pill pill-quiet">no longer applies</span> '
        form = _balance_form(
            "/ledger-balance-use-again",
            ref,
            month,
            entry,
            f"Remove the disregard of the {entry.stating} balance for the end of {entry.day}",
        )
        basis = f"{basis}; the balance it named is no longer held"
    elif entry.disregarded:
        pill = '<span class="pill pill-quiet">disregarded</span> '
        form = _balance_form(
            "/ledger-balance-use-again",
            ref,
            month,
            entry,
            f"Use the {entry.stating} balance for the end of {entry.day} again",
        )
    elif entry.can_disregard:
        pill = ""
        form = _balance_form(
            "/ledger-balance-disregard",
            ref,
            month,
            entry,
            f"Disregard the {entry.stating} balance for the end of {entry.day}",
        )
    else:
        pill = ""
        form = ""
    return f"<li>{pill}{code_html(entry.stating)}: {_esc(basis)}{form}</li>"


def _listing_html(own: Any) -> str:
    """Where a known balance is tested by its own statement's listing, and where a statement is
    taken to have closed before a day's last transactions. Plain quiet lines: both are said in
    `agreement`'s words, which carry how it is known. The verdict line already says it where the
    tested balance is the only one the account adds up through."""
    # A statement fault is its own finding, said whichever hold is earliest above; the hold's box
    # already carries it where it is the hold. The way out is to disregard that statement's
    # balance, which the known-balances section offers.
    held = own.held
    faults = "".join(
        f'<p class="warn">{_esc(fault.says)} If the statement\'s balance is the wrong thing, '
        'disregard it under <a class="tap" href="#opening">known balances</a>.</p>'
        for fault in own.statement_faults
        if held is None or held.kind != HELD_STATEMENT or held.day != fault.day
    )
    return faults + "".join(
        f'<p class="sub">{_esc(listing_tested_sentence(tested))}</p>'
        for tested in own.listing_tested
    ) + "".join(
        f'<p class="sub">{_esc(closed_before_sentence(claim))}</p>' for claim in own.closed_before
    )


def _statements_score_html(view: Any) -> str:
    """How many of the account's statements add up by what they list, how many cannot say, and
    which two documents clash: the evidence for testing this account's statements by what they
    list. Counts only, in ordinary text. How many a calendar-day test would have reproduced is on
    Identity health, where the two tests are compared."""
    checks = view.statements
    if checks is None or not checks.statements:
        return ""
    total = len(checks.statements)
    adding = sum(1 for s in checks.statements if s.adds_up is True)
    unsaid = sum(1 for s in checks.statements if s.adds_up is None)
    failing = sum(1 for s in checks.statements if s.adds_up is False)
    verb, them = ("adds", "it lists") if total == 1 else ("add", "they list")
    said = f"{adding} of {total} {word(total, 'statement')} {verb} up by what {them}"
    if unsaid:
        said += f"; {unsaid} cannot say"
    if failing:
        said += f"; {failing} {agree(failing, 'does')} not"
    clashing = sorted({s.day.isoformat() for s in checks.statements if s.clash})
    body = (
        f'<p class="muted">{_esc(said)}. A statement lists a purchase by the day it was made, '
        "which can fall either side of its closing day, so it is tested by what it lists and "
        "not by date.</p>"
    )
    for day in clashing:
        body += (
            f'<p class="warn">Two documents state a closing balance for {_esc(day)} and do not '
            "list the same transactions, so neither tests its days. It may be one statement "
            "uploaded twice.</p>"
        )
    return body


def _shared_days_html(view: Any) -> str:
    """The balances several sources state for one day, side by side, with the way to disregard one.

    Whether the figures are equal is said and never what they are. Disregarding keeps the balance
    on the page, marked, and takes it out of every stretch and every conflict; a balance used
    again is read as before. A day on which a statement is taken to have closed before some
    transactions says why the figures differ, in `agreement`'s words, and is not offered as a
    conflict.
    """
    opening = view.opening
    if not opening.shared_days and not opening.disregarded:
        return ""
    ref, month = view.ref, _scope(view)
    standing = view.standing
    explained = (
        {claim.day.isoformat(): claim for claim in standing.own.closed_before}
        if standing is not None
        else {}
    )
    body = ""
    if opening.shared_days:
        body += (
            '<p class="muted">Where several sources state a balance for one day, each is listed '
            "with whether their figures are equal. Disregarding one leaves the others, and it "
            "stays here, marked, so it can be used again.</p>"
        )
        routine = ""
        for day in opening.shared_days:
            items = "".join(_entry_html(entry, ref, month) for entry in day.entries)
            figures = _FIGURES_SAID.get(day.figures, "")
            if day.figures == "differ" and day.day in explained:
                figures = (
                    "The figures differ, and the statement is taken to have closed before "
                    "some transactions."
                )
            said = (
                f'<p>End of <span class="mono nowrap">{_esc(day.day)}</span>: '
                f"{_esc(figures)}</p><ul>{items}</ul>"
            )
            if day.figures == "equal" and not any(e.disregarded or e.stale for e in day.entries):
                routine += said
            else:
                body += said
        if routine:
            body += _disclosure("Days on which the sources state equal figures", routine)
        if opening.shared_more:
            body += (
                f'<p class="muted">and {_plural(opening.shared_more, "older day")} not listed.</p>'
            )
    listed = {entry.day for day in opening.shared_days for entry in day.entries}
    elsewhere = [entry for entry in opening.disregarded if entry.day not in listed]
    if elsewhere:
        body += (
            "<p>Disregarded:</p><ul>"
            + "".join(_entry_html(entry, ref, month) for entry in elsewhere)
            + "</ul>"
        )
    return body


def _listed_anchors(
    anchors: tuple[Any, ...], *, everything: bool
) -> tuple[tuple[Any, ...], int]:
    """The known balances worth listing, and how many agreeing ones are left out.

    The balance that sets the opening and every one that differs or was not tested are always
    listed, since those are what a reader came for. Of the agreeing ones the newest
    `_SHOWN_AGREEING_ANCHORS` are listed, being what a person lays beside the bank's app. The
    anchors arrive oldest first, so "newest" is the tail. `everything` lists them all.
    """
    agreeing = [line for line in anchors if not line.defines_opening and line.verdict == "agrees"]
    if everything or len(agreeing) <= _SHOWN_AGREEING_ANCHORS:
        return anchors, 0
    left_out = {id(line) for line in agreeing[:-_SHOWN_AGREEING_ANCHORS]}
    return tuple(line for line in anchors if id(line) not in left_out), len(left_out)


def _anchors_html(
    anchors: tuple[Any, ...],
    *,
    balance_only: bool = False,
    unmasked: bool = True,
    everything: bool = False,
    earlier: Callable[[int], str] | None = None,
    notes: dict[str, str] | None = None,
) -> str:
    """The known balances worth listing (`_listed_anchors`), then one line for the earlier ones.

    `earlier` makes that line from the count left out, as a link to the full list; without it a
    long run is simply listed whole. `notes` are what a statement's balance says of how it parts
    from the position by date, by day.
    """
    listed, left_out = _listed_anchors(anchors, everything=everything or earlier is None)
    said = notes or {}
    items = "".join(
        _anchor_row(
            line,
            balance_only=balance_only,
            unmasked=unmasked,
            note=said.get(line.day, "") if line.basis == "statement" else "",
        )
        for line in listed
    )
    line = earlier(left_out) if earlier is not None and left_out else ""
    return f'<ul class="anchors">{items}</ul>{line}'


def _opening_note(family: Any) -> str:
    css = "muted" if family.nil_day else "warn"
    return f'<p class="{css}">{_esc(family.opening_note)}</p>'


#: How many days of a long list of faults are named before "and N more".
_LISTED_FAULT_DAYS = 20


def _day_list(days: tuple[str, ...]) -> str:
    named = ", ".join(
        f'<span class="mono nowrap">{_esc(day)}</span>' for day in days[:_LISTED_FAULT_DAYS]
    )
    more = len(days) - _LISTED_FAULT_DAYS
    return named + (f" and {more} more" if more > 0 else "")


def _changes_html(family: Any) -> str:
    """Where the faults are, by day: the known balances at which the difference
    from the rows changes, which are the places a movement is missing or surplus.

    Dates and counts only, never a figure, so it reads the same masked or not.
    """
    days = family.change_days
    if not days:
        return ""
    explained = family.unheld_change_days
    body = (
        f"<p>The difference changes on {len(days)} "
        f"{'day' if len(days) == 1 else 'days'}: {_day_list(days)}. "
        "Each change is one more movement missing or surplus, on or just before that day.</p>"
    )
    if explained:
        body += (
            f"<p>{len(explained)} of those "
            f"{'changes coincides' if len(explained) == 1 else 'changes coincide'} with a "
            f"transfer to a Space whose rows are not held ({_day_list(explained)}), which "
            "needs that Space declared and not a row found.</p>"
        )
    return body


def _mono(text: object) -> str:
    return f'<span class="mono nowrap">{_esc(str(text))}</span>'


#: The one place the page says which clock its times are on and what that does to a date.
#: Every time on the page comes from `_clock`, which is what the sentence describes.
_CLOCK_NOTE = (
    "Times are London time, as the bank's app shows them. The bank's feed states UTC, which "
    "is an hour behind London from the last Sunday of March to the last Sunday of October. "
    "A time past midnight in London but before it in UTC is shown with its London date; "
    "the transaction keeps the feed's own (UTC) date and counts toward that day."
)


def _clock(moment: Any) -> str:
    """A feed time as the bank's app shows it, and its London date where that is not the feed's."""
    local = london(moment)
    shown = local.strftime("%H:%M")
    if local.date() > moment.astimezone(UTC).date():
        shown += f" on {local.date().isoformat()}"
    return shown


def _clock_html(moment: Any) -> str:
    return _mono(_clock(moment))


def _row_note(note: Any) -> str:
    """One row, named by who dated it when, which way it moved, and its status.

    The feed's time stands beside the day the bank's own feed gave: it is the feed's
    time, so no other source's date carries it.
    """
    at = f" {_clock_html(note.feed_at)}" if note.feed_at is not None else ""
    dated = ", ".join(
        f"{code_html(source)} {_mono(day)}{at if source == BANK_SOURCE else ''}"
        for source, day in note.dates
    )
    seen = _sources_html(note.sources) or "no source"
    kind = "a round-up leg" if note.round_up_leg else "a transfer leg" if note.transfer else ""
    pairing = (
        f", confirmed paired with {_esc(note.partner_account)}{_partner_of(note)}"
        if note.pairing == "paired"
        else " with no pair"
        if note.pairing == "unpaired"
        else ""
    )
    extras = "".join(
        (
            f", {kind}{pairing}" if kind else "",
            f"; {_esc(note.why)}" if note.why and note.why != note.status else "",
            "; the export's own figure for it differs" if note.figure_differs else "",
            f"; in {_esc(note.account)}" if note.account else "",
            f"; {_CARRIES[note.carries]}" if note.carries else "",
            (
                "; a counter-item lies within three days"
                if note.counter_item
                else "; no counter-item within three days"
            )
            if note.counter_item is not None
            else "",
            (
                "; a Space arrival of the same size lies within three days"
                if note.arrival_near
                else "; no Space arrival of the same size within three days"
            )
            if note.arrival_near is not None
            else "",
            (
                f"; not folded into a Space row: {_esc(note.fold_refusal)}"
                if note.fold_refusal
                else ""
            ),
            _lookalike(note.lookalike) if note.lookalike is not None else "",
        )
    )
    feed = f"; feed status {_esc(note.feed_status)}" if note.feed_status else ""
    return (
        f"{_esc(note.direction)} row dated {dated}; seen by {seen}; "
        f"{_esc(note.status)}{feed}{extras}"
    )


def _partner_of(note: Any) -> str:
    """What a transfer leg's partner is: its direction, its day, and its kind.

    The kind is what tells a leg paired with a leg from a leg paired with an
    ordinary payment of the same size, which is the pairing that went wrong
    when "confirmed paired with the Space" sat beside "a transfer leg with no pair".
    """
    if not note.partner_day:
        return ""
    kind = "an internal leg" if note.partner_is_leg else "an ordinary payment"
    return f" ({_esc(note.partner_direction)} row dated {_mono(note.partner_day)}, {kind})"


def _days_away(days: int) -> str:
    return "on the same day" if days == 0 else f"{_plural(days, 'day')} away"


def _shape(found: Any) -> str:
    """What the two rows share, said once: size, direction, and the recipient if compared.

    Only whether the recipient agrees is said, never who it is.
    """
    if found.recipient == "agrees":
        return "of the same size, direction, and recipient"
    if found.recipient == "differs":
        return "of the same size and direction, to a different recipient"
    return "of the same size and direction (whose recipient could not be compared)"


def _lookalike(found: Any) -> str:
    """The row on the other side of an export comparison that is like this one, or none.

    Said once, here. "No figure, no description": a row is the same size and
    direction as another and the sentence never says what size.
    """
    lister = _esc(found.source) if found.source else "the export"
    if found.side == "export":
        if not found.found:
            return f"; {lister} lists no row of the same size and direction within thirty days"
        away = _days_away(found.days_away)
        lead = f"; {lister} lists a row {_shape(found)}, {away}"
        if found.sighted_on == "nothing":
            return f"{lead}, that no stored row carries"
        if found.sighted_on == "this row":
            return f"{lead}, reported on this row"
        if found.sighted_on == "a row of another account":
            return f"{lead}, reported on a row of another account"
        other = _row_note(found.other) if found.other is not None else ""
        return f"{lead}, reported on another stored row ({other})"
    if not found.found:
        return "; the store counts no row of the same size and direction within thirty days"
    listing = (
        f"which {lister} lists as another row"
        if found.sighted_on == "listed"
        else f"which {lister} does not list"
    )
    other = _row_note(found.other) if found.other is not None else ""
    return (
        f"; the store counts a row {_shape(found)}, "
        f"{_days_away(found.days_away)}, {listing} ({other})"
    )


#: What `round_up_accounts.carrier_state` says, in the words of a row's note.
_CARRIES = {
    "unreadable": "carries a round-up that cannot be read",
    "nothing": "carries a round-up of nothing",
    "no leg": "carries a round-up with no leg held",
    "leg paired": "carries a round-up whose leg is held and paired",
    "leg unpaired": "carries a round-up whose leg is held and has no pair",
}


def _percent_words(percent: int) -> str:
    return "under 1%" if percent == 0 else f"{percent}%"


def _shape_sentence(found: Any) -> str:
    """How a row's feed item differs from the usual one. Said once, here.

    Field names, closed-set values, currency codes, and percentages only: the
    measurement (`feed_item_shape`) never carries an amount, a name, or free text.
    """
    if found is None:
        return ""
    kind = " ".join(part for part in (found.status, found.direction, found.source) if part)
    kind = f"{kind} item" if kind else "item"
    if found.comparable < MIN_COMPARABLE:
        parts = []
        if found.changed:
            parts.append("between its first and latest landing, " + ", ".join(found.changed))
        said = f"; {'; '.join(parts)}" if parts else ""
        return (
            f" Its feed item has {_plural(found.comparable, 'other comparable item')}, too few "
            f"to say what the usual {_esc(kind)} is{_esc(said)}."
        )
    if not differs(found):
        return " Its feed item is like the usual one."
    parts = []
    if found.lacks:
        parts.append("lacks " + ", ".join(found.lacks))
    if found.extra:
        parts.append(
            "has " + ", ".join(found.extra) + ", which fewer than one in ten comparable items carry"
        )
    parts += [
        f"{name} {value}, carried by {_percent_words(percent)} of comparable items"
        for name, value, percent in found.rare_values
    ]
    if found.currencies != ("", ""):
        parts.append(
            f"amount and sourceAmount name different currencies ({found.currencies[0]} and "
            f"{found.currencies[1]})"
        )
    if found.days_apart is not None:
        parts.append(
            f"its settlement time is {_plural(found.days_apart, 'day')} after its transaction "
            f"time, a gap at least that long in {_percent_words(found.days_apart_percent)} "
            "of comparable items"
        )
    if found.changed:
        parts.append("between its first and latest landing, " + ", ".join(found.changed))
    return f" Its feed item differs from the usual {_esc(kind)}: {_esc('; '.join(parts))}."


def _row_li(note: Any) -> str:
    return f"<li>{_row_note(note)}.{_shape_sentence(note.feed_shape)}</li>"


def _row_list(rows: Any) -> str:
    if not rows.count:
        return ""
    items = "".join(_row_li(note) for note in rows.named)
    more = f"<li>and {rows.more} more</li>" if rows.more > 0 else ""
    return f"<ul>{items}{more}</ul>"


def _hold_html(change: Any, hold: str) -> str:
    """The sentence for one explanation that holds. Said once, here."""
    source = _esc(change.source)
    if hold == "source-disagreement":
        other = (
            f"{_esc(change.previous_source)} and {source} stating different balances"
            if change.previous_source
            else f"{source} stating a balance that disagrees with the rows differently"
        )
        return (
            f"<p>{source}'s own difference from the rows has not moved since its previous "
            f"balance, so this change is {other}, not a row.</p>"
        )
    if hold == "listed-not-counted":
        return (
            "<p>The change equals the sum of the "
            f"{_plural(change.listed_not_counted.count, 'row')} {source} lists in the window "
            "that the store does not count:</p>"
            + _row_list(change.listed_not_counted)
        )
    if hold == "counted-not-listed":
        return (
            "<p>The change equals minus the sum of the "
            f"{_plural(change.counted_not_listed.count, 'row')} the store counts in the "
            f"window that {source} does not list:</p>" + _row_list(change.counted_not_listed)
        )
    if hold == "different-figure":
        return (
            "<p>The change equals the sum of the differences between the figure "
            f"{source} lists and the store's, for "
            f"{_plural(change.different_figure.count, 'payment')}:</p>"
            + _row_list(change.different_figure)
        )
    if hold == "combined":
        return (
            "<p>No one set of rows equals the change, but together they do exactly: the "
            f"rows {source} lists that the store does not count, less the rows the store "
            f"counts that {source} does not list, plus the differences in figure.</p>"
            + (
                f"<p>Listed, not counted ({change.listed_not_counted.count}):</p>"
                + _row_list(change.listed_not_counted)
                if change.listed_not_counted.count
                else ""
            )
            + (
                f"<p>Counted, not listed ({change.counted_not_listed.count}):</p>"
                + _row_list(change.counted_not_listed)
                if change.counted_not_listed.count
                else ""
            )
            + (
                f"<p>Listed under another figure ({change.different_figure.count}):</p>"
                + _row_list(change.different_figure)
                if change.different_figure.count
                else ""
            )
        )
    if hold == "one-row":
        shape = "the negative of a single counted row" if change.one_row_negated else (
            "a single counted row"
        )
        return f"<p>The change equals {shape}:</p><ul>{_row_li(change.one_row)}</ul>"
    if hold == "feed-status-rows":
        return "".join(
            "<p>The change equals "
            f"{'minus ' if found.equals == 'equals minus' else ''}the sum of the "
            f"{_plural(found.rows.count, 'row')} whose feed status is {_esc(found.status)} "
            "in the window:</p>" + _row_list(found.rows)
            for found in change.feed_status_rows
            if found.equals
        )
    if hold == "feed-status-left-out":
        return "".join(
            f"<p>Leave the {_plural(found.rows.count, 'row')} of feed status "
            f"{_esc(found.status)} out of the count and "
            + (
                "nothing is left to explain.</p>"
                if found.left == "nil"
                else "the rest of the rows the store counts that "
                f"{source} does not list sum to what is left.</p>"
            )
            for found in change.feed_status_rows
            if found.left
        )
    if hold == "no-row-status-rows":
        statuses = sorted({note.feed_status for note in change.no_row_status_rows.named})
        return (
            "<p>The change equals "
            f"{'minus ' if change.no_row_status_equals == 'equals minus' else ''}the sum of the "
            f"{_plural(change.no_row_status_rows.count, 'row')} the store counts whose own feed "
            "item is, in the newest landed feed, a status that makes no row "
            f"({_esc(', '.join(statuses))}). The store made each from an earlier fetch, and "
            "nothing has voided it since the bank changed the item:</p>"
            + _row_list(change.no_row_status_rows)
        )
    if hold == "timing-pair":
        return (
            f"<p>The change equals {'minus ' if change.sides_negated else ''}the sum of the "
            f"{_plural(change.sides.count, 'row')} that sit on different sides of the two "
            "balances, dated inside one change's window by the source and inside the "
            "other's by the store:</p>" + _row_list(change.sides)
        )
    if hold == "straddling":
        return (
            "<p>The change equals the sum of the "
            f"{_plural(change.straddling.count, 'row')} whose date from {source} and stored "
            "date fall on different sides of this balance. That would mean the source's own "
            "dating is not being applied to them, which is a defect:</p>"
            + _row_list(change.straddling)
        )
    if hold == "unheld-space":
        return "<p>It coincides with a transfer to a Space whose rows are not held.</p>"
    if hold == "row-counts":
        return (
            f"<p>The export lists {change.export_rows} "
            f"{'transaction' if change.export_rows == 1 else 'transactions'} in the window and "
            f"the store holds {change.store_sightings} "
            f"{'report' if change.store_sightings == 1 else 'reports'} of it there: "
            "identical transactions collapsed into one, or a transaction reported on a "
            "different day.</p>"
        )
    if hold == "export-opening":
        return (
            "<p>It equals the export's own opening balance: the export opens at a figure "
            "other than nil, so rows before it are not held.</p>"
        )
    return (
        "<p>None of these accounts for it. Counted rows by the source's dating: "
        f"{change.rows_before} before the window, {change.rows_inside} inside it, "
        f"{change.rows_after} after it.</p>"
    )


def _parting_html(found: Any) -> str:
    """Where inside the window the export's balance and the store's running sum first part.

    Said once, here. A position, a date, a direction, and what the store holds in
    that row's place: never a figure, a description, or a payee.
    """
    if found is None:
        return ""
    row = (
        f"row {found.position:,} of {found.total:,} in the export's own sequence, dated "
        f"{_mono(found.day)}, a payment {_esc(found.direction)}"
    )
    surplus = (
        f"{_plural(found.surplus_before, 'surplus row')} the export does not list"
        if found.surplus_before
        else ""
    )
    if found.after_last:
        return (
            "<p>Walked row by row, the export's balance and the store's running sum do not "
            f"part within the window's rows. The store holds {surplus}, which "
            f"{'falls' if found.surplus_before == 1 else 'fall'} after the last of them "
            f"({row}).</p>"
        )
    place = {
        "nothing": "holds no row in its place",
        "another size": "holds a row of another size in its place",
        "not counted": f"holds the row but does not count it ({_esc(found.why)})",
        "another account": "holds the row under an account outside this family",
        "held": "holds that row, as the export lists it",
    }[found.holds]
    extra = (
        f", and {surplus} between the row before it and this one"
        if surplus
        else ""
    )
    return (
        "<p>Walked row by row, the export's balance and the store's running sum first part "
        f"after {row}. There the store {place}{extra}.</p>"
    )


def _search_html(searched: Any) -> str:
    """What every source says of the rows a change is explained by. Said once, here.

    Source names and counts only, so the sentence reads the same masked or not.
    """
    if not searched:
        return ""
    one = searched[0].of == 1
    parts = []
    for found in searched:
        name = _esc(found.source)
        if not found.covers:
            parts.append(f"{name}'s period does not cover the window")
        elif found.listed == found.of:
            parts.append(f"{name} lists {'it' if one else f'all {found.of}'}")
        elif found.listed == 0:
            tail = (
                f", though it lists {'it' if one else _plural(found.elsewhere, 'of them')} "
                "on another day"
                if found.elsewhere
                else ""
            )
            parts.append(f"{name} does not list {'it' if one else 'any of them'}{tail}")
        else:
            parts.append(f"{name} lists {found.listed} of {found.of}")
    subject = "the row" if one else f"the {searched[0].of} rows"
    return (
        f"<p>Searching this window in each source for {subject} that account"
        f"{'s' if one else ''} for the change: {'; '.join(parts)}.</p>"
    )


def _span_words(seconds: int) -> str:
    """How long, in the unit a person reads it in."""
    minutes = seconds // 60
    if minutes < 1:
        return "under a minute"
    if minutes < 120:
        return _plural(minutes, "minute")
    if minutes < 2880:
        hours, rest = divmod(minutes, 60)
        return _plural(hours, "hour") + (f" {_plural(rest, 'minute')}" if rest else "")
    return _plural(minutes // 1440, "day")


def _no_row_item(item: Any) -> str:
    """One feed item that made no row, beside the row it sits nearest. Said once, here.

    Only whether the size and the recipient agree is said, never either.
    """
    kind = f"{_esc(item.status)} {_esc(item.direction)} item".replace("  ", " ")
    text = f"{kind} at {_clock_html(item.at)}"
    if not item.compared:
        return text
    row = f"the {_esc(item.row_direction)} row"
    if item.seconds_after is not None and item.row_at is not None:
        stated = f"{row} at {_clock_html(item.row_at)}"
        if abs(item.seconds_after) < 60:
            text += f", at the same minute as {stated}"
        else:
            side = "after" if item.seconds_after > 0 else "before"
            text += f", {_span_words(abs(item.seconds_after))} {side} {stated}"
    size = "the same size" if item.same_size else "a different size"
    if item.recipient == "agrees":
        return f"{text}, of the same recipient and {size}"
    if item.recipient == "differs":
        return f"{text}, of a different recipient and {size}"
    return f"{text}, whose recipient could not be compared, and of {size}"


def _no_rows_html(found: Any) -> str:
    """The feed items in a change's window that are not rows, or nothing where there are none."""
    if not found.count:
        return ""
    lead = (
        "The bank's feed also holds 1 item in this window that is not a row:"
        if found.count == 1
        else f"The bank's feed also holds {found.count:,} items in this window that are not rows:"
    )
    items = "".join(f"<li>{_no_row_item(item)}</li>" for item in found.named)
    more = (
        f"<li>and {found.more} more (the page names at most {len(found.named)})</li>"
        if found.more
        else ""
    )
    return f"<p>{_esc(lead)}</p><ul>{items}{more}</ul>"


def _reversed_html(found: Any) -> str:
    """How many reversed rows are held as history, and what the export and the rows say of them.

    Said even when there are none: three counts, over the whole account, are
    what says whether the reading "a reversed row is not money" still holds.
    """
    sentence = (
        f"{found.held} reversed row is held as history."
        if found.held == 1
        else f"{found.held} reversed rows are held as history."
    )
    if found.held:
        sentence += (
            f" The export lists {found.listed} of them, and {found.counter_item} "
            f"{'has' if found.counter_item == 1 else 'have'} a counter-item, a row of the "
            "opposite direction and equal size within three days."
        )
    return f'<p class="muted">{_esc(sentence)}</p>'


def _feed_statuses_html(found: Any, *, exports: bool) -> str:
    """What the bank's own feed says the counted rows are, and the items it says that yield none.

    Said even when there is nothing to say of them: a status other than SETTLED on
    counted rows is what says whether such a row is money, and a status the
    provider's map lacks is a payment that never became a row. Names and counts only.
    """
    sentences = []
    for count in found.by_status:
        one = count.counted == 1
        sentence = f"{count.counted} counted {'row carries' if one else 'rows carry'}"
        sentence += f" the feed status {count.status}"
        if exports and one:
            sentence += ", the export lists it" if count.listed else ", the export does not list it"
        elif exports:
            sentence += f", the export lists {count.listed} of them"
        if one:
            sentence += ", and it has " + ("a" if count.counter_item else "no") + " counter-item."
        else:
            sentence += (
                f", and {count.counter_item} {'has' if count.counter_item == 1 else 'have'} "
                "a counter-item."
            )
        sentences.append(sentence)
    if found.by_status:
        sentences.append(
            "A counter-item is a row of the opposite direction and equal size within three days."
        )
    if found.dropped:
        names = ", ".join(f"{name} ({count})" for name, count in found.dropped)
        sentences.append(f"Feed items the map drops on purpose, which make no row: {names}.")
    if found.unmapped:
        names = ", ".join(f"{name} ({count})" for name, count in found.unmapped)
        sentences.append(f"Feed items with a status the map does not list, so no row: {names}.")
    elif found.by_status or found.dropped:
        sentences.append("No feed item carries a status the map does not list.")
    if not sentences:
        return ""
    return "".join(f'<p class="muted">{_esc(sentence)}</p>' for sentence in sentences)


def _explanations_html(explanation: Any) -> str:
    """Why each of the first changes happened, and what the held exports are like."""
    if explanation is None:
        return ""
    body = ""
    facts = explanation.facts
    if facts is not None:
        body += (
            f"<p>The held {'export lists' if facts.exports == 1 else 'exports list'} "
            f"{_plural(facts.rows, 'transaction')}. In its own order {facts.out_of_order:,} "
            f"{'is' if facts.out_of_order == 1 else 'are'} out of date order, "
            f"{_plural(facts.uncut_days, 'day')} hold a transaction but have no clean cut and "
            f"so state no balance, and {facts.unsighted:,} "
            f"{'is' if facts.unsighted == 1 else 'are'} not held in the store.</p>"
        )
    body += _reversed_html(explanation.reversed)
    body += _feed_statuses_html(explanation.feed_statuses, exports=facts is not None)
    pairs = sum(1 for change in explanation.changes if change.undone_on)
    if explanation.changes:
        body += f'<p class="muted">{_esc(THRESHOLDS)}</p>'
        body += (
            f"<p>{_plural(len(explanation.changes), 'explanation')} "
            f"{'follows' if len(explanation.changes) == 1 else 'follow'}: "
            f"{len(explanation.changes) - pairs} for permanent changes, and "
            f"{pairs} for timing pairs. The two changes of a pair undo each other and are "
            "explained once, at the first.</p>"
        )
    if explanation.omitted:
        body += (
            f'<p class="warn">{_plural(explanation.omitted, "change")} '
            f"{'has' if explanation.omitted == 1 else 'have'} no explanation here: the page "
            f"works out at most {explanation.bound} explanations for one account.</p>"
        )
    for change in explanation.changes:
        start = _mono(change.after) if change.after else "the start"
        undone = (
            f", undone by an opposite change at the end of {_mono(change.undone_on)}, so the "
            "two are explained here once"
            if change.undone_on
            else ""
        )
        body += (
            f"<div><p><strong>The change at the end of {_mono(change.day)}"
            f"</strong> (after {start}, stated by {_esc(change.source)}{undone}):</p>"
            + "".join(_hold_html(change, hold) for hold in change.holds)
            + _parting_html(change.parting)
            + _search_html(change.searched)
            + _no_rows_html(change.no_rows)
            + "</div>"
        )
    return body


def _round_ups_html(family: Any) -> str:
    """Counts of the feed's round-ups, said even when there are none.

    A feed that carries no round-up at all is how a wrong reading of it would
    show, so the nil case is a sentence of its own rather than silence.
    """
    unreadable = (
        f"{_esc(str(family.round_ups_unreadable))} could not be read and hold no leg."
    )
    if not family.round_ups_carried and not family.round_up_legs:
        return (
            '<p class="muted">No row of the feed carries a round-up, so no round-up leg to a '
            "Space is held. If this account's card payments do round up into a Space, the "
            f"feed does not report it in the shape this page reads. {unreadable}</p>"
        )
    return (
        f'<p class="muted">{_plural(family.round_ups_carried, "feed row")} '
        f"{'carries' if family.round_ups_carried == 1 else 'carry'} a "
        f"round-up. {_plural(family.round_up_legs, 'round-up leg')} to a Space "
        f"{'is' if family.round_up_legs == 1 else 'are'} held, "
        f"and {_esc(str(family.round_up_legs_paired))} of them "
        f"{'is' if family.round_up_legs_paired == 1 else 'are'} paired with a row in that "
        f"Space. {unreadable}</p>" + _round_up_gaps_html(family.round_up_gaps)
    )


def _days_html(days: Any, total: int) -> str:
    if not days:
        return ""
    named = ", ".join(_mono(day) for day in days)
    more = f", and {total - len(days)} more" if total > len(days) else ""
    return f'<p class="muted">Dated {named}{more}.</p>'


def _round_up_gaps_html(gaps: Any) -> str:
    """What became of the round-ups that are not a paired leg, in counts and days.

    Each sentence is said even when its count is nil, since a nil is what shows
    a reading to be complete.
    """
    if gaps.no_leg:
        parts = [
            _plural(
                gaps.no_leg_of_nothing, "is a round-up of nothing", "are round-ups of nothing"
            ),
            _plural(gaps.no_leg_incoming, "is on an incoming item", "are on incoming items"),
            _plural(
                gaps.no_leg_reversed_or_declined,
                "is on a reversed or declined item",
                "are on reversed or declined items",
            ),
            _plural(gaps.no_leg_unreadable, "could not be read", "could not be read"),
            _plural(gaps.no_leg_other, "is other", "are other"),
        ]
        body = (
            f'<p class="muted">Of the '
            f"{_plural(gaps.no_leg, 'feed row that carries', 'feed rows that carry')} a "
            f"round-up and {'holds' if gaps.no_leg == 1 else 'hold'} no leg, "
            f"{', '.join(parts[:-1])}, and {parts[-1]}.</p>"
        )
    else:
        body = '<p class="muted">Every feed row that carries a round-up holds a leg.</p>'
    if gaps.unpaired_legs:
        body += (
            f'<p class="muted">Of the '
            f"{_plural(gaps.unpaired_legs, 'round-up leg that has', 'round-up legs that have')} "
            "no pair in a Space, "
            f"{_plural(gaps.unpaired_on_reversed, 'is', 'are')} on a reversed or dropped payment, "
            f"{_plural(gaps.unpaired_to_unheld_space, 'goes', 'go')} to a Space whose rows "
            f"are not held, and {_plural(gaps.unpaired_other, 'is', 'are')} other.</p>"
            + _days_html(gaps.unpaired_days, gaps.unpaired_legs)
        )
    else:
        body += '<p class="muted">No round-up leg is without a pair in a Space.</p>'
    if gaps.space_in_unpaired:
        subject = _plural(
            gaps.space_in_unpaired,
            "incoming transfer leg in a Space has",
            "incoming transfer legs in a Space have",
        )
        body += (
            f'<p class="muted">{subject} '
            "no partner in the main account: round-ups the main feed did not report, or "
            "transfers whose main row is missing.</p>"
            + _days_html(gaps.space_in_unpaired_days, gaps.space_in_unpaired)
        )
    else:
        body += (
            '<p class="muted">No incoming transfer leg in a Space lacks a partner in the '
            "main account.</p>"
        )
    return body


def _family_html(family: Any, ref: str = "") -> str:
    """The walk of the whole account's stated balances against the rows of the
    main account and its Spaces together.

    Said once, here: what a family balance is, and what the main account's own
    balance is taken to be from it. The structure of its changes is
    `web_balance_chart.structure_summary_html`.
    """
    if family is None:
        return ""
    body = (
        "<h3>The whole account's known balances</h3>"
        '<p class="muted">A whole-account known balance is one stated by a source that '
        "cannot see this account's Spaces (the certified statement, the export, the "
        "aggregator), so it is the balance of the main account and every Space together "
        "and is checked against the rows of all of them, where a transfer between them "
        "cancels.</p>"
        '<p class="muted">This account\'s own balance is taken as that known balance less what '
        "each Space's rows sum to, which assumes every Space's rows start from nil: its "
        "history is held from its first row.</p>"
    )
    if family.withheld:
        return body + f'<p class="warn">Not walked: {_esc(family.withheld)}.</p>'
    # With the opened anchor every known balance is tested, so none is "later".
    which = "Ones" if family.nil_day else "Later ones"
    body += (
        '<div class="scroll"><table>'
        + _count("Spaces", ", ".join(family.spaces))
        + _count_html("Stated by", _sources_html(family.sources))
        + _count("Whole-account known balances", family.anchors)
        + _count(f"{which} the transactions add up to", family.agreeing)
        + _count(f"{which} the transactions do not add up to", family.differing)
        + (
            _count("Opened, with a nil balance at the end of", family.nil_day)
            if family.nil_day
            else _count("Earliest, from which the opening is worked out", family.defining_day)
        )
        + "</table></div>"
    )
    body += _opening_note(family)
    if family.before_opening:
        body += (
            f'<p class="warn"><strong>{_plural(family.before_opening, "row")} '
            f"{'is' if family.before_opening == 1 else 'are'} dated on "
            "or before the day the account opened.</strong> An account cannot move money "
            "before it exists, so either the creation date or those rows' dates are wrong, "
            "and the opening cannot be trusted until that is settled.</p>"
        )
    if family.unheld_legs:
        body += (
            f'<p class="warn"><strong>{_plural(family.unheld_legs, "transfer leg")} '
            f"{'goes' if family.unheld_legs == 1 else 'go'} "
            "to or from a Space whose own rows are not held, the first on "
            f'<span class="mono nowrap">{_esc(family.unheld_first)}</span>.</strong> The '
            "whole account cannot balance until that Space's rows are held."
        )
        if family.unheld_refused:
            body += (
                f" The provider refused the request for the history of "
                f"{_esc(str(family.unheld_refused))} of them on "
                f'<span class="mono nowrap">{_esc(family.unheld_refused_on)}</span>, so they '
                "cannot be fetched and recovering them will not help."
            )
        if family.unheld_empty:
            body += (
                f" The provider answered the request for the history of "
                f"{_esc(str(family.unheld_empty))} of them on "
                f'<span class="mono nowrap">{_esc(family.unheld_empty_on)}</span> with '
                "nothing."
            )
        if family.unheld_legs and not (family.unheld_refused or family.unheld_empty):
            body += (
                f" Declare it with the {code_html('recover-spaces')} command or "
                'the <a class="tap" href="/spaces">Spaces page</a> and bind its category in '
                "the account map, and the next pull fetches its history."
            )
        body += "</p>"
    body += _round_ups_html(family)
    if family.refused_figures:
        body += (
            f'<p class="warn">{_plural(family.refused_figures, "printed end-of-day balance")} '
            f"disagreed with {'its' if family.refused_figures == 1 else 'their'} own "
            f"statement's rows and {'was' if family.refused_figures == 1 else 'were'} "
            "not used.</p>"
        )
    if not family.anchors:
        return body
    if not family.differing:
        return body + (
            f'<p><span class="pill pill-ok">{ADDS_UP}</span> The transactions of the account '
            f"and its Spaces add up to every {'' if family.nil_day else 'later '}whole-account "
            "known balance.</p>"
        )
    pattern = (
        "The difference is the same at every later known balance, so one movement is "
        "missing or surplus between those two days."
        if family.pattern == "constant"
        else "The difference changes between later known balances, so more than one "
        "movement is missing or surplus."
    )
    body += (
        '<p class="warn"><strong>The transactions first stop adding up to the known balance '
        f'at the end of <span class="mono nowrap">{_esc(family.first_differing)}</span>; they '
        f'last added up at the end of <span class="mono nowrap">{_esc(family.last_agreeing)}'
        f"</span>.</strong> {_esc(pattern)}</p>"
        + _changes_html(family)
        + (structure_summary_html(family.structure, ref) if family.structure and ref else "")
        + _explanations_html(family.explanation)
        + '<ul class="anchors">'
    )
    for line in family.lines:
        side = "lower" if line.difference_direction == "out" else "higher"
        body += (
            f'<li><p><span class="pill pill-bad">differs</span> at the end of '
            f'<span class="mono nowrap">{_esc(line.day)}</span>: the known balance is '
            f'{side} than the rows predict by '
            f'<span class="mono nowrap">{_esc(line.difference)}</span></p>'
            f'<p class="muted">{_sources_html(line.sources)}</p></li>'
        )
    return body + "</ul>"


def _anchor_forms(view: Any, ref: str, month: str) -> str:
    """The forms that state or remove a balance.

    Nothing typed is ever put back into a form: the amount field is empty on
    every rendering, masked or not, because a pre-filled field is a value on
    a page reachable by address.
    """
    hidden = (
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
    )
    way_round = (
        "start with a minus sign if the account is overdrawn or owed"
        + (
            ", which a mortgage or any other loan always is"
            if view.opening.balance_only
            else ""
        )
    )
    save = (
        '<form method="post" action="/ledger-anchor">'
        + hidden
        + '<input type="hidden" name="currency" value="GBP">'
        '<p><label>Date the balance applies to, the end of that day<br>'
        '<input type="date" name="day" required></label></p>'
        '<p><label>Balance at the end of that day, in pounds and pence<br>'
        f'<span class="muted">{way_round}</span><br>'
        '<input name="amount" inputmode="decimal" autocomplete="off" required>'
        "</label></p>" + submit_button("Save known balance", secondary=True) + "</form>"
    )
    return _part(
        "State a balance",
        '<p class="muted">Stating a balance for a date already stated replaces it. '
        "The amount you type is never shown on any page you can bookmark; after "
        "saving, this page comes back masked.</p>" + save,
    )


def _remove_forms(view: Any, ref: str, month: str, *, unmasked: bool, everything: bool) -> str:
    """One button per balance a person stated, each asking for confirmation before it removes.

    They sit in the danger zone at the foot of the page, where the stylesheet outlines every
    button in red, so a thumb scrolling past them does not mistake one for the page's action.

    Only the balances the page lists have a button (`_listed_anchors`): a button for each of
    1,906 stated balances was as much of the page as the list was. The rest are removed from
    the full list, which this ends with a way to.
    """
    hidden = (
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
    )
    listed, left_out = _listed_anchors(view.opening.anchors, everything=everything)
    omitted = {line.day for line in view.opening.anchors} - {line.day for line in listed}
    forms = "".join(
        '<form method="post" action="/ledger-anchor-remove">'
        + hidden
        + f'<input type="hidden" name="day" value="{_esc(day)}">'
        + submit_button(f"Remove the known balance for the end of {day}", secondary=True)
        + "</form>"
        for day in view.opening.stated_days
        if day not in omitted
    )
    if not left_out:
        return forms
    return forms + _balances_control(
        view,
        "Remove an older known balance from the full list",
        unmasked=unmasked,
        everything=True,
        fragment=f"#{OPENING_ANCHOR}",
    )


def _removed_balances_html(view: Any, ref: str, month: str, unmasked: bool) -> str:
    """The known balances removed from the account, so one removed by mistake can be stated again.

    The dates and the times of removal are always shown. The figure, and the form that states it
    again, appear only on the values view: the masked page is reachable by address.
    """
    removed = view.removed_balances
    if not removed:
        return ""
    items = ""
    for item in removed:
        figure = ""
        again = ""
        if unmasked:
            minus = "minus " if item.balance_direction == "out" else ""
            figure = f' - it was <span class="mono nowrap">{_esc(minus + item.balance)}</span>'
            again = (
                '<form method="post" action="/ledger-anchor">'
                f'<input type="hidden" name="ref" value="{_esc(ref)}">'
                f'<input type="hidden" name="month" value="{_esc(month)}">'
                '<input type="hidden" name="currency" value="GBP">'
                f'<input type="hidden" name="day" value="{_esc(item.day)}">'
                f'<input type="hidden" name="amount" value="{_esc(item.restate_as)}">'
                + submit_button(f"State the balance for the end of {item.day} again")
                + "</form>"
            )
        items += (
            f'<li>The known balance for the end of <span class="mono nowrap">'
            f"{_esc(item.day)}</span> was removed at {_esc(item.removed_at)}{figure}.{again}</li>"
        )
    reveal = (
        ""
        if unmasked
        else '<p class="muted">Show values to read a removed balance back and state it again.</p>'
    )
    return _disclosure(f"Removed known balances ({len(removed)})", f"{reveal}<ul>{items}</ul>")


def _own_first_difference(anchors: tuple[Any, ...]) -> str:
    """Where the account's own anchors first stop agreeing with its own rows,
    reported apart from the whole-account walk (`_family_html`)."""
    for at, line in enumerate(anchors):
        if line.verdict == "differs":
            stated = (
                f"It is the {_esc(line.basis)} balance stated by {_esc(line.source)}"
                if line.source
                else f"It is a {_esc(line.basis)} balance"
            )
            walk = {
                "agrees": "the whole-account walk adds up at that balance from the same "
                "source, "
                "so the difference arises in the step from the whole account to this one's "
                "own (the Spaces' rows taken off)",
                "differs": "the whole-account walk differs at that same balance from the same "
                "source, so the cause is the one explained there",
                "": "the whole-account walk does not include that known balance, so it is "
                "tested against this account's rows alone",
            }[line.walk]
            return (
                '<p class="warn">The transactions first stop adding up to the account\'s own '
                f'known balances at the end of <span class="mono nowrap">{_esc(line.day)}</span>; '
                'they last added up at the end of '
                f'<span class="mono nowrap">{_esc(anchors[at - 1].day)}</span>.</p>'
                f"<p>{stated}, and {walk}.</p>"
            )
    return ""


def _meaning_html(meanings: tuple[Any, ...]) -> str:
    """For each source blind to the Spaces, what its own rows say its known
    balance means, in counts a person can check."""
    if not meanings:
        return ""
    percent = round(READING_THRESHOLD * 100)
    body = (
        "<h3>What each source's known balance means</h3>"
        '<p class="muted">A source that cannot see this account\'s Spaces states a balance '
        "that is either the whole account's (it moves with every payment it lists, "
        "including those from a Space) or the main account's own (it skips those and "
        "moves with each transfer to or from a Space). Each is tested against the "
        "source's own consecutive balances; only steps that tell the two apart count, "
        f"and a reading is adopted when it explains at least {percent}% of them.</p>"
    )
    for item in meanings:
        name = code_html(item.source)
        if item.verdict == "undecided":
            body += (
                f'<p class="warn">{name}: no step between its balances tells the two readings '
                "apart, so its balances are used for nothing.</p>"
            )
            continue
        counts = (
            f"{name}'s balance follows {item.main:,} of its {item.steps:,} steps when read as "
            f"the main account's own, and {item.whole:,} when read as the whole account's."
        )
        outcome = {
            "whole": "It is therefore read as the whole account's and tested against the "
            "rows of the main account and its Spaces together.",
            "main": "It is therefore read as the main account's own and tested against the "
            "main account's rows alone.",
            "both": "Both readings fit, so they cannot be told apart and its balances are "
            "used for nothing.",
            "neither": "Neither reading fits, so its balances are used for nothing.",
        }[item.verdict]
        css = "muted" if item.verdict in ("whole", "main") else "warn"
        body += f'<p class="{css}">{counts} {outcome}</p>'
    return body


def _bank_html(opening: Any) -> str:
    """What the bank's own landed balances were, how they were read, and what the
    newest says about the differences open elsewhere. The sentences are written by
    `bank_balances`, once; this only lays them out."""
    if not opening.bank_lines:
        return ""
    body = (
        "<h3>The bank's own balance</h3>"
        '<p class="muted">Starling states the account\'s balance with every pull. It is the '
        "bank's own figure and independent of the export and the statements, so it can say "
        "which side is wrong where they and the feed part company.</p>"
    )
    body += "".join(f'<p class="muted">{_esc(line)}</p>' for line in opening.bank_lines)
    body += "".join(f"<p><strong>{_esc(line)}</strong></p>" for line in opening.bank_sayings)
    return body


def _clearing_html(clearing: Any) -> str:
    """Cleared and uncleared rows in all, with the months a click away."""
    if clearing is None or not (clearing.cleared or clearing.uncleared):
        return ""
    months = "".join(
        f"<li>{_esc(m.month)}: {m.cleared} cleared, {m.uncleared} uncleared</li>"
        for m in clearing.months
    )
    return _part(
        f"Cleared and uncleared by month ({clearing.cleared} cleared, {clearing.uncleared} not)",
        f"<p>Across the account, {_plural(clearing.cleared, 'transaction')} "
        f"{agree(clearing.cleared, 'is')} cleared and {clearing.uncleared} "
        f"{agree(clearing.uncleared, 'is')} not. A transaction is cleared when a statement, an "
        "export, or the bank's own feed lists "
        "it; the aggregator alone does not clear one, and a pending transaction is never cleared."
        f'</p><ul class="plain">{months}</ul>',
    )


#: Where a person reads about a hold that is a movement fault and not a balance.
_MOVEMENT_HREF = "/identity-health"


def _hold(view: Any) -> tuple[str, str, str] | None:
    """What holds the account's own agreement back, for the thing to do that says it once: the
    sentence (`agreement.held_sentence`'s, said once there), where to read the explanation, and
    what the control says. A movement fault is explained on the identity-health page and a
    balance's difference in the known balances on this one."""
    standing = view.standing
    if standing is None or view.rebuilding:
        return None
    own = standing.own
    sentence = held_sentence(own)
    if not sentence or own.held is None:
        return None
    if own.held.kind == HELD_MOVEMENT:
        return sentence, _MOVEMENT_HREF, "See the movement checks"
    return sentence, f"#{OPENING_ANCHOR}", "See the explanation"


def _held_html(agreement: Any) -> str:
    """The whole account's hold and the note that nothing yet tests the rows, as plain lines in
    the known balances: the account's own hold is the thing to do."""
    sentence = held_sentence(agreement)
    if not sentence:
        return ""
    held = agreement.held
    if held is None:
        return f'<p class="sub">{_esc(sentence)}</p>'
    return f'<p class="warn">{_esc(sentence)}</p>'


def _month_start(month: str) -> date | None:
    return date(int(month[:4]), int(month[5:7]), 1) if month else None


def _month_end(month: str) -> date | None:
    start = _month_start(month)
    if start is None:
        return None
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _standing_detail_html(view: Any, *, held_said: bool) -> str:
    """What the standing says in detail, at the head of the known balances: where the
    transactions stop adding up and what that can mean, what holds the account back unless a
    thing to do has said so already (`held_said`), the balances a statement's own listing tests,
    and the whole account with its Spaces."""
    standing = view.standing
    if standing is None:
        return ""
    own = standing.own
    body = (
        _stretches_html(own)
        + (_held_html(own) if own.held is None or not held_said else "")
        + _listing_html(own)
        + (
            ""
            if own.movement_checked
            else '<p class="sub">The movement checks were not read for this view, so this is '
            "from the known balances alone.</p>"
        )
    )
    whole = standing.whole
    if whole is not None:
        body += (
            '<p class="sub">The whole account, with its Spaces:</p>'
            + line_html(whole, None, with_protection=False)
            + _held_html(whole)
        )
    return body


def _lock_offer_form(view: Any) -> str:
    """The one button that offers to lock in through the newest day offered. It asks before it
    acts (`LedgerPages._confirm_page`), so nothing here acts on a single tap."""
    protection = view.protection
    if protection is None or view.rebuilding or not protection.offer:
        return ""
    return _post("/protect", view.ref, _scope(view), _through(protection.offer[-1]), "Lock in")


def _locking_html(view: Any, unmasked: bool = False, everything: bool = False) -> str:
    """The fold for locking in: what is locked and its history, the way to remove the lock, and,
    where more than one day may be locked through, the choice of an earlier one. The offer to lock
    in through the newest day is a thing to do (`account_page.lock_offer_html`) and is not here.

    Every press goes to a confirmation first (`LedgerPages._confirm_page`), including the
    removal, so nothing here acts on a single tap.

    The earlier dates offered are the newest `_OFFERED_DATES` unless `everything` asks for the
    full list of known balances: the account whose page held 1,906 known balances offered a date
    for each, 92 kilobytes of a drop-down nobody scrolls.
    """
    protection = view.protection
    if protection is None or view.rebuilding:
        return ""
    ref, month = view.ref, _scope(view)
    body = f'<div id="{LOCKING_ANCHOR}">'
    state = protection.state
    gist = "nothing yet"
    if state == "intact":
        gist = f"to {protection.through.isoformat()}"
        detail = (
            f"<p>The locked stretch runs from {_esc(protection.span_start.isoformat())} to "
            f"{_esc(protection.through.isoformat())}. It is an alarm on change and never a "
            "freeze: a rebuild or an import still does what the rules say, and says here if "
            "that changed anything inside the locked stretch.</p>"
        )
        if protection.healed_on:
            detail += (
                f"<p>It broke on {_esc(protection.broken_on.isoformat())} and a later "
                f"derivation restored it on {_esc(protection.healed_on.isoformat())}.</p>"
            )
        if protection.accepted_on:
            detail += (
                f"<p>A change to it was accepted on {_esc(protection.accepted_on.isoformat())}."
                "</p>"
            )
        detail += f"<p>{_plural(protection.events, 'recorded event')} in its history.</p>"
        body += (
            f'<p class="protect-line">{_esc(_lock_line(protection))}</p>'
            + detail
            + _post("/protect-withdraw", ref, month, "", REMOVE_PROTECTION)
        )
    elif state == "broken":
        gist = f"to {protection.through.isoformat()}, changed"
        said = "".join(f"<li>{_esc(line)}</li>" for line in protection.changes)
        body += (
            '<p class="bad"><strong>The locked stretch, through '
            f"{_esc(protection.through.isoformat())}, has changed since "
            f"{_esc(protection.pressed_on.isoformat())}.</strong></p>"
            f'<ul class="plain">{said}</ul>'
            '<p class="muted">Nothing was changed back or updated: the lock stays broken '
            "until a later rebuild restores the locked stretch, or you accept the new "
            "state.</p>"
            + _post("/protect-accept", ref, month, "", "Accept the change and lock in again")
            + _post("/protect-withdraw", ref, month, "", REMOVE_PROTECTION)
        )
    else:
        body += (
            "<p>Nothing is locked in. Locking in says that you accept a stretch that adds up, "
            "and a later change inside it is reported loudly and never applied quietly.</p>"
        )
    if protection.earlier_said:
        body += (
            '<p class="warn"><strong>A fault in the data before the locked stretch, not a '
            f"change to it:</strong> {_esc(protection.earlier_said)}</p>"
        )
    offer = protection.offer
    if len(offer) > 1:
        dates = list(reversed(offer))
        held_back = len(dates) - _OFFERED_DATES
        if held_back > 0 and not everything:
            dates = dates[:_OFFERED_DATES]
        options = "".join(
            f'<option value="{_esc(d.isoformat())}">{_esc(d.isoformat())}</option>' for d in dates
        )
        older = (
            _balances_control(
                view,
                f"{_plural(held_back, 'older date')} offered with every known balance listed",
                unmasked=unmasked,
                everything=True,
            )
            if held_back > 0 and not everything
            else ""
        )
        body += _part(
            "Lock in to an earlier day",
            '<form method="post" action="/protect"><input type="hidden" name="ref" '
            f'value="{_esc(ref)}"><input type="hidden" name="month" value="{_esc(month)}">'
            f'<p><select name="through" aria-label="Lock in to">{options}</select></p>'
            + submit_button("Lock in to the day chosen", secondary=True)
            + "</form>"
            + older,
        )
    return _disclosure(f"Locking in ({gist})", body + "</div>", css="locking")


def _lock_line(protection: Any) -> str:
    """The one line an intact lock collapses to: through when, how many transactions, what it was
    tested against, and when."""
    return (
        f"Locked in to {protection.through.isoformat()}, on {protection.pressed_on.isoformat()}: "
        f"{_plural(protection.rows, 'transaction')}, added up against "
        f"{protection.verified_source}'s balance of {protection.verified_day.isoformat()}."
    )


def _explained_closing(line: Any, view: Any) -> bool:
    """True for a statement's closing balance that the page says it took to have closed before a
    transaction dated that day: it differs by date and is not a difference to look for."""
    standing = view.standing
    return (
        standing is not None
        and line.verdict == "differs"
        and line.basis == "statement"
        and line.day in {claim.day.isoformat() for claim in standing.own.closed_before}
    )


def _opening_gist(
    opening: Any, listing_tested: int = 0, closed_before_days: frozenset[str] = frozenset()
) -> str:
    """How the known balances stand, in a few words, for the summary that folds them away. A
    balance tested by its own statement's listing adds up though it sets the opening.

    A statement's closing balance that the page elsewhere says it took to have closed before a
    transaction dated that day (`agreement.closed_before_sentence`) is explained, not differing:
    it is counted under those words, so the heading never says "differ" of a balance the page
    has already accounted for.
    """
    if opening.state == "none":
        return "none stated"
    agrees = sum(1 for line in opening.anchors if line.verdict == "agrees") + listing_tested
    explained = sum(
        1
        for line in opening.anchors
        if line.verdict == "differs"
        and line.basis == "statement"
        and line.day in closed_before_days
    )
    differ = sum(1 for line in opening.anchors if line.verdict == "differs") - explained
    family = opening.family
    if not agrees and not differ and not explained and family is not None and family.anchors:
        agrees, differ = family.agreeing, family.differing
    parts = [f"{agrees:,} add up"]
    if differ or not explained:
        parts.append(f"{'none' if not differ else f'{differ:,}'} {agree(differ, 'differs')}")
    if explained:
        parts.append(f"{explained:,} closed before a transaction dated that day")
    return ", ".join(parts)


def _balances_control(
    view: Any, label: str, *, unmasked: bool, everything: bool, fragment: str = ""
) -> str:
    """The way to the full list of known balances (or back from it), as its own line.

    A link where the page is masked, since a masked page has an address. Where values are shown
    it is a posted button, so no address that opens a page of values exists to be kept.
    """
    if unmasked:
        extra = f'<input type="hidden" name="{BALANCES_PARAM}" value="{BALANCES_ALL}">'
        return (
            '<form method="post" action="/ledger">'
            f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
            f'<input type="hidden" name="month" value="{_esc(_scope(view))}">'
            f"{extra if everything else ''}" + submit_button(label, secondary=True) + "</form>"
        )
    params = {"ref": view.ref, **query_of(_scope(view))}
    if everything:
        params[BALANCES_PARAM] = BALANCES_ALL
    return f'<p><a class="tap" href="{_url("/ledger", **params)}{fragment}">{_esc(label)}</a></p>'


def _earlier_balances(view: Any, count: int, *, unmasked: bool, balance_only: bool) -> str:
    """The one line for the agreeing known balances that are not listed, linking to all of them."""
    if balance_only:
        state = f"{'all ' if count != 1 else ''}followed"
    else:
        state = f"which {ADDS_UP}" if count == 1 else "all add up"
    label = f"and {_plural(count, 'earlier known balance')}, {state}"
    return _balances_control(
        view, label, unmasked=unmasked, everything=True, fragment=f"#{OPENING_ANCHOR}"
    )


def _opening_html(
    view: Any,
    unmasked: bool,
    *,
    everything: bool = False,
    state_form: bool = True,
    held_said: bool = False,
) -> str:
    """The "Known balances" fold, and the forms that edit them: stating a balance (unless a thing
    to do holds that form already), removing one, and the ones removed.

    Folded away whatever the account's state: where it does not add up the thing to do says what
    stops it once and its control opens this fold at the explanation, and an account holding
    thirty-five known balances must not open them by itself. It lists the balances worth reading
    and links to the rest (`_listed_anchors`); `everything` lists them all, and is open because it
    is what a person asked for. A disregard that no longer applies is open too: it is a decision
    about nothing, which only this fold can remove.
    """
    opening = view.opening
    if opening is None:
        return ""

    def earlier(count: int) -> str:
        return _earlier_balances(view, count, unmasked=unmasked, balance_only=opening.balance_only)

    body = _standing_detail_html(view, held_said=held_said)
    if everything and _listed_anchors(opening.anchors, everything=False)[1]:
        body += _balances_control(
            view,
            "Every known balance is listed. Show only the newest",
            unmasked=unmasked,
            everything=False,
            fragment=f"#{OPENING_ANCHOR}",
        )
    if opening.state == "none":
        body += (
            '<p class="warn"><strong>No opening balance: the figures on this page '
            "start from zero.</strong> That is how they are counted, and it is not "
            "a claim that the account opened empty. No known balance has been stated for "
            "it, and neither the bank's records nor a held statement supplies one.</p>"
        )
    else:
        body += _anchors_html(
            opening.anchors,
            balance_only=opening.balance_only,
            unmasked=unmasked,
            everything=everything,
            earlier=earlier,
            notes=_date_notes(view),
        )
        if sum(1 for line in opening.anchors if line.basis == "statement") >= 2:
            body += (
                '<p class="muted"><a class="tap" '
                f'href="{_esc(account_address("periods", view.ref))}">'
                "Test the transactions between the statements, period by period</a></p>"
            )
        if opening.state == "derived":
            figure = _balance_figure(opening.direction, opening.opening, unmasked)
            body += (
                f'<p><strong>Opening balance, at the end of {_esc(opening.as_at)}:</strong> '
                f"{figure}. "
                + (
                    "The account opened with nothing, so no transaction has to be taken on trust."
                    if opening.anchors and opening.anchors[0].basis == "opened"
                    else "It is the earliest known balance less "
                    "the transactions dated on or before that day."
                )
                + "</p>"
            )
            tested = view.standing is not None and bool(view.standing.own.listing_tested)
            if opening.single_anchor and not opening.balance_only and tested:
                body += (
                    '<p class="muted">The one known balance is tested by its own statement: the '
                    "transactions that statement lists carry its opening balance to it.</p>"
                )
            elif opening.single_anchor and not opening.balance_only:
                body += (
                    '<p class="warn">An opening worked out from a single known balance absorbs '
                    "every missing or surplus transaction before that day into the opening "
                    "figure, and nothing here can tell. A second known balance turns it into a "
                    "test.</p>"
                )
            differing = sum(
                1
                for line in opening.anchors
                if line.verdict == "differs" and not _explained_closing(line, view)
            )
            if differing:
                body += (
                    f'<p class="warn"><strong>{_plural(differing, "later known balance")} '
                    f"{agree(differing, 'differs')}</strong> "
                    "from what the transactions add up to. Between the known balances a "
                    "transaction may be missing, counted twice, or dated on the wrong day.</p>"
                )
                body += _own_first_difference(opening.anchors)
                if opening.own_structure:
                    body += structure_summary_html(
                        opening.own_structure, view.ref, scope=OWN
                    )
                body += _explanations_html(opening.own_explanation)
        else:
            if any(line.verdict for line in opening.anchors):
                # Tested against nil (a Space's listings): the verdicts are the finding.
                body += _anchors_html(
                    opening.anchors,
                    balance_only=opening.balance_only,
                    unmasked=unmasked,
                    everything=everything,
                    earlier=earlier,
                    notes=_date_notes(view),
                )
                differing = sum(1 for line in opening.anchors if line.verdict == "differs")
                if differing:
                    body += (
                        f'<p class="warn"><strong>{_plural(differing, "known balance")} '
                        f"{agree(differing, 'differs')}</strong> "
                        "from what the transactions add up to. A transaction may be missing, "
                        "counted twice, or dated on the wrong day.</p>"
                    )
            body += (
                '<p class="warn"><strong>No opening balance could be derived:</strong> '
                f"{_esc(opening.withheld)}.</p>"
            )
    body += _shared_days_html(view)
    if opening.unusable_statements:
        body += (
            f'<p class="muted">{_plural(opening.unusable_statements, "held statement")} '
            "could not supply a balance - unreadable, or "
            f"{agree(opening.unusable_statements, 'its')} rows do not carry "
            f"{agree(opening.unusable_statements, 'its')} "
            "opening balance to its closing one - and "
            f"{agree(opening.unusable_statements, 'is')} not used.</p>"
        )
    body += (
        _bank_html(opening)
        + _meaning_html(opening.meanings)
        + _family_html(opening.family, view.ref)
    )
    stale = any(entry.stale for entry in opening.disregarded)
    body += (
        (_anchor_forms(view, view.ref, _scope(view)) if state_form else "")
        + _removed_balances_html(view, view.ref, _scope(view), unmasked)
        + _unitemised_html(view)
        + (
            _part(
                "Remove a known balance you stated",
                _remove_forms(
                    view, view.ref, _scope(view), unmasked=unmasked, everything=everything
                ),
            )
            if opening.stated_days
            else ""
        )
    )
    return _disclosure(
        "Known balances ("
        + _opening_gist(
            opening,
            len(view.standing.own.listing_tested) if view.standing is not None else 0,
            frozenset(claim.day.isoformat() for claim in view.standing.own.closed_before)
            if view.standing is not None
            else frozenset(),
        )
        + ")",
        f'<div id="{OPENING_ANCHOR}">{body}</div>',
        open=everything or stale,
    )


def _unitemised_html(view: Any) -> str:
    """The changes derived from a balance-only account's stated balances.

    Said once, here: what such a change is, and why the rows between two stated
    balances come off it. Nothing is listed for any other kind of account.
    """
    opening = view.opening
    if opening is None or not opening.balance_only:
        return ""
    body = (
        '<p class="muted">This account is tracked by its known balances. Between two '
        "consecutive ones the balance moved by the difference, less any rows dated "
        "between them, typed or otherwise; what is left is shown here as one change "
        "dated at the later balance. Where the rows explain the whole difference "
        "there is no change. They are worked out from the known balances each time "
        "and never stored, so removing or restating a balance changes them.</p>"
    )
    if not view.unitemised:
        stated = sum(1 for line in opening.anchors if line.basis == "stated")
        return _disclosure(
            "Unitemised changes (none)",
            body
            + "<p>None: "
            + (
                "the rows between the known balances explain every difference."
                if stated >= 2
                else "it takes two known balances for there to be a difference."
            )
            + "</p>",
        )
    items = "".join(
        "<li>"
        f'<div class="txn-head"><span class="mono nowrap">{_esc(line.day)}</span>'
        f'<span class="mono nowrap">'
        f"{_esc(_signed(line.direction, line.direction, line.amount))}</span></div>"
        + (
            f'<p class="muted">since the known balance for the end of {_esc(line.since)}</p>'
            if line.since
            else ""
        )
        + "</li>"
        for line in view.unitemised
    )
    return _disclosure(
        f"Unitemised changes ({len(view.unitemised)})", body + f'<ul class="txns">{items}</ul>'
    )


def _typed_html(view: Any, *, ref: str, month: str) -> str:
    """The form that types a transaction in, and the list it can be withdrawn from.

    Nothing typed is ever put back into the form: the figure and the description
    are empty on every rendering, masked or not, because a pre-filled field is a
    value on a page reachable by an address. Both controls are secondary: the
    page's primary action is showing values.
    """
    typed = view.typed
    if typed is None:
        return ""
    hidden = (
        f'<input type="hidden" name="ref" value="{_esc(ref)}">'
        f'<input type="hidden" name="month" value="{_esc(month)}">'
    )
    way_round = (
        "in raises the balance, which for a mortgage or any loan owed is a payment "
        "towards nil; out lowers it, which is interest or a further borrowing"
        if view.opening is not None and view.opening.balance_only
        else "in is money arriving in this account, out is money leaving it"
    )
    form = (
        '<form method="post" action="/ledger-typed">'
        + hidden
        + '<p><label>Date of the transaction<br>'
        '<input type="date" name="day" required></label></p>'
        '<p><label>Direction<br>'
        f'<span class="muted">{_esc(way_round)}</span><br>'
        '<select name="direction" required>'
        '<option value="" selected disabled>Choose in or out</option>'
        '<option value="in">in</option><option value="out">out</option>'
        "</select></label></p>"
        '<p><label>Figure, in pounds and pence, without a sign<br>'
        '<input name="amount" inputmode="decimal" autocomplete="off" required>'
        "</label></p>"
        '<p><label>Description<br>'
        '<input name="description" autocomplete="off" maxlength="140" required>'
        "</label></p>" + submit_button("Save typed transaction", secondary=True) + "</form>"
    )
    items = ""
    for line in typed.lines:
        figure = _esc(_signed(line.direction, line.direction, line.amount))
        if line.withdrawn:
            tail = '<p><span class="pill pill-quiet">removed</span></p>'
        else:
            tail = (
                '<form method="post" action="/ledger-typed-withdraw">'
                + hidden
                + f'<input type="hidden" name="entry" value="{_esc(line.entry_id)}">'
                + submit_button(REMOVE_TYPED_TRANSACTION, secondary=True)
                + "</form>"
            )
        items += (
            "<li>"
            '<div class="txn-head">'
            f'<span class="mono nowrap">{_esc(line.day)}</span>'
            f'<span class="mono nowrap">{figure}</span>'
            "</div>"
            f"<p><strong>{_esc(line.description)}</strong></p>"
            f"{tail}"
            "</li>"
        )
    notes = ""
    if typed.live_elsewhere:
        where = (
            "dated outside this window: choose a window or a month that holds it to remove one"
            if view.window_first
            else "dated in other months: step to that month to remove one"
        )
        notes += (
            f"<p class=\"muted\">{_plural(typed.live_elsewhere, 'more typed transaction')} "
            f"{agree(typed.live_elsewhere, 'is')} {where}.</p>"
        )
    if typed.withdrawn_total:
        notes += (
            f"<p class=\"muted\">{_plural(typed.withdrawn_total, 'typed transaction')} "
            "removed in all. They stay in the record as evidence and count nowhere.</p>"
        )
    return _disclosure(
        "Add a transaction by hand"
        + (
            f" ({len(typed.lines)} typed {'in this window' if view.window_first else 'this month'})"
            if typed.lines
            else ""
        ),
        '<p class="muted">For an account no feed reports, such as a mortgage at another '
        "bank. Each one is kept as evidence and counted like any other transaction, and "
        "becomes one transaction with the bank's if a feed later reports the same payment. "
        "The figure and description you type are never shown on any page you can "
        "bookmark; after saving, this page comes back masked.</p>"
        + form
        + (f'<ul class="txns">{items}</ul>' if items else "")
        + notes,
    )


def _position_html(position: Any, *, bound: bool, unmasked: bool) -> str:
    included = position.opening_included
    verdict = _NOTHING_SENT if not bound else f"The two positions {_differ(position.differs)}."

    def figure(direction: str, amount: str) -> str:
        # With the opening included the figure is a balance, sealed whole when masked like
        # every balance on the page; without it, a net movement, whose direction is shown.
        if included:
            return _balance_figure(direction, amount, unmasked)
        return _esc(_signed(_direction_word(direction), direction, amount))

    return _part("Running position", (
        '<div class="scroll"><table>'
        + _count("Counted through", position.through)
        + _count(
            "Transactions counted (void, counted elsewhere, and reversed excluded)",
            position.rows_counted,
        )
        + _count_html(
            "Balance by the transactions held"
            + (", plus the opening balance" if included else ""),
            figure(position.store_direction, position.store_balance),
        )
        + _count_html(
            "Balance by what would be sent to Actual",
            figure(position.sent_direction, position.sent_balance),
        )
        + "</table></div>"
        f"<p><strong>{_esc(verdict)}</strong> "
        + (
            "Both figures start from the account's opening balance, worked out and shown "
            "above."
            if included
            else "Neither figure includes an opening balance: both start from zero, "
            "which is not a claim that the account opened empty."
        )
        + "</p>"
    ))


_LIMITS = (
    '<ul class="muted">'
    "<li>A booked transaction that a source reported once and stopped reporting in later "
    "fetches covering the same dates is not detected yet. Void transactions are listed, "
    "because the store records a vanished pending payment; a vanished booked transaction "
    "leaves no record in the merged layer, so a page with no warning is not a "
    "pass.</li>"
    "<li>Transactions are dated by value date, the date Actual is sent.</li>"
    "<li>The Actual comparison is with what the payload builder would send now, "
    "not with what Actual currently holds.</li>"
    "</ul>"
)


def _month_links(view: Any, unmasked: bool) -> str:
    """Previous, next, and newest month, as text links that wrap on a phone.

    Stepping is a navigation and not the page's action, so it is never styled
    as the primary button.
    """
    ref = view.ref

    def step(label: str, target: str) -> str:
        if not unmasked:
            href = _url("/ledger", ref=ref, month=target)
            return f'<a class="tap" href="{href}">{_esc(label)}</a>'
        # Somebody reading several months of values asked for them once.
        # A link here would either drop them back to the masked view at every
        # step, or be an address that shows values; a posted form is neither.
        return (
            '<form method="post" action="/ledger">'
            f'<input type="hidden" name="ref" value="{_esc(ref)}">'
            f'<input type="hidden" name="month" value="{_esc(target)}">'
            f'<button class="tap" type="submit">{_esc(label)}</button>'
            "</form>"
        )

    steps = []
    if view.previous_month:
        steps.append(step(f"Previous month, {view.previous_month}", view.previous_month))
    if view.next_month:
        steps.append(step(f"Next month, {view.next_month}", view.next_month))
    if view.month and view.newest_month and view.newest_month != view.month:
        steps.append(step(f"Newest month with rows, {view.newest_month}", view.newest_month))
    return f'<div class="monthnav">{"".join(steps)}</div>' if steps else ""


_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
)  # fmt: skip


def _month_picker(view: Any, unmasked: bool) -> str:
    """Every year the account holds rows in, a row of twelve months each.

    A month with rows is a link (a button in a form where values are shown, so the address
    never carries a value and the reader is not dropped back to the masked view); one without
    is plain text, so a gap is seen and not tapped. The count of rows is the link's own small
    text, read from the counts the page already holds, so a month costs no statement.
    """
    counts = dict(view.month_counts)
    if not counts:
        return ""
    ref = view.ref
    years = sorted({month[:4] for month in counts})
    rows = ""
    for year in years:
        cells = ""
        for number, name in enumerate(_MONTH_NAMES, start=1):
            month = f"{year}-{number:02d}"
            held = counts.get(month, 0)
            if not held:
                cells += f'<li><span class="absent">{name}</span></li>'
                continue
            said = f"{name} {year}, {held} {'transaction' if held == 1 else 'transactions'}"
            here = ' aria-current="true"' if month == view.month else ""
            inner = f'{name}<small class="count">{held}</small>'
            if unmasked:
                cells += (
                    f'<li><button class="tap" type="submit" name="month" value="{month}" '
                    f'aria-label="{_esc(said)}"{here}>{inner}</button></li>'
                )
            else:
                href = _url("/ledger", ref=ref, month=month)
                cells += (
                    f'<li><a class="tap" href="{href}" aria-label="{_esc(said)}"{here}>'
                    f"{inner}</a></li>"
                )
        rows += (
            f'<div class="year"><span class="year-label mono">{year}</span>'
            f'<ol class="monthgrid">{cells}</ol></div>'
        )
    body = rows
    if unmasked:
        body = (
            '<form method="post" action="/ledger">'
            f'<input type="hidden" name="ref" value="{_esc(ref)}">{rows}</form>'
        )
    return _disclosure(
        f"Choose a month ({_esc(view.oldest_month)} to {_esc(view.newest_month)})",
        body,
        css="months",
    )


def _default_html(view: Any, key: str, default: str, *, can_set: bool) -> str:
    """What the default is, said quietly, and the one button that makes the window on show the
    default. A decision, so a POST that asks before it acts (`LedgerPages._window_default_post`).

    The newest calendar month is offered only while it is the one on show, since a month stepped
    back to is not what "the newest" means.
    """
    if key == MONTH_KEY and view.month != view.newest_month:
        key = ""
    said = f'<p class="muted">The default is: {_esc(label_of(default).lower())}.'
    if key == default:
        return said + " It is the one on show.</p>"
    said += "</p>"
    if not can_set or key not in DEFAULTABLE:
        return said
    return said + (
        '<form method="post" action="/ledger-window-default">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        f'<input type="hidden" name="month" value="{_esc(_scope(view))}">'
        f'<input type="hidden" name="default" value="{_esc(key)}">'
        + submit_button("Use this as the default", secondary=True)
        + "</form>"
    )


def _window_html(
    view: Any,
    unmasked: bool,
    window: WindowChoice | None,
    today: date,
    default: str,
    *,
    can_set: bool,
) -> str:
    """The fold that chooses which days are listed: the windows in one tap, a length to type, and
    two dates; the calendar months are the picker's, beside it.

    The control is the shared one (`window_control`), in a form of its own: a link-able GET while
    values are masked, and a POST while they are shown, so choosing a window never drops the
    values and no address that holds them exists. It opens by itself where a choice was refused,
    so the sentence saying why is read.
    """
    choice = window if window is not None else read_scope({}, today=today, default=default).choice
    controls = window_controls(
        choice,
        today=today.isoformat(),
        legend="Days listed",
        first_tap=FIRST_TAP,
        omit=OMITTED,
        show_now=False,
        extra=EXTRA_CHIPS,
    )
    form = (
        f'<form method="{"post" if unmasked else "get"}" action="/ledger">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">{controls}</form>'
    )
    return _disclosure(
        "Choose another window, or your own dates",
        form + _default_html(view, choice.key, default, can_set=can_set),
        open=bool(choice.refusal),
        css="windows",
    )


def _bars_html(view: Any) -> str:
    """The fold that says what the bars show, with the way to the full timeline, which stays on
    its own page and opens beside this one."""
    return _disclosure(
        "What the bars show, and the full timeline",
        key_html()
        + f'<p><a class="tap" href="{_esc(account_address("timeline", view.ref))}" '
        f'target="_blank" rel="noopener">{_esc(page_name("/coverage-timeline"))}, in a new '
        "tab</a></p>",
    )


def _mode(view: Any, unmasked: bool, everything: bool = False, *, pressing: bool = False) -> str:
    """The press that reads the values, beside the transactions it unmasks; outlined where the
    page has a more pressing action, since the page's one filled control is that action."""
    kept = (
        f'<input type="hidden" name="{BALANCES_PARAM}" value="{BALANCES_ALL}">'
        if everything
        else ""
    )
    around = {BALANCES_PARAM: BALANCES_ALL} if everything else {}
    if unmasked:
        return (
            '<p class="bad shown">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button secondary" '
            f'href="{_url("/ledger", ref=view.ref, **query_of(_scope(view)), **around)}">'
            "Hide values</a></p>"
        )
    # The sealed slots are the state; what masked means is said once, in "How this was checked".
    return (
        '<form method="post" action="/ledger">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        f'<input type="hidden" name="month" value="{_esc(_scope(view))}">'
        + kept
        + submit_button("Show values", secondary=pressing)
        + "</form>"
    )


def _masked_means_html() -> str:
    return _part(
        "What masked means",
        '<p class="sub">Values are masked: every digit shows as 9 and every letter '
        "as X, with length, case, and punctuation kept. A balance or a sum shows "
        f"as {MASKED_TOTAL} whatever its size, since its number of digits would "
        "say how much there is. Counts, dates, sources, directions, and flags "
        "are real.</p>",
    )


def _name(view: Any) -> str:
    """The account's own name, which leads the page; its reference where it has no name."""
    return str(view.label or view.ref)


def _archive_html(view: Any, *, archive_wired: bool) -> str:
    """The fold that renames or retires the account, the one place for what takes it out of use.
    The name is changed where accounts are declared, which this links to; each control here still
    asks for confirmation before it acts."""
    archive = (
        archive_controls(view.ref, view.archive, offer=view.state != "unknown", with_date=True)
        if archive_wired
        else ""
    )
    rename = (
        '<p>Change the name shown for this account in <a class="tap" '
        f'href="{_esc(account_address("edit", view.ref))}">its own form</a>, or see it among '
        f'<a class="tap" href="{_esc(account_address("list", view.ref))}">the declared '
        "accounts</a>.</p>"
    )
    return _disclosure("Rename or archive", rename + archive, css="ledger-danger")


def _closed_on(view: Any) -> date | None:
    """The day an archived account closed, where the page knows it; None for any other."""
    archive = view.archive
    if archive is None or archive.state != "archived":
        return None
    try:
        return date.fromisoformat(str(archive.closed))
    except ValueError:
        return None


def _head(view: Any) -> str:
    """Under the name, one muted line: the reference as code, and whether it is sent to Actual."""
    archive = view.archive
    archived = archive is not None and archive.state == "archived"
    return head_html(
        AccountShown.named(view.ref, view.label),
        sent=bool(view.actual_bound),
        archived=(
            f"archived {archive.closed} ({'inferred' if archive.inferred else 'stated'})"
            if archived
            else ""
        ),
    )


def _month_line(view: Any) -> str:
    """The month in one line: how many transactions, and how many a statement, an export, or the
    bank's own feed has cleared. Which sources reported them is said in "How this was checked"."""
    summary = view.summary
    rows = int(str(summary.rows))
    noun = "transaction" if rows == 1 else "transactions"
    copies = int(str(summary.folded))
    void = int(str(summary.void))
    counted = rows - copies - void
    cleared = int(str(summary.cleared))
    said = f"{rows} {noun}"
    if copies or void:
        said += f": {counted} counted, {_copies(copies)}" if copies else f": {counted} counted"
        said += f", {void} void" if void else ""
    said += "."
    if not cleared:
        said += " None is cleared yet."
    elif cleared == counted:
        said += " All are cleared."
    else:
        said += f" {cleared} {agree(cleared, 'is')} cleared."
    return f'<p class="txcount">{said}</p>'


def _copies_html(rows: list[str]) -> str:
    """The month's copies as one closed line at the foot of the list, and the rows beneath it.

    At the foot and not where each would fall: the counted rows then read as one run a person
    can lay beside their bank's app, which a copy between two of them would break.
    """
    if not rows:
        return ""
    return (
        '<details class="folded-rows">'
        f"<summary>{_copies(len(rows))} ({_COPIES_WHY})</summary>"
        f'<ul class="txns">{"".join(rows)}</ul></details>'
    )


def _statement_cost() -> str:
    return (
        f'<p class="muted">This page makes {QUERIES_PER_PAGE} database queries however '
        f"many transactions the account holds, plus {ANCHOR_QUERIES} to look for opening "
        "known balances and a few more for each held statement or bank record "
        "that has not been read yet. A main account with Spaces adds "
        f"{FAMILY_QUERIES} and one per Space to check the whole account's balances. "
        f"An account the bank's own feed fills adds {FEED_TIME_QUERIES} more to read the "
        "feed's times.</p>"
    )


def _frame(view: Any, *, notice: str, head: str, state: str, txns: str, more: str) -> bytes:
    """The page: the account's name as its heading, then four places the stylesheet arranges.

    On a phone they stack in this order. From 60rem the state and the folded sections sit in a
    narrow column and the month's transactions fill a wide one beside them, which is why they
    are separate elements and not one run of markup.
    """
    announced = f'<p class="ok"><strong>{_esc(notice)}</strong></p>' if notice else ""
    return render_page(
        "Ledger",
        announced
        + '<div class="acct-grid">'
        + f'<div class="acct-head">{head}</div>'
        + f'<div class="acct-state">{state}</div>'
        + f'<div class="acct-txns">{txns}</div>'
        + f'<div class="ledger-more">{more}</div>'
        + "</div>",
        heading=_name(view),
        body_class="ledger-page",
    )


def _state_html(
    view: Any, reading: AccountReading, today: date, *, hold: tuple[str, str, str] | None
) -> str:
    """The account's state, first on the page: the trust sentence, the strip, and what to do."""
    heading = f'<h2 class="visually-hidden">{ACCOUNT_CHECK_HEADING}</h2>'
    if view.rebuilding:
        return heading + f'<p class="warn">{_esc(view.rebuilding)}</p>'
    held = sum(count for _, count in view.month_counts)
    # An account that does not add up is asked to be fixed first: locking in what is checked is
    # offered again once nothing fails, and the earlier-day choice stays in the locking fold.
    failing = verification_of(reading.standing) == DOES_NOT_ADD_UP
    lock = "" if failing else lock_offer_html(view.protection, _lock_offer_form(view))
    todos = todos_html(reading, view.ref, _scope(view), today, hold=hold, lock=lock)
    return (
        heading
        + trust_html(
            reading, held_transactions=held, span=(view.oldest_month, view.newest_month)
        )
        + strip_html(reading, view.ref, today, closed=_closed_on(view))
        + todos
        + cannot_lock_yet_html(view.protection, reading.trust.adds_up_to)
    )


def _how_checked_html(
    view: Any, unmasked: bool, *, with_counts: bool, kept_statements: int | None = None
) -> str:
    """The fold that says how this was checked: how the sources' reports were matched, which of
    the statements add up by what they list, what is cleared, the month's counts, what this page
    does not check, and what masked means. Parts of one fold, so the page keeps five."""
    clock = (
        f'<p class="sub">{_esc(_CLOCK_NOTE)}</p>'
        if BANK_SOURCE in view.sources and view.state == "ok"
        else ""
    )
    position = (
        _position_html(view.position, bound=view.actual_bound, unmasked=unmasked)
        if view.position is not None
        else ""
    )
    counts = ""
    if with_counts:
        span = "window" if view.window_first else "month"
        counts = _part(
            f"This {span}'s counts and sums ({_plural(view.summary.rows, 'transaction')})",
            _summary_html(view.summary, bound=view.actual_bound, span=span) + position,
        )
        position = ""
    limits = _part(
        f"What this page does not check ({_LIMITS.count('<li>')})", _LIMITS + _statement_cost()
    )
    if view.state == "ok" and not view.running_shown:
        counts += (
            '<p class="muted">No balance is shown after each transaction, because the '
            "account has no known balance to count from.</p>"
        )
    fields = (
        f'<p><a class="tap" href="{_esc(account_address("account", view.ref))}">'
        f"{_esc(page_name('/account'))} for this account</a></p>"
    )
    kept = (
        f'<p><a class="tap" href="{_esc(account_address("statements", view.ref))}">'
        f"{_esc(_plural(kept_statements, 'statement'))} kept for this account</a></p>"
        if kept_statements is not None
        else ""
    )
    return _disclosure(
        "How this was checked",
        _joins_html(view.joins, clock)
        + _statements_score_html(view)
        + kept
        + _clearing_html(view.clearing)
        + counts
        + position
        + limits
        + _masked_means_html()
        + fields,
    )


def render_ledger(
    ledger: Ledger,
    *,
    unmasked: bool,
    archive_wired: bool = False,
    notice: str = "",
    today: date | None = None,
    all_balances: bool = False,
    reading: AccountReading | None = None,
    window: WindowChoice | None = None,
    window_default: str = DEFAULT_KEY,
    can_set_default: bool = False,
    kept_statements: int | None = None,
) -> bytes:
    """`notice` is a sentence about what the request just did, escaped here.

    `kept_statements` is how many statements are kept for the account, for the line that leads to
    the account's own list of them; without it the line is not drawn.

    `window` is the window as the request's control read it, which the control is set to and
    which says why a choice was refused; without it the control is set to the default window.

    `reading` is what the account's trust and things to do are drawn from
    (`account_page.read_account`). Without it the page reads the account from this ledger alone,
    which has no things to do and takes the account's first and newest transactions from its
    months, so a rendering holds no hook and does not depend on the store.

    `all_balances` lists every known balance and not the newest few (`_listed_anchors`).

    It is for a confirmation that names a date and a basis; a value must never
    be passed in it, since the masked rendering is the one that carries it.

    `today` is where the bars end. The handler passes the real day; without it they end with the
    newest month held, so a rendering does not depend on the clock.
    """
    view = Disclosed(ledger, unmasked=unmasked)
    head = _head(view)
    end = today or _month_end(view.newest_month) or date(2000, 1, 1)

    if view.state == "unknown":
        return _frame(
            view,
            notice=notice,
            head=head,
            state=(
                '<p class="bad"><strong>Unknown account.</strong> Nothing is held under '
                "this reference and no account is declared with it. This is not an "
                "empty account.</p>" + _HOME
            ),
            txns="",
            more="",
        )
    reading = reading or read_account(None, ledger, end)
    hold = _hold(view)
    confirming = any(t.kind == "confirm-balance" for t in reading.todos)
    offering = bool(view.protection is not None and view.protection.offer)
    pressing = bool(reading.todos) or hold is not None or offering
    more = (
        _bars_html(view)
        + _opening_html(
            view,
            unmasked,
            everything=all_balances,
            state_form=not confirming,
            held_said=hold_is_said(reading, hold),
        )
        + _locking_html(view, unmasked, all_balances)
        + _how_checked_html(
            view, unmasked, with_counts=view.state == "ok", kept_statements=kept_statements
        )
        + _archive_html(view, archive_wired=archive_wired)
    )
    typed = _typed_html(view, ref=view.ref, month=_scope(view))
    if view.state == "no-rows":
        return _frame(
            view,
            notice=notice,
            head=head,
            state=(
                '<p class="warn"><strong>This account holds no transactions at all.</strong> '
                "It is declared, but nothing has been imported or fetched for it, or "
                "its feed has been silent since it was set up. This is not a clean "
                "month.</p>" + _state_html(view, reading, end, hold=hold)
            ),
            txns=typed,
            more=more,
        )

    windowed = bool(view.window_first)
    words = view.window_words or "Window"
    if view.window_widened:
        taken = view.window_widened
        title = (
            f"Last {_plural(taken, 'transaction')}, {view.window_first} to {view.window_last} "
            f"(more than {words.removeprefix('Last ')}, so that {taken} "
            f"{agree(taken, 'is')} shown)"
        )
    elif windowed:
        title = f"{words}, {view.window_first} to {view.window_last}"
    else:
        title = view.month
    month = (
        '<div class="acct-month"><div class="txhead">'
        f"<h2>{_esc(title)}</h2>{_month_links(view, unmasked)}</div>"
        f"{_window_html(view, unmasked, window, end, window_default, can_set=can_set_default)}"
        f"{_month_picker(view, unmasked)}</div>"
    )
    if view.window_fell_back:
        month += (
            '<p class="muted">Nothing is dated in the '
            f"{_esc(view.window_words.lower())}, so this is the newest month with "
            "transactions.</p>"
        )
    if view.state == "empty-month":
        what = "window" if windowed else "month"
        month += (
            f'<p class="warn"><strong>No transactions are dated in this {what}.</strong> '
            f"The account holds transactions from {_esc(view.oldest_month)} to "
            f"{_esc(view.newest_month)}, so a quiet {what} here is a gap to explain, "
            "not a clean result.</p>"
        )
    else:
        month += _month_line(view)
    month += _mode(view, unmasked, all_balances, pressing=pressing)
    txns = month
    if view.state == "ok":
        counted = [row for row in view.rows if not _is_copy(row)]
        copies = [row for row in view.rows if _is_copy(row)]
        txns += (
            '<ul class="txns">'
            + "".join(_row_html(row, unmasked, running=view.running_shown) for row in counted)
            + "</ul>"
            + _copies_html(
                [_row_html(row, unmasked, running=view.running_shown) for row in copies]
            )
            + _OPEN_TARGETED_ROW
        )
    return _frame(
        view,
        notice=notice,
        head=head,
        state=_state_html(view, reading, end, hold=hold),
        txns=txns + typed,
        more=more,
    )


def _window_fields(fields: Mapping[str, list[str]]) -> dict[str, str]:
    """The window's own fields from a query or a form, and no others (`WINDOW_FIELDS`)."""
    return {name: fields[name][0] for name in WINDOW_FIELDS if fields.get(name)}


def _asks_for_all_balances(fields: dict[str, list[str]]) -> bool:
    return (fields.get(BALANCES_PARAM, [""])[0] or "").strip() == BALANCES_ALL


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class LedgerPages(AnswerPages):
    """The ledger's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _kept_statement_count(self, ref: str) -> int | None:
        """How many statements are kept for an account, or None where that is not wired or
        cannot be read: the page then draws no line rather than a false count."""
        hook = self.bound_config.kept_statement_count
        if hook is None:
            return None
        try:
            return hook(ref)
        except Exception:
            return None

    def _ledger_get(self, params: dict[str, list[str]]) -> None:
        # Nothing in the query string can unmask: only `ref`, `month`, the window's fields, and
        # which known balances to list are read, and the rendering is chosen by which method
        # was used.
        self._ledger(
            (params.get("ref", [""])[0] or "").strip(),
            (params.get("month", [""])[0] or "").strip(),
            unmasked=False,
            all_balances=_asks_for_all_balances(params),
            window_fields=_window_fields(params),
        )

    def _ledger_post(self, form: dict[str, list[str]]) -> None:
        self._ledger(
            (form.get("ref", [""])[0] or "").strip(),
            (form.get("month", [""])[0] or "").strip(),
            unmasked=True,
            all_balances=_asks_for_all_balances(form),
            window_fields=_window_fields(form),
        )

    def _anchor_refusal(self, status: int, title: str, message: str, *, ref: str = "") -> None:
        """A refusal that still offers the account's ledger first, where the account is known."""
        self._respond(
            status,
            render_page(title, f"{self.answer_link(ref)}<p>{_esc(message)}</p>{_HOME}"),
            no_store=True,
        )

    def _anchor_save_post(self, form: dict[str, list[str]]) -> None:
        """State (or restate) a balance, then answer with the MASKED ledger.

        The amount is handed to the hook and goes no further: not into the
        confirmation, not into a refusal, and not into the page that follows.
        That page is the masked one, so a person who wants to see what they
        saved asks for values the ordinary way.
        """
        hook = self.bound_config.anchor_save
        if hook is None:
            self._respond(404, _page("Not available", "Stating a balance is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        day = (form.get("day", [""])[0] or "").strip()
        try:
            hook(
                ref,
                day,
                form.get("amount", [""])[0] or "",
                (form.get("currency", ["GBP"])[0] or "GBP").strip(),
            )
        except DataError as exc:
            self._anchor_refusal(400, "Balance not saved", f"Nothing was saved. {exc}.", ref=ref)
            return
        except Exception as fault:
            # Not str(fault): an unexpected failure's text is not under this
            # module's control and could quote what was typed.
            say("ledger.anchor.save.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Balance not saved",
                "Nothing was saved, because of an unexpected fault.",
                ref=ref,
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            # No sentence on how the account stands: the standing readable in the same request
            # as the save is the one held before it (`account_standings` is held until the
            # derived layer moves), so a clause here said the old state as if it were the new.
            # The page beneath is where the new standing is said.
            notice=f"Saved: a known balance for the end of {day}. Nothing else changed.",
            no_store=True,
        )

    def _anchor_remove_post(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.anchor_remove
        if hook is None:
            self._respond(404, _page("Not available", "Removing a balance is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        day = (form.get("day", [""])[0] or "").strip()
        if (form.get("confirmed", [""])[0] or "") != "yes":
            # Asked like a protection is: a tap on the ledger's button changes nothing, and the
            # figure is never on the question, because the page is reachable by address.
            try:
                asked = parse_calendar_day(day).isoformat()
            except DataError as exc:
                self._anchor_refusal(
                    400, "Balance not removed", f"Nothing was removed. {exc}.", ref=ref
                )
                return
            self._confirm_page(
                "/ledger-anchor-remove",
                ref,
                month,
                f"Remove the known balance for the end of {asked}, stated by you? It stops "
                "counting at once. The figure is kept in the record of removed balances, "
                "readable on the values view, so it can be stated again.",
                "Remove the known balance",
                f'<input type="hidden" name="day" value="{_esc(asked)}">',
            )
            return
        before = self.answer_standing(ref)
        try:
            removed = hook(ref, day)
        except DataError as exc:
            self._anchor_refusal(
                400, "Balance not removed", f"Nothing was removed. {exc}.", ref=ref
            )
            return
        except Exception as fault:
            say("ledger.anchor.remove.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Balance not removed",
                "Nothing was removed, because of an unexpected fault.",
                ref=ref,
            )
            return
        if not removed:
            self._anchor_refusal(
                404,
                "No such known balance",
                f"No balance was stated for the end of {day}, so nothing was removed.",
                ref=ref,
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=self.answer_notice(
                f"Removed: the known balance for the end of {day}.", ref, before
            ),
            no_store=True,
        )

    def _balance_choice_post(
        self,
        form: dict[str, list[str]],
        *,
        hook_name: str,
        confirm_route: str,
        question: str,
        label: str,
        done: str,
        refused_title: str,
        nothing: str,
        asks: bool,
    ) -> None:
        """The shared shape of disregarding a known balance and using it again.

        Disregarding asks first (a tap on the ledger's button changes nothing); using again is
        undone as easily as it is done, so it acts at once. The balance is named by day, source,
        basis, and place, and no figure is on any page the route answers.
        """
        hook = getattr(self.bound_config, hook_name)
        if hook is None:
            self._respond(404, _page("Not available", "This is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        day = (form.get("day", [""])[0] or "").strip()
        source = (form.get("source", [""])[0] or "").strip()
        basis = (form.get("basis", [""])[0] or "").strip()
        try:
            asked = parse_calendar_day(day).isoformat()
            which = int((form.get("which", ["0"])[0] or "0").strip())
        except (DataError, ValueError) as exc:
            reason = exc if isinstance(exc, DataError) else "the balance asked for is not one"
            self._anchor_refusal(400, refused_title, f"Nothing was changed. {reason}.", ref=ref)
            return
        if asks and (form.get("confirmed", [""])[0] or "") != "yes":
            self._confirm_page(
                confirm_route,
                ref,
                month,
                question.format(day=asked, source=source),
                label,
                f'<input type="hidden" name="day" value="{_esc(asked)}">'
                f'<input type="hidden" name="source" value="{_esc(source)}">'
                f'<input type="hidden" name="basis" value="{_esc(basis)}">'
                f'<input type="hidden" name="which" value="{which}">',
            )
            return
        before = self.answer_standing(ref)
        try:
            changed = hook(ref, asked, source, basis, which)
        except DataError as exc:
            self._anchor_refusal(400, refused_title, f"Nothing was changed. {exc}.", ref=ref)
            return
        except Exception as fault:
            say(f"ledger.{hook_name}.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                refused_title,
                "Nothing was changed, because of an unexpected fault.",
                ref=ref,
            )
            return
        if not changed:
            self._anchor_refusal(404, refused_title, nothing.format(day=asked), ref=ref)
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=self.answer_notice(done.format(day=asked, source=source), ref, before),
            no_store=True,
        )

    def _balance_disregard_post(self, form: dict[str, list[str]]) -> None:
        self._balance_choice_post(
            form,
            hook_name="balance_disregard",
            confirm_route="/ledger-balance-disregard",
            question=(
                "Disregard the balance {source} states for the end of {day}? It takes no part in "
                "any check or any conflict, and every other balance for that day is kept. It "
                "stays on the account page, marked, and can be used again."
            ),
            label="Disregard the balance",
            done="Disregarded: the balance {source} states for the end of {day}.",
            refused_title="Balance not disregarded",
            nothing="Nothing was disregarded: no such known balance is held for the end of {day}.",
            asks=True,
        )

    def _balance_use_again_post(self, form: dict[str, list[str]]) -> None:
        self._balance_choice_post(
            form,
            hook_name="balance_use_again",
            confirm_route="/ledger-balance-use-again",
            question="",
            label="Use the balance again",
            done="Used again: the balance {source} states for the end of {day}.",
            refused_title="Balance not used again",
            nothing="Nothing changed: no balance was disregarded for the end of {day}.",
            asks=False,
        )

    def _window_default_post(self, form: dict[str, list[str]]) -> None:
        """Make the window on show the one every account's page opens on.

        Asks first, since it changes every account's page and not this one alone; the key is
        checked before it asks, so a value that is not a window is refused at once. Nothing
        but a window's name is kept or shown.
        """
        hook = self.bound_config.window_default_set
        if hook is None:
            self._respond(404, _page("Not available", "Setting the default is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        key = (form.get("default", [""])[0] or "").strip()
        if key not in DEFAULTABLE:
            self._anchor_refusal(
                400,
                "Default window not set",
                "Nothing was changed. That is not a window the page can open on.",
                ref=ref,
            )
            return
        if (form.get("confirmed", [""])[0] or "") != "yes":
            self._confirm_page(
                "/ledger-window-default",
                ref,
                month,
                f"Open every account's page on {label_of(key).lower()}, whenever the address "
                "names no window?",
                "Use this as the default",
                f'<input type="hidden" name="default" value="{_esc(key)}">',
            )
            return
        try:
            hook(key)
        except DataError as exc:
            self._anchor_refusal(400, "Default window not set", f"Nothing was changed. {exc}.")
            return
        except Exception as fault:
            say("ledger.window_default.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Default window not set",
                "Nothing was changed, because of an unexpected fault.",
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=f"Saved: every account's page now opens on {label_of(key).lower()}.",
            no_store=True,
        )

    def _confirm_page(
        self, action: str, ref: str, month: str, question: str, label: str, extra: str = ""
    ) -> None:
        """Ask "are you sure" before a protection is made, accepted, or withdrawn.

        The route answers its first POST with this and changes nothing; only the form here, which
        carries `confirmed`, acts. Every protection press goes through it, because what the
        owner's manual flow asked for was a lock whose release is behind an are-you-sure.
        """
        back = _url("/ledger", ref=ref, **query_of(month))
        self._respond(
            200,
            render_page(
                "Are you sure?",
                f"<p><strong>{_esc(question)}</strong></p>"
                f'<form method="post" action="{action}">'
                f'<input type="hidden" name="ref" value="{_esc(ref)}">'
                f'<input type="hidden" name="month" value="{_esc(month)}">'
                f'<input type="hidden" name="confirmed" value="yes">{extra}'
                + submit_button(label)
                + "</form>"
                f'<p><a class="tap" href="{back}">No, go back to the ledger</a></p>',
            ),
            no_store=True,
        )

    def _protection_action(
        self,
        form: dict[str, list[str]],
        *,
        hook_name: str,
        refused_title: str,
        refused_lead: str,
        done: str,
        question: str,
        label: str,
        with_through: bool = False,
    ) -> None:
        """The shared shape of the three protection POSTs: confirm first, then act, then answer
        with the MASKED ledger. Nothing typed is echoed except a date that has been read as one."""
        hook = getattr(self.bound_config, hook_name)
        if hook is None:
            self._respond(404, _page("Not available", "Locking in is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        through = ""
        extra = ""
        if with_through:
            try:
                day = parse_calendar_day((form.get("through", [""])[0] or "").strip())
            except DataError as exc:
                self._anchor_refusal(400, refused_title, f"{refused_lead} {exc}.", ref=ref)
                return
            through = day.isoformat()
            extra = f'<input type="hidden" name="through" value="{_esc(through)}">'
        if (form.get("confirmed", [""])[0] or "") != "yes":
            self._confirm_page(
                self._route_of(hook_name),
                ref,
                month,
                question.format(through=through),
                label,
                extra,
            )
            return
        try:
            hook(ref, through) if with_through else hook(ref)
        except DataError as exc:
            self._anchor_refusal(400, refused_title, f"{refused_lead} {exc}.", ref=ref)
            return
        except Exception as fault:
            say(f"ledger.{hook_name}.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                refused_title,
                f"{refused_lead} An unexpected fault stopped it.",
                ref=ref,
            )
            return
        self._ledger(
            ref, month, unmasked=False, notice=done.format(through=through), no_store=True
        )

    @staticmethod
    def _route_of(hook_name: str) -> str:
        return {
            "protect": "/protect",
            "protect_withdraw": "/protect-withdraw",
            "protect_accept": "/protect-accept",
        }[hook_name]

    def _protect_post(self, form: dict[str, list[str]]) -> None:
        self._protection_action(
            form,
            hook_name="protect",
            refused_title="Not locked in",
            refused_lead="Nothing was locked in.",
            done="Locked in to {through}. It is an alarm on change and never a freeze.",
            question=(
                "Lock in this account to {through}? From now on a rebuild, import, or "
                "pull that adds, removes, or changes any transaction dated up to then, or "
                "re-pairs one of its transfers, is reported loudly as a change to what you "
                "locked in. Nothing is blocked: the change still happens."
            ),
            label="Lock in",
            with_through=True,
        )

    def _protect_withdraw_post(self, form: dict[str, list[str]]) -> None:
        self._protection_action(
            form,
            hook_name="protect_withdraw",
            refused_title="Lock not removed",
            refused_lead="Nothing was removed.",
            done=f"{PROTECTION_REMOVED}: the account's lock. The removal is in its history.",
            question=(
                "Remove this account's lock? Changes to its transactions will no longer be "
                "reported."
            ),
            label=REMOVE_PROTECTION,
        )

    def _protect_accept_post(self, form: dict[str, list[str]]) -> None:
        self._protection_action(
            form,
            hook_name="protect_accept",
            refused_title="Change not accepted",
            refused_lead="Nothing was accepted.",
            done="Accepted: the locked stretch is now as it stands, and the acceptance is "
            "recorded beside the original.",
            question=(
                "Accept the change and lock in again? The lock will describe the stretch as it "
                "is now. The original and this acceptance both stay in its history."
            ),
            label="Accept the change and lock in again",
        )

    def _typed_save_post(self, form: dict[str, list[str]]) -> None:
        """Type a transaction in, then answer with the MASKED ledger.

        The figure and the description go to the hook and no further: not into
        the confirmation, not into a refusal, and not into the page that follows.
        The page that follows is shown on the window it was typed from, or at the month
        the transaction is dated in where it was typed from a month; that date is the only
        part of the entry that is echoed, and only once the hook has accepted it as a real
        date.
        """
        hook = self.bound_config.typed_save
        if hook is None:
            self._respond(404, _page("Not available", "Typing a transaction is not wired."))
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        day = (form.get("day", [""])[0] or "").strip()
        before = self.answer_standing(ref)
        try:
            hook(
                ref,
                day,
                (form.get("direction", [""])[0] or "").strip(),
                form.get("amount", [""])[0] or "",
                form.get("description", [""])[0] or "",
            )
        except DataError as exc:
            self._anchor_refusal(
                400, "Transaction not saved", f"Nothing was saved. {exc}.", ref=ref
            )
            return
        except Exception as fault:
            say("ledger.typed.save.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Transaction not saved",
                "Nothing was saved, because of an unexpected fault.",
                ref=ref,
            )
            return
        asked = (form.get("month", [""])[0] or "").strip()
        self._ledger(
            ref,
            # A page on a window stays on it, and one on a month goes to the month the entry is
            # dated in; the entry's date is echoed only once the hook has accepted it.
            asked if is_window_token(asked) else day[:7],
            unmasked=False,
            notice=self.answer_notice(
                f"Saved: a typed transaction dated {day}. Nothing else changed.", ref, before
            ),
            no_store=True,
        )

    def _typed_withdraw_post(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.typed_withdraw
        if hook is None:
            self._respond(
                404, _page("Not available", "Removing a typed transaction is not wired.")
            )
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        before = self.answer_standing(ref)
        try:
            hook(ref, (form.get("entry", [""])[0] or "").strip())
        except DataError as exc:
            self._anchor_refusal(
                400, "Transaction not removed", f"Nothing was removed. {exc}.", ref=ref
            )
            return
        except Exception as fault:
            say("ledger.typed.withdraw.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Transaction not removed",
                "Nothing was removed, because of an unexpected fault.",
                ref=ref,
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=self.answer_notice(
                f"{TYPED_TRANSACTION_REMOVED}. It stays in the record as evidence "
                "and counts nowhere.",
                ref,
                before,
            ),
            no_store=True,
        )

    def _ledger(
        self,
        ref: str,
        month: str,
        *,
        unmasked: bool,
        notice: str = "",
        no_store: bool = False,
        all_balances: bool = False,
        window_fields: Mapping[str, str] | None = None,
    ) -> None:
        """`month` is the scope token the page's forms carry (`ledger_scope`): a month, nothing,
        or a window; `window_fields` are the window's fields where the request gave them
        directly, as the control and an address do."""
        hook = self.bound_config.ledger_data
        if hook is None:
            self._respond(404, _page("Not available", "No ledger is wired."))
            return
        if not ref:
            self._respond(400, _page("No account named", "Say which account with ?ref=."))
            return
        today = datetime.now(UTC).date()
        default = DEFAULT_KEY
        if self.bound_config.window_default is not None:
            try:
                default = self.bound_config.window_default()
            except Exception as exc:
                self._respond(500, _page("Default window failed", str(exc)))
                return
        scope = read_scope({"month": month, **(window_fields or {})}, today=today, default=default)
        try:
            window = scope.window_for(today)
            ledger = hook(ref, scope.month) if window is None else hook(ref, "", window)
        except LedgerRequestError as exc:
            self._respond(400, _page("Not a month", str(exc)))
            return
        except Exception as exc:
            self._respond(500, _page("Ledger failed", str(exc)))
            return
        ledger = replace(ledger, scope=scope.token, window_words=scope.words(today))
        self._respond(
            404 if ledger.state == "unknown" else 200,
            render_ledger(
                ledger,
                unmasked=unmasked,
                archive_wired=self.bound_config.archive_account is not None,
                notice=notice,
                today=today,
                all_balances=all_balances,
                reading=(
                    read_account(self.bound_config, ledger, today)
                    if ledger.state != "unknown"
                    else None
                ),
                window=scope.choice,
                window_default=default,
                can_set_default=self.bound_config.window_default_set is not None,
                kept_statements=self._kept_statement_count(ref),
            ),
            no_store=unmasked or no_store,
        )
