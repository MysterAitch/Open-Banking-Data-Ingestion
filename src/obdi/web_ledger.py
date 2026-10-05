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
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .account_names import code_html
from .agreement import (
    HELD_MOVEMENT,
    NONE,
    STRETCH_MEANINGS,
    held_sentence,
    standing_line,
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
from .logs import say
from .london_clock import london
from .masking import MASKED_TOTAL, Disclosed
from .models import BASIS_ID
from .navigation import page_name
from .page_words import (
    PROTECTION_REMOVED,
    REMOVE_PROTECTION,
    REMOVE_TYPED_TRANSACTION,
    TYPED_TRANSACTION_REMOVED,
)
from .plural import agree
from .plural import plural as _plural
from .proof_rail import build_rail, rail_svg
from .protection import protection_line
from .standing_data import ADDS_UP
from .web_account_timeline import block_for
from .web_accounts import archive_controls, archive_label, submit_button
from .web_answers import AnswerPages
from .web_balance_chart import structure_summary_html
from .web_standing import _post, _through, line_html

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


def _url(path: str, **params: str) -> str:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return _esc(f"{path}?{query}")


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
_COPY_CHIP = "copy, not counted"
_COPIES_WHY = "held under a Space, or itemised by a statement"


def _copies(count: int) -> str:
    """A count of copies with its noun, which names what they are: "3 copies not counted"."""
    return _plural(count, "copy not counted", "copies not counted")


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
            "one source",
            "More than one source feeds this account and only one of them has "
            "reported this row, so nothing else confirms it yet.",
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
            f'<span class="pill pill-quiet" title="Actual is not sent this row, and no sum counts '
            f'it: it is a copy of a payment {_esc(_COPIES_WHY)}.">{_COPY_CHIP}</span>'
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
    return f'<p class="muted">{code_html(sighting.source)} - {_esc(how)}{tail}</p>'


def _line_html(row: Any, line: str) -> str:
    """The row's line, and what each source stated of it one tap away.

    Times are London time, the clock the rest of the page uses (`_CLOCK_NOTE`); a date is as stated.

    The whole line is the disclosure's own summary, so the row is its own tap target and takes no
    line for a control (a month of fifty rows was a third taller with the disclosure on a line of
    its own). The summary's name is the line followed by "Dates and joins", and the line carries
    the row's date, so no two rows' summaries read alike. A row nothing sighted has nothing to
    open and carries the line plain.
    """
    if not row.sightings:
        return f'<div class="t-row">{line}</div>'
    lines = "".join(_sighting_line(sighting) for sighting in row.sightings)
    return (
        f'<details class="t-more"><summary class="t-row">{line}'
        '<span class="visually-hidden">Dates and joins</span>'
        f"</summary>{lines}</details>"
    )


def _joins_html(joins: Any, clock: str = "") -> str:
    """The account's rows by how they joined, and the guessed ones' dates a click away.

    `clock` is the sentence about the times a row states, which is about the same detail a row's
    "Dates and joins" opens, and so is said here rather than in a disclosure of its own.
    """
    counts = dict(joins.by_basis) if joins is not None else {}
    sentence = count_sentence(counts)
    if not sentence:
        return _disclosure("About the times shown", clock) if clock else ""
    guessed = joins.heuristic_days
    listing = (
        f"<details><summary>{len(guessed)} joined on a guess from amount, date, and "
        "description: the dates</summary>"
        "<p>" + ", ".join(_mono(day) for day in guessed) + "</p></details>"
        if guessed
        else '<p class="muted">No row was joined on a guess.</p>'
    )
    return _disclosure(
        f"How the rows were joined ({_esc(_joined_gist(counts))})",
        f"<p>{_esc(sentence[0].upper() + sentence[1:])}.</p>{listing}{clock}",
    )


def _joined_gist(counts: dict[str, int]) -> str:
    """The joins worth a glance, said short: those other than by the source's own id."""
    labels = dict(COUNT_LABELS)
    parts = [
        f"{counts[basis]} {label.removeprefix('joined ')}"
        for basis, label in labels.items()
        if counts.get(basis) and basis != BASIS_ID
    ]
    return ", ".join(parts) or "all by id"


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


def _row_html(row: Any, unmasked: bool = True) -> str:
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
        f'<br><span class="muted txt{seal}">{_esc(row.counterparty)}</span>'
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
    ident = f' id="row-{_esc(row.anchor)}"' if row.anchor else ""
    line = (
        f'<span class="t-desc"><strong class="txt{seal}">{_esc(row.description)}</strong>'
        f"{counterparty}</span>"
        f'<span class="t-fig mono nowrap fig{seal}">{figure}</span>'
        '<span class="t-meta">'
        f'<span class="t-when mono nowrap">{_esc(row.dated.isoformat())}{at}</span>'
        f'<span class="t-chips pills">{_status_pill(row)} {sources}{_row_flags(row)}</span>'
        "</span>"
    )
    kind = " folded" if _is_copy(row) else ""
    return (
        f'<li class="txn{kind}{_row_rail(row)}"{ident}>'
        f"{_line_html(row, line)}{dates}{annotation}</li>"
    )


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


def _summary_html(summary: Any, *, bound: bool) -> str:
    """The month's counts: the always-meaningful rows, then only the non-zero flags.

    A zero is not dropped silently.
    The sentence after the table names every flag count that is zero, so a
    reader can tell "nothing of that kind" from "not looked for".
    """
    reasons = _pairs(summary.withheld_by_reason)
    rows = _count("Rows in the month", summary.rows) + _count_html(
        "Rows per source", _source_pairs(summary.per_source)
    )
    zero: list[str] = []
    for field, label, phrase in _FLAG_COUNTS:
        number = getattr(summary, field)
        if number:
            rows += _count(label, number)
        else:
            zero.append(phrase)
    rows += _count("Cleared rows", summary.cleared)
    rows += _count("Uncleared rows", summary.uncleared)
    rows += _count("Would be sent to Actual", summary.would_send)
    rows += _count("Withheld from Actual", f"{summary.withheld} ({reasons})")
    if summary.unsendable:
        rows += _count("Refused by the push builder", summary.unsendable)
    else:
        zero.append("refused by the push builder")
    verdict = (
        _NOTHING_SENT
        if not bound
        else f"The two month sums {_differ(summary.sums_differ)}."
    )
    return (
        '<div class="scroll"><table>'
        + rows
        + _count(
            "Sum of the store's rows (void, folded, and reversed excluded)",
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
        + (f'<p class="muted">None this month: {_words(zero)}.</p>' if zero else "")
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
    "family": "the whole account's known balance, less its Spaces' own rows",
    "opened": "the day before the account was created, with its feed held from then",
    "export": "the export's own balance, read as the main account's",
    "assumed-nil": "assumed, not shown: a Space's rows are taken to start from nil",
}


def _balance_word(direction: str) -> str:
    return _BALANCE_WORDS.get(direction, direction)


def _anchor_row(line: Any, *, balance_only: bool = False, unmasked: bool = True) -> str:
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
        difference = _signed(
            _balance_word(line.difference_direction), line.difference_direction, line.difference
        )
        detail = (
            f' from what the rows predict by <span class="fig{_seal(unmasked)}">'
            f"{_esc(difference)}</span>"
        )
    basis = _BASIS_WORDS.get(line.basis, line.basis)
    if line.balance_direction == "nil":
        balance = "nil"
    else:
        balance = (
            f"{_esc(_balance_word(line.balance_direction))} "
            f'<span class="mono nowrap fig{_seal(unmasked)}">{_esc(line.balance)}</span>'
        )
    return (
        "<li>"
        f"<p>{role}{detail}</p>"
        f'<p>End of <span class="mono nowrap">{_esc(line.day)}</span>: {balance}</p>'
        f'<p class="muted">{_esc(basis)}</p>'
        "</li>"
    )


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
        said += _disclosure(
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


def _shared_days_html(view: Any) -> str:
    """The balances several sources state for one day, side by side, with the way to disregard one.

    Whether the figures are equal is said and never what they are. Disregarding keeps the balance
    on the page, marked, and takes it out of every stretch and every conflict; a balance used
    again is read as before.
    """
    opening = view.opening
    if not opening.shared_days and not opening.disregarded:
        return ""
    ref, month = view.ref, view.month
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
            said = (
                f'<p>End of <span class="mono nowrap">{_esc(day.day)}</span>: '
                f"{_esc(_FIGURES_SAID.get(day.figures, ''))}</p><ul>{items}</ul>"
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
) -> str:
    """The known balances worth listing (`_listed_anchors`), then one line for the earlier ones.

    `earlier` makes that line from the count left out, as a link to the full list; without it a
    long run is simply listed whole.
    """
    listed, left_out = _listed_anchors(anchors, everything=everything or earlier is None)
    items = "".join(
        _anchor_row(line, balance_only=balance_only, unmasked=unmasked) for line in listed
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
    "the row keeps the feed's own (UTC) date and counts toward that day."
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
            return f"{lead}, sighted on this row"
        if found.sighted_on == "a row of another account":
            return f"{lead}, sighted on a row of another account"
        other = _row_note(found.other) if found.other is not None else ""
        return f"{lead}, sighted on another stored row ({other})"
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
            f"{'row' if change.export_rows == 1 else 'rows'} in the window and the store "
            f"holds {change.store_sightings} "
            f"{'sighting' if change.store_sightings == 1 else 'sightings'} of it there: "
            "identical rows collapsed into one, or a row sighted on a different day.</p>"
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
            f"{_plural(facts.rows, 'row')}. In its own sequence {facts.out_of_order:,} "
            f"{'is' if facts.out_of_order == 1 else 'are'} out of date order, "
            f"{_plural(facts.uncut_days, 'day')} hold a row but have no clean cut and so state "
            f"no balance, and {facts.unsighted:,} "
            f"{'has' if facts.unsighted == 1 else 'have'} no sighting in the store.</p>"
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
        "</label></p>" + submit_button("Save known balance") + "</form>"
    )
    return _disclosure(
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
    return _disclosure(
        f"Cleared and uncleared by month ({clearing.cleared} cleared, {clearing.uncleared} not)",
        f"<p>Across the account, {_plural(clearing.cleared, 'row')} "
        f"{agree(clearing.cleared, 'is')} cleared and {clearing.uncleared} "
        f"{agree(clearing.uncleared, 'is')} not. A row is cleared when a statement, an "
        "export, or the bank's own feed lists "
        "it; the aggregator alone does not clear a row, and a pending row is never cleared."
        f'</p><ul class="plain">{months}</ul>',
    )


#: Where a person reads about a hold that is a movement fault and not a balance.
_MOVEMENT_HREF = "/identity-health"


def _held_html(agreement: Any, *, boxed: bool) -> str:
    """What holds the agreement back, and the way to its explanation on this very page.

    The sentence is `agreement.held_sentence`'s, said once there. A hold is a tinted box and
    nothing else on the page is; a whole-account reading's hold, and the note that nothing yet
    tests the rows, are plain lines, since the account's own hold is the one to act on.
    """
    sentence = held_sentence(agreement)
    if not sentence:
        return ""
    held = agreement.held
    if held is None:
        return f'<p class="sub">{_esc(sentence)}</p>'
    link = (
        f'<a class="tap" href="{_MOVEMENT_HREF}">Movement checks</a>'
        if held.kind == HELD_MOVEMENT
        else '<a class="tap" href="#opening">See the explanation</a>'
    )
    if not boxed:
        return f'<p class="warn">{_esc(sentence)} {link}</p>'
    return f'<div class="held"><p><strong>{_esc(sentence)}</strong> {link}</p></div>'


def _verdict_text(own: Any, protection: Any) -> str:
    """The standing sentence, ending in the protection in the page's own words.

    `agreement.standing_line` says "not protected" of an account nothing protects; here a
    broken protection says so instead of claiming a date it no longer holds.
    """
    if own.state == NONE:
        return standing_line(own, None)
    line = standing_line(own, None, with_protection=False)[:-1]
    state = "none" if protection is None else protection.state
    if state == "intact":
        return f"{line}; protected through {protection.through.isoformat()}."
    if state == "broken":
        return f"{line}; the protection through {protection.through.isoformat()} is broken."
    return f"{line}; not protected."


def _rail_html(view: Any, own: Any, today: date) -> str:
    """The account's history as one bar, with the two dates it runs between."""
    protection = view.protection
    state = "none" if protection is None else protection.state
    held = own.held
    first = _month_start(view.oldest_month)
    rail = build_rail(
        first=first,
        known_from=own.known_from,
        known_to=own.known_to,
        through=own.through,
        held_day=held.day if held is not None else None,
        protected_through=protection.through if state in ("intact", "broken") else None,
        protection_broken=state == "broken",
        today=today,
    )
    return (
        '<div class="proof">'
        + rail_svg(rail, uid="account-rail")
        + f'<div class="rail-ends mono"><span>{_esc(rail.start.isoformat())}</span>'
        f"<span>{_esc(rail.end.isoformat())}</span></div></div>"
    )


def _month_start(month: str) -> date | None:
    return date(int(month[:4]), int(month[5:7]), 1) if month else None


def _month_end(month: str) -> date | None:
    start = _month_start(month)
    if start is None:
        return None
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _state_html(view: Any, today: date) -> str:
    """The account's state, first on the page: the rail, the verdict, and what holds it back."""
    if view.rebuilding:
        return (
            '<h2 class="visually-hidden">Verification</h2>'
            f'<p class="warn">{_esc(view.rebuilding)}</p>'
        )
    standing = view.standing
    if standing is None:
        return ""
    own = standing.own
    verdict_css = "verdict warn" if own.state == NONE else "verdict"
    if own.state == "agrees" and own.held is None:
        verdict_css += " clear"
    body = (
        '<h2 class="visually-hidden">Verification</h2>'
        + _rail_html(view, own, today)
        + f'<p class="{verdict_css}">{_esc(_verdict_text(own, view.protection))}</p>'
        + _held_html(own, boxed=True)
        + _stretches_html(own)
    )
    if not own.movement_checked:
        body += (
            '<p class="sub">The movement checks were not read for this view, so this is from '
            "the known balances alone.</p>"
        )
    whole = standing.whole
    if whole is not None:
        body += (
            '<p class="sub">The whole account, with its Spaces:</p>'
            + line_html(whole, None, with_protection=False)
            + _held_html(whole, boxed=False)
        )
    return body


def _protect_html(view: Any, unmasked: bool = False, everything: bool = False) -> str:
    """The protection where the state is described: what can be pressed, or the line and its exit.

    Every press goes to a confirmation first (`LedgerPages._confirm_page`), including the
    withdrawal, so nothing here acts on a single tap.

    The earlier dates offered are the newest `_OFFERED_DATES` unless `everything` asks for the
    full list of known balances: the account whose page held 1,906 known balances offered a date
    for each, 92 kilobytes of a drop-down nobody scrolls.
    """
    protection = view.protection
    if protection is None or view.rebuilding:
        return ""
    ref, month = view.ref, view.month
    body = ""
    state = protection.state
    if state == "intact":
        detail = (
            f"<p>The protected period runs from {_esc(protection.span_start.isoformat())} to "
            f"{_esc(protection.through.isoformat())}. It is an alarm on change and never a "
            "freeze: a rebuild or an import still does what the rules say, and says here if "
            "that changed anything inside the protected period.</p>"
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
            f'<p class="protect-line"><strong>{_esc(protection_line(protection))}</strong></p>'
            + _post("/protect-withdraw", ref, month, "", REMOVE_PROTECTION)
            + _disclosure("About this protection", detail)
        )
    elif state == "broken":
        said = "".join(f"<li>{_esc(line)}</li>" for line in protection.changes)
        body += (
            '<p class="bad"><strong>The protection is broken: the protected period, through '
            f"{_esc(protection.through.isoformat())}, has changed since "
            f"{_esc(protection.pressed_on.isoformat())}.</strong></p>"
            f'<ul class="plain">{said}</ul>'
            '<p class="muted">Nothing was changed back or updated: the protection stays broken '
            "until a later rebuild restores the protected period, or you accept the new "
            "state.</p>"
            + _post("/protect-accept", ref, month, "", "Accept the change and protect again")
            + _post("/protect-withdraw", ref, month, "", REMOVE_PROTECTION)
        )
    if protection.earlier_said:
        body += (
            '<p class="warn"><strong>A fault in the data before the protected period, not a '
            f"change to it:</strong> {_esc(protection.earlier_said)}</p>"
        )
    offer = protection.offer
    if offer:
        newest = offer[-1]
        body += _post(
            "/protect", ref, month, _through(newest), f"Protect through {newest.isoformat()}"
        )
        if len(offer) > 1:
            dates = list(reversed(offer))
            held_back = len(dates) - _OFFERED_DATES
            if held_back > 0 and not everything:
                dates = dates[:_OFFERED_DATES]
            options = "".join(
                f'<option value="{_esc(d.isoformat())}">{_esc(d.isoformat())}</option>'
                for d in dates
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
            body += _disclosure(
                "Protect through an earlier date",
                '<form method="post" action="/protect"><input type="hidden" name="ref" '
                f'value="{_esc(ref)}"><input type="hidden" name="month" value="{_esc(month)}">'
                f'<p><select name="through" aria-label="Protect through">{options}</select></p>'
                + submit_button("Protect through the date chosen", secondary=True)
                + "</form>"
                + older,
            )
    elif protection.not_offered and state == "none":
        body += f'<p class="sub">Not offered: {_esc(protection.not_offered)}.</p>'
    return f'<div class="protect">{body}</div>' if body else ""


def _opening_gist(opening: Any) -> str:
    """How the known balances stand, in a few words, for the summary that folds them away."""
    if opening.state == "none":
        return "none stated"
    agree = sum(1 for line in opening.anchors if line.verdict == "agrees")
    differ = sum(1 for line in opening.anchors if line.verdict == "differs")
    family = opening.family
    if not agree and not differ and family is not None and family.anchors:
        agree, differ = family.agreeing, family.differing
    return f"{agree:,} add up, {'none' if not differ else f'{differ:,}'} differ"


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
            f'<input type="hidden" name="month" value="{_esc(view.month)}">'
            f"{extra if everything else ''}" + submit_button(label, secondary=True) + "</form>"
        )
    params = {"ref": view.ref, "month": view.month}
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
    view: Any, unmasked: bool, *, held: bool = False, everything: bool = False
) -> str:
    """The "Known balances and the opening" section, and the forms that edit it.

    Folded away, since an account in agreement has no use for it, and open where the account is
    held back, since the explanation of the difference is in it and the box above links here. It
    lists the balances worth reading and links to the rest (`_listed_anchors`); `everything` lists
    them all, and is open because it is what a person asked for.
    """
    opening = view.opening
    if opening is None:
        return ""

    def earlier(count: int) -> str:
        return _earlier_balances(view, count, unmasked=unmasked, balance_only=opening.balance_only)

    body = ""
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
        )
        if sum(1 for line in opening.anchors if line.basis == "statement") >= 2:
            body += (
                '<p class="muted"><a class="tap" '
                f'href="/period-reconciliation?ref={_esc(quote(view.ref, safe=""))}">'
                "Test the rows between the statements, period by period</a></p>"
            )
        if opening.state == "derived":
            figure = _signed(_balance_word(opening.direction), opening.direction, opening.opening)
            body += (
                f'<p><strong>Opening balance, at the end of {_esc(opening.as_at)}:</strong> '
                f'<span class="mono">{_esc(figure)}</span>. '
                + (
                    "The account opened with nothing, so no row has to be taken on trust."
                    if opening.anchors and opening.anchors[0].basis == "opened"
                    else "It is the earliest known balance less "
                    "the rows dated on or before that day."
                )
                + "</p>"
            )
            if opening.single_anchor and not opening.balance_only:
                body += (
                    '<p class="warn">An opening worked out from a single known balance absorbs '
                    "every missing or surplus row before that day into the opening figure, "
                    "and nothing here can tell. A second known balance turns it into a "
                    "test.</p>"
                )
            differing = sum(1 for line in opening.anchors if line.verdict == "differs")
            if differing:
                body += (
                    f'<p class="warn"><strong>{_plural(differing, "later known balance")} '
                    f"{agree(differing, 'differs')}</strong> "
                    "from what the rows predict: rows are missing, duplicated, or "
                    "mis-dated between the known balances.</p>"
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
                )
                differing = sum(1 for line in opening.anchors if line.verdict == "differs")
                if differing:
                    body += (
                        f'<p class="warn"><strong>{_plural(differing, "known balance")} '
                        f"{agree(differing, 'differs')}</strong> "
                        "from what the rows predict: rows are missing, duplicated, or "
                        "mis-dated.</p>"
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
    differing = any(line.verdict == "differs" for line in opening.anchors) or (
        opening.family is not None and bool(opening.family.differing)
    )
    # A disregard that no longer applies is a decision about nothing, which only this section can
    # remove, so it is not left folded away.
    stale = any(entry.stale for entry in opening.disregarded)
    return _disclosure(
        f"Known balances and the opening ({_opening_gist(opening)})",
        body,
        open=held or differing or everything or stale,
        anchor=OPENING_ANCHOR,
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
        notes += (
            f"<p class=\"muted\">{_plural(typed.live_elsewhere, 'more typed transaction')} "
            f"{agree(typed.live_elsewhere, 'is')} dated in other months: step to that "
            "month to remove one.</p>"
        )
    if typed.withdrawn_total:
        notes += (
            f"<p class=\"muted\">{_plural(typed.withdrawn_total, 'typed transaction')} "
            "removed in all. They stay in the record as evidence and count nowhere.</p>"
        )
    return _disclosure(
        f"Typed transactions ({len(typed.lines)} this month)",
        '<p class="muted">For an account no feed reports, such as a mortgage at another '
        "bank. Each one is kept as evidence and counted like any other row, and "
        "becomes one row with the bank's if a feed later reports the same payment. "
        "The figure and description you type are never shown on any page you can "
        "bookmark; after saving, this page comes back masked.</p>"
        + form
        + (f'<ul class="txns">{items}</ul>' if items else "")
        + notes,
    )


def _position_html(position: Any, *, bound: bool) -> str:
    included = position.opening_included
    word = _balance_word if included else _direction_word
    verdict = _NOTHING_SENT if not bound else f"The two positions {_differ(position.differs)}."
    return _disclosure("Running position", (
        '<div class="scroll"><table>'
        + _count("Counted through", position.through)
        + _count("Rows counted (void, folded, and reversed excluded)", position.rows_counted)
        + _count(
            "Balance by the store's own rows" + (", plus the opening balance" if included else ""),
            _signed(
                word(position.store_direction), position.store_direction, position.store_balance
            ),
        )
        + _count(
            "Balance by what would be sent to Actual",
            _signed(
                word(position.sent_direction), position.sent_direction, position.sent_balance
            ),
        )
        + "</table></div>"
        f"<p><strong>{_esc(verdict)}</strong> "
        + (
            "Both figures start from the account's derived opening balance, shown "
            "above."
            if included
            else "Neither figure includes an opening balance: both start from zero, "
            "which is not a claim that the account opened empty."
        )
        + "</p>"
    ))


_LIMITS = (
    '<ul class="muted">'
    "<li>A booked row that a source reported once and stopped reporting in later "
    "fetches covering the same dates is not detected yet. Void rows are listed, "
    "because the store records a vanished pending payment; a vanished booked row "
    "leaves no record in the merged layer, so a page with no warning is not a "
    "pass.</li>"
    "<li>Rows are dated by value date, the date Actual is sent.</li>"
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
    if view.newest_month and view.newest_month != view.month:
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
            said = f"{name} {year}, {held} {'row' if held == 1 else 'rows'}"
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


def _navigation(view: Any, unmasked: bool) -> str:
    """The way on from the foot of the page: the shape page, and home.

    Months are stepped from beside the month's own heading, where the rows they change are.
    """
    shape = f'<p><a class="tap" href="{_url("/account", ref=view.ref)}">'
    shape += f"{_esc(page_name('/account'))} for this account</a> "
    shape += (
        f'<a class="tap" href="{_url("/coverage-timeline", ref=view.ref)}">'
        f"{_esc(page_name('/coverage-timeline'))}</a></p>"
    )
    return f'<div class="foot-links">{shape}{_HOME}</div>'


def _mode(view: Any, unmasked: bool, everything: bool = False) -> str:
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
            f'href="{_url("/ledger", ref=view.ref, month=view.month, **around)}">'
            "Hide values</a></p>"
        )
    # The button is the call to action and the sealed slots are the state; what masked means is
    # one tap away, so a reader who knows it does not scroll past a paragraph every time.
    return (
        '<form method="post" action="/ledger">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        f'<input type="hidden" name="month" value="{_esc(view.month)}">'
        + kept
        + submit_button("Show values")
        + "</form>"
        + _disclosure(
            "What masked means",
            '<p class="sub">Values are masked: every digit shows as 9 and every letter '
            "as X, with length, case, and punctuation kept. A balance or a sum shows "
            f"as {MASKED_TOTAL} whatever its size, since its number of digits would "
            "say how much there is. Counts, dates, sources, directions, and flags "
            "are real.</p>",
        )
    )


def _name(view: Any) -> str:
    """The account's own name, which leads the page; its reference where it has no name."""
    return str(view.label or view.ref)


def _danger_zone(
    view: Any, *, archive_wired: bool, unmasked: bool = False, everything: bool = False
) -> str:
    """The controls that remove or retire something, in one bordered place at the foot.

    A review of the page found two full-width "Remove the known balance" buttons in the middle
    of it, a thumb's scroll from the transactions. Each control here still asks for
    confirmation before it acts.
    """
    removals = (
        _remove_forms(view, view.ref, view.month, unmasked=unmasked, everything=everything)
        if view.opening is not None and view.opening.stated_days
        else ""
    )
    archive = (
        archive_controls(view.ref, view.archive, offer=view.state != "unknown", with_date=True)
        if archive_wired
        else ""
    )
    if not (removals or archive):
        return ""
    return _disclosure(
        "Danger zone: remove a known balance, or archive this account",
        removals + archive,
        css="ledger-danger",
    )


def _head(view: Any) -> str:
    """Under the name: the reference, where it is fed from, and whether it is sent to Actual."""
    id_line = f'<p class="ref">{code_html(view.ref)}</p>' if view.label else ""
    fed = ", ".join(code_html(source) for source in view.sources) or "none"
    binding = (
        "bound to an Actual account"
        if view.actual_bound
        else "not bound to an Actual account, so nothing in it is sent"
    )
    held = (
        f"Rows held from {_esc(view.oldest_month)} to {_esc(view.newest_month)}."
        if view.oldest_month
        else "No rows are held."
    )
    archive = view.archive
    archived = archive is not None and archive.state == "archived"
    label = f" {archive_label(archive)}" if archived else ""
    return (
        f'{id_line}<p class="sub">{label.strip()} '
        f"Fed by: {fed}. This account is {_esc(binding)}. {held}</p>"
    )


def _month_line(view: Any) -> str:
    """The month in one line: how many rows, and which sources reported them."""
    summary = view.summary
    rows = summary.rows
    noun = "row" if str(rows) == "1" else "rows"
    sources = _source_pairs(summary.per_source)
    copies = int(str(summary.folded))
    if not copies:
        return f'<p class="sub">{_esc(str(rows))} {noun}. Sources {sources}.</p>'
    counted = int(str(rows)) - copies - int(str(summary.void))
    void = f", {summary.void} void" if int(str(summary.void)) else ""
    return (
        f'<p class="sub">{_esc(str(rows))} {noun}: {counted} counted, '
        f"{_copies(copies)}{void}. "
        f"Sources {sources}.</p>"
    )


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
        f'<p class="muted">This page costs {QUERIES_PER_PAGE} statements however '
        f"many rows the account holds, plus {ANCHOR_QUERIES} to look for opening "
        "known balances and a few more for each held statement or bank record "
        "that has not been read yet. A main account with Spaces adds "
        f"{FAMILY_QUERIES} and one per Space to check the whole account's balances. "
        f"An account the bank's own feed fills adds {FEED_TIME_QUERIES} more to read the "
        "feed's times.</p>"
    )


def _held_back(view: Any) -> bool:
    standing = view.standing
    return standing is not None and standing.own.held is not None


def _frame(
    view: Any, *, notice: str, head: str, state: str, month: str, txns: str, more: str
) -> bytes:
    """The page: the account's name as its heading, then four places the stylesheet arranges.

    On a phone they stack in this order. From 60rem the state, the month, and the folded
    sections sit in a narrow column and the transactions fill a wide one beside them, which is
    why they are separate elements and not one run of markup.
    """
    announced = f'<p class="ok"><strong>{_esc(notice)}</strong></p>' if notice else ""
    return render_page(
        "Ledger",
        announced
        + '<div class="acct-grid">'
        + f'<div class="acct-head">{head}</div>'
        + f'<div class="acct-state">{state}</div>'
        + f'<div class="acct-month">{month}</div>'
        + f'<div class="acct-txns">{txns}</div>'
        + f'<div class="ledger-more">{more}</div>'
        + "</div>",
        heading=_name(view),
        body_class="ledger-page",
    )


def render_ledger(
    ledger: Ledger,
    *,
    unmasked: bool,
    archive_wired: bool = False,
    notice: str = "",
    today: date | None = None,
    all_balances: bool = False,
    timeline: str = "",
) -> bytes:
    """`notice` is a sentence about what the request just did, escaped here.

    `timeline` is the compact coverage timeline's block (`web_account_timeline.block_for`), which
    sits directly under the state it qualifies.

    `all_balances` lists every known balance and not the newest few (`_listed_anchors`).

    It is for a confirmation that names a date and a basis; a value must never
    be passed in it, since the masked rendering is the one that carries it.

    `today` is where the proof rail ends. The handler passes the real day; without it the rail
    ends with the newest month held, so a rendering does not depend on the clock.
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
            month="",
            txns="",
            more="",
        )
    held = _held_back(view)
    if view.state == "no-rows":
        return _frame(
            view,
            notice=notice,
            head=head,
            state=(
                '<p class="warn"><strong>This account holds no transactions at all.</strong> '
                "It is declared, but nothing has been imported or fetched for it, or "
                "its feed has been silent since it was set up. This is not a clean "
                "month.</p>" + _state_html(view, end) + _protect_html(view, unmasked, all_balances)
            ),
            month="",
            txns="",
            more=(
                _opening_html(view, unmasked, held=held, everything=all_balances)
                + _clearing_html(view.clearing)
                + _anchor_forms(view, view.ref, view.month)
                + _unitemised_html(view)
                + _typed_html(view, ref=view.ref, month=view.month)
                + _removed_balances_html(view, view.ref, view.month, unmasked)
                + _navigation(view, unmasked)
                + _danger_zone(
                    view, archive_wired=archive_wired, unmasked=unmasked, everything=all_balances
                )
            ),
        )

    state = (
        _state_html(view, end)
        + timeline
        + _protect_html(view, unmasked, all_balances)
        + _mode(view, unmasked, all_balances)
    )
    month = (
        f"<h2>{_esc(view.month)}</h2>"
        + _month_links(view, unmasked)
        + _month_picker(view, unmasked)
    )
    counts = ""
    position = _position_html(view.position, bound=view.actual_bound)
    if view.state == "empty-month":
        month += (
            '<p class="warn"><strong>No rows are dated in this month.</strong> '
            f"The account holds rows from {_esc(view.oldest_month)} to "
            f"{_esc(view.newest_month)}, so a quiet month here is a gap to explain, "
            "not a clean result.</p>"
        )
    else:
        month += _month_line(view)
        counts = _disclosure(
            f"This month's counts and sums ({_plural(view.summary.rows, 'row')})",
            _summary_html(view.summary, bound=view.actual_bound) + position,
        )
        position = ""
    clock = (
        f'<p class="sub">{_esc(_CLOCK_NOTE)}</p>'
        if BANK_SOURCE in view.sources and view.state == "ok"
        else ""
    )
    txns = ""
    if view.state == "ok":
        counted = [row for row in view.rows if not _is_copy(row)]
        copies = [row for row in view.rows if _is_copy(row)]
        txns = (
            "<h2>Transactions, newest first</h2>"
            '<ul class="txns">'
            + "".join(_row_html(row, unmasked) for row in counted)
            + "</ul>"
            + _copies_html([_row_html(row, unmasked) for row in copies])
        )
    limits = _disclosure(
        f"What this page does not check ({_LIMITS.count('<li>')})",
        _LIMITS + _statement_cost(),
    )
    more = (
        _joins_html(view.joins, clock)
        + _opening_html(view, unmasked, held=held, everything=all_balances)
        + _clearing_html(view.clearing)
        + counts
        + position
        + _anchor_forms(view, view.ref, view.month)
        + _typed_html(view, ref=view.ref, month=view.month)
        + _unitemised_html(view)
        + _removed_balances_html(view, view.ref, view.month, unmasked)
        + limits
        + _navigation(view, unmasked)
        + _danger_zone(
                    view, archive_wired=archive_wired, unmasked=unmasked, everything=all_balances
                )
    )
    return _frame(
        view, notice=notice, head=head, state=state, month=month, txns=txns, more=more
    )


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

    def _ledger_get(self, params: dict[str, list[str]]) -> None:
        # Nothing in the query string can unmask: only `ref`, `month`, and which known balances
        # to list are read, and the rendering is chosen by which method was used.
        self._ledger(
            (params.get("ref", [""])[0] or "").strip(),
            (params.get("month", [""])[0] or "").strip(),
            unmasked=False,
            all_balances=_asks_for_all_balances(params),
        )

    def _ledger_post(self, form: dict[str, list[str]]) -> None:
        self._ledger(
            (form.get("ref", [""])[0] or "").strip(),
            (form.get("month", [""])[0] or "").strip(),
            unmasked=True,
            all_balances=_asks_for_all_balances(form),
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
        before = self.answer_standing(ref)
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
            notice=self.answer_notice(
                f"Saved: a known balance for the end of {day}. Nothing else changed.",
                ref,
                before,
            ),
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

    def _confirm_page(
        self, action: str, ref: str, month: str, question: str, label: str, extra: str = ""
    ) -> None:
        """Ask "are you sure" before a protection is made, accepted, or withdrawn.

        The route answers its first POST with this and changes nothing; only the form here, which
        carries `confirmed`, acts. Every protection press goes through it, because what the
        owner's manual flow asked for was a lock whose release is behind an are-you-sure.
        """
        back = _url("/ledger", ref=ref, month=month)
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
            self._respond(404, _page("Not available", "Protection is not wired."))
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
            refused_title="Not protected",
            refused_lead="Nothing was protected.",
            done="Protected through {through}. It is an alarm on change and never a freeze.",
            question=(
                "Protect this account through {through}? From now on a rebuild, import, or "
                "pull that adds, removes, or changes any row dated up to then, or re-pairs one "
                "of its transfers, is reported as a broken protection. Nothing is blocked: "
                "the change still happens."
            ),
            label="Protect",
            with_through=True,
        )

    def _protect_withdraw_post(self, form: dict[str, list[str]]) -> None:
        self._protection_action(
            form,
            hook_name="protect_withdraw",
            refused_title="Protection not removed",
            refused_lead="Nothing was removed.",
            done=f"{PROTECTION_REMOVED}: the account's protection. The removal is in its history.",
            question=(
                "Remove this account's protection? Changes to its rows will no longer be "
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
            done="Accepted: the protected period is now as it stands, and the acceptance is "
            "recorded beside the original.",
            question=(
                "Accept the change and protect again? The protection will describe the period as "
                "it is now. The original and this acceptance both stay in its history."
            ),
            label="Accept the change and protect again",
        )

    def _typed_save_post(self, form: dict[str, list[str]]) -> None:
        """Type a transaction in, then answer with the MASKED ledger.

        The figure and the description go to the hook and no further: not into
        the confirmation, not into a refusal, and not into the page that follows.
        The page that follows is shown at the month the transaction is dated in,
        which is the only part of it that is echoed, and only once the hook has
        accepted it as a real date.
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
        self._ledger(
            ref,
            day[:7],
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
    ) -> None:
        hook = self.bound_config.ledger_data
        if hook is None:
            self._respond(404, _page("Not available", "No ledger is wired."))
            return
        if not ref:
            self._respond(400, _page("No account named", "Say which account with ?ref=."))
            return
        try:
            ledger = hook(ref, month)
        except LedgerRequestError as exc:
            self._respond(400, _page("Not a month", str(exc)))
            return
        except Exception as exc:
            self._respond(500, _page("Ledger failed", str(exc)))
            return
        today = datetime.now(UTC).date()
        self._respond(
            404 if ledger.state == "unknown" else 200,
            render_ledger(
                ledger,
                unmasked=unmasked,
                archive_wired=self.bound_config.archive_account is not None,
                notice=notice,
                today=today,
                all_balances=all_balances,
                timeline=(
                    block_for(self.bound_config, ref, ledger.month, today)
                    if ledger.state in ("ok", "empty-month")
                    else ""
                ),
            ),
            no_store=unmasked or no_store,
        )
