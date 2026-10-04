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
from datetime import UTC
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .balance_anchors import parse_calendar_day
from .balance_chart import OWN
from .balance_meaning import READING_THRESHOLD
from .bank_balances import BANK_SOURCE
from .callback import render_page
from .errors import DataError
from .feed_item_shape import MIN_COMPARABLE, THRESHOLDS, differs
from .join_basis import BASIS_WORDS, count_sentence, moment_text
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
from .web_accounts import archive_controls, archive_label, submit_button
from .web_balance_chart import structure_summary_html
from .web_standing import protection_html, standing_html

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    # Only the annotation is needed, and importing the handler's module at
    # runtime would close a cycle: web.py composes this module in.
    from .web import WebConfig

_HOME = '<p><a class="tap" href="/">Back to overview</a></p>'

#: Agreeing anchors are listed plainly up to this many, and folded beyond it.
#: A card with years of statements has one agreeing anchor a month, and the
#: ones worth reading are the defining anchor and any that differ.
_PLAIN_AGREEING_ANCHORS = 3

_NOTHING_SENT = "Nothing in this account is sent to Actual, so there is nothing to compare."

_esc = html.escape


def _url(path: str, **params: str) -> str:
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return _esc(f"{path}?{query}")


def _count(label: str, value: object) -> str:
    return f"<tr><th>{_esc(label)}</th><td>{_esc(str(value))}</td></tr>"


def _pairs(items: tuple[tuple[str, int], ...]) -> str:
    return ", ".join(f"{name}: {number}" for name, number in items) or "none"


def _flag(text: str, title: str, css: str = "pill-quiet") -> str:
    return f'<span class="pill {css}" title="{_esc(title)}">{_esc(text)}</span> '


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


def _row_flags(row: Any) -> str:
    """The pills for what is unusual about a row, or nothing when nothing is."""
    flags = ""
    if row.cleared_by:
        flags += _flag(
            f"cleared by {', '.join(row.cleared_by)}",
            "An authoritative listing of the account lists this row: a statement, an export, "
            "or the bank's own feed. The aggregator alone does not clear a row.",
            "pill-ok",
        )
    if row.origin == "typed":
        flags += _flag(
            "typed",
            "A person typed this transaction. It is evidence like any other, and "
            "can be withdrawn from the typed transactions list below.",
        )
    elif row.origin == "unitemised":
        flags += _flag(
            "unitemised change",
            "Derived from the account's stated balances: the difference between two "
            "of them that no row explains. It is never stored, so restating a "
            "balance changes it.",
        )
    if row.one_source:
        flags += _flag(
            "one source",
            "More than one source feeds this account and only one of them has "
            "reported this row.",
            "pill-bad",
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
    if row.withheld:
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


def _status_pill(status: str) -> str:
    css = {"booked": "pill-ok", "void": "pill-bad"}.get(status, "pill-quiet")
    return f'<span class="pill {css}">{_esc(status)}</span>'


def _sighting_line(sighting: Any) -> str:
    """One source's sighting: how it came to be on the row, then everything it stated."""
    words = BASIS_WORDS.get(sighting.basis, BASIS_WORDS[""])
    how = f"copied from the main account's row, {words}" if sighting.copy else words
    stated = ", ".join(
        f"{_esc(moment.field)} {_esc(moment_text(moment))}" for moment in sighting.moments
    )
    tail = f": {stated}" if stated else ""
    return f'<p class="muted"><strong>{_esc(sighting.source)}</strong> - {_esc(how)}{tail}</p>'


def _sightings_html(row: Any) -> str:
    """What each source stated of the row and how its sighting joined, one click away.

    Times are London time, the clock the rest of the page uses (`_CLOCK_NOTE`); a date is as stated.
    """
    if not row.sightings:
        return ""
    return (
        '<details class="muted"><summary>Dates and joins</summary>'
        + "".join(_sighting_line(sighting) for sighting in row.sightings)
        + "</details>"
    )


def _joins_html(joins: Any) -> str:
    """The account's rows by how they joined, and the guessed ones' dates a click away."""
    if joins is None:
        return ""
    counts = dict(joins.by_basis)
    sentence = count_sentence(counts)
    if not sentence:
        return ""
    guessed = joins.heuristic_days
    listing = (
        f"<details><summary>{len(guessed)} joined by the matcher's guess: the dates</summary>"
        "<p>" + ", ".join(_mono(day) for day in guessed) + "</p></details>"
        if guessed
        else '<p class="muted">No row rests on the matcher\'s guess.</p>'
    )
    return (
        "<h3>How the rows were joined</h3>"
        f"<p>{_esc(sentence[0].upper() + sentence[1:])}.</p>{listing}"
    )


def _row_html(row: Any) -> str:
    """One transaction as a list item that wraps instead of scrolling.

    The date and the figure share the first line, the description has the second,
    and the status, sources, and flags wrap beneath.
    A seven-column table was wider than a phone, and wider than the page column on
    a desktop, so the flags were the part that sat out of sight.
    """
    dates = ""
    if row.dates_differ:
        said = ", ".join(f"{source} {day}" for source, day in row.observed)
        dates = (
            '<p class="warn" title="The sources dated this row '
            f'differently.">dates differ: {_esc(said)}</p>'
        )
    counterparty = (
        f'<br><span class="muted">{_esc(row.counterparty)}</span>'
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
        '<p class="muted">'
        + "<br>".join([*notes, f"set by {_esc(row.annotated_by)}"])
        + "</p>"
        if row.annotated_by
        else ""
    )
    sources = "".join(
        f'<span class="pill pill-quiet">{_esc(source)}</span> ' for source in row.sources
    )
    at = f" {_esc(_clock(row.feed_at))}" if row.feed_at is not None else ""
    return (
        "<li>"
        '<div class="txn-head">'
        f'<span class="mono nowrap">{_esc(row.dated.isoformat())}{at}</span>'
        f'<span class="mono nowrap">{figure}</span>'
        "</div>"
        f"{dates}"
        f"<p><strong>{_esc(row.description)}</strong>{counterparty}</p>"
        f'<p class="pills">{_status_pill(row.status)} {sources}{_row_flags(row)}</p>'
        f"{annotation}"
        f"{_sightings_html(row)}"
        "</li>"
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
    rows = _count("Rows in the month", summary.rows) + _count(
        "Rows per source", _pairs(summary.per_source)
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
    "stated": "stated by a person",
    "bank": "the bank's own running balance",
    "statement": "a held statement's closing balance",
    "family": "the whole account's stated balance, less its Spaces' own rows",
    "opened": "the day before the account was created, with its feed held from then",
    "export": "the export's own balance, read as the main account's",
    "assumed-nil": "assumed, not shown: a Space's rows are taken to start from nil",
}


def _balance_word(direction: str) -> str:
    return _BALANCE_WORDS.get(direction, direction)


def _anchor_row(line: Any, *, balance_only: bool = False) -> str:
    """One anchor as a list item: its verdict first, then when, what, and whence.

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
        role = '<span class="pill pill-quiet">defines the opening balance</span>'
        detail = ""
    elif balance_only and line.basis == "stated":
        role = '<span class="pill pill-quiet">followed</span>'
        detail = (
            ": this account is tracked by its stated balances, so the balance "
            "follows this figure"
        )
    elif line.verdict == "agrees":
        role = '<span class="pill pill-ok">agrees</span>'
        detail = " with what the rows predict"
    else:
        role = '<span class="pill pill-bad">differs</span>'
        difference = _signed(
            _balance_word(line.difference_direction), line.difference_direction, line.difference
        )
        detail = f" from what the rows predict by {_esc(difference)}"
    basis = _BASIS_WORDS.get(line.basis, line.basis)
    if line.balance_direction == "nil":
        balance = "nil"
    else:
        balance = (
            f"{_esc(_balance_word(line.balance_direction))} "
            f'<span class="mono nowrap">{_esc(line.balance)}</span>'
        )
    return (
        "<li>"
        f"<p>{role}{detail}</p>"
        f'<p>End of <span class="mono nowrap">{_esc(line.day)}</span>: {balance}</p>'
        f'<p class="muted">{_esc(basis)}</p>'
        "</li>"
    )


def _anchors_html(anchors: tuple[Any, ...], *, balance_only: bool = False) -> str:
    """The anchors, with a long run of agreeing ones folded away.

    The defining anchor and every differing one stay in view, since those are what
    a reader came for.
    The agreeing ones are a count to open rather than a list to scroll past.
    """
    agreeing = [line for line in anchors if not line.defines_opening and line.verdict == "agrees"]

    def row(line: Any) -> str:
        return _anchor_row(line, balance_only=balance_only)

    if len(agreeing) <= _PLAIN_AGREEING_ANCHORS:
        return '<ul class="anchors">' + "".join(row(line) for line in anchors) + "</ul>"
    folded = {id(line) for line in agreeing}
    return (
        '<ul class="anchors">'
        + "".join(row(line) for line in anchors if id(line) not in folded)
        + "</ul>"
        '<details class="agreeing"><summary>'
        f"{len(agreeing)} later anchors "
        + ("are followed" if balance_only else "agree with what the rows predict")
        + "</summary>"
        '<ul class="anchors">' + "".join(row(line) for line in agreeing) + "</ul>"
        "</details>"
    )


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
    """Where the faults are, by day: the stated balances at which the difference
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
        f"{_esc(source)} {_mono(day)}{at if source == BANK_SOURCE else ''}"
        for source, day in note.dates
    )
    seen = ", ".join(_esc(source) for source in note.sources) or "no source"
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


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


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
        else f"The bank's feed also holds {found.count} items in this window that are not rows:"
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
            f"{facts.rows:,} rows. In its own sequence {facts.out_of_order:,} "
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
        f'<p class="muted">{_esc(str(family.round_ups_carried))} feed row(s) carry a '
        f"round-up. {_esc(str(family.round_up_legs))} round-up leg(s) to a Space are held, "
        f"and {_esc(str(family.round_up_legs_paired))} of them are paired with a row in that "
        f"Space. {unreadable}</p>" + _round_up_gaps_html(family.round_up_gaps)
    )


def _counted(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


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
            _counted(
                gaps.no_leg_of_nothing, "is a round-up of nothing", "are round-ups of nothing"
            ),
            _counted(gaps.no_leg_incoming, "is on an incoming item", "are on incoming items"),
            _counted(
                gaps.no_leg_reversed_or_declined,
                "is on a reversed or declined item",
                "are on reversed or declined items",
            ),
            _counted(gaps.no_leg_unreadable, "could not be read", "could not be read"),
            _counted(gaps.no_leg_other, "is other", "are other"),
        ]
        body = (
            f'<p class="muted">Of the '
            f"{_counted(gaps.no_leg, 'feed row that carries', 'feed rows that carry')} a "
            f"round-up and {'holds' if gaps.no_leg == 1 else 'hold'} no leg, "
            f"{', '.join(parts[:-1])}, and {parts[-1]}.</p>"
        )
    else:
        body = '<p class="muted">Every feed row that carries a round-up holds a leg.</p>'
    if gaps.unpaired_legs:
        body += (
            f'<p class="muted">Of the '
            f"{_counted(gaps.unpaired_legs, 'round-up leg that has', 'round-up legs that have')} "
            "no pair in a Space, "
            f"{_counted(gaps.unpaired_on_reversed, 'is', 'are')} on a reversed or dropped payment, "
            f"{_counted(gaps.unpaired_to_unheld_space, 'goes', 'go')} to a Space whose rows "
            f"are not held, and {_counted(gaps.unpaired_other, 'is', 'are')} other.</p>"
            + _days_html(gaps.unpaired_days, gaps.unpaired_legs)
        )
    else:
        body += '<p class="muted">No round-up leg is without a pair in a Space.</p>'
    if gaps.space_in_unpaired:
        subject = _counted(
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
        "<h3>The whole account's stated balances</h3>"
        '<p class="muted">A family balance is one stated by a source that cannot see '
        "this account's Spaces (the certified statement, the export, the aggregator), so "
        "it is the balance of the main account and every Space together and is checked "
        "against the rows of all of them, where a transfer between them cancels.</p>"
        '<p class="muted">This account\'s own balance is taken as that figure less what '
        "each Space's rows sum to, which assumes every Space's rows start from nil: its "
        "history is held from its first row.</p>"
    )
    if family.withheld:
        return body + f'<p class="warn">Not walked: {_esc(family.withheld)}.</p>'
    # With the opened anchor every stated balance is tested, so none is "later".
    which = "Ones" if family.nil_day else "Later ones"
    body += (
        '<div class="scroll"><table>'
        + _count("Spaces", ", ".join(family.spaces))
        + _count("Stated by", ", ".join(family.sources))
        + _count("Whole-account balances stated", family.anchors)
        + _count(f"{which} the rows reproduce", family.agreeing)
        + _count(f"{which} the rows do not reproduce", family.differing)
        + (
            _count("Opened, with a nil balance at the end of", family.nil_day)
            if family.nil_day
            else _count("Earliest, which defines the opening", family.defining_day)
        )
        + "</table></div>"
    )
    body += _opening_note(family)
    if family.before_opening:
        body += (
            f'<p class="warn"><strong>{_esc(str(family.before_opening))} row(s) are dated on '
            "or before the day the account opened.</strong> An account cannot move money "
            "before it exists, so either the creation date or those rows' dates are wrong, "
            "and the opening cannot be trusted until that is settled.</p>"
        )
    if family.unheld_legs:
        body += (
            f'<p class="warn"><strong>{_esc(str(family.unheld_legs))} transfer leg(s) go '
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
                " Declare it with the <span class=\"mono\">recover-spaces</span> command or "
                'the <a class="tap" href="/spaces">Spaces page</a> and bind its category in '
                "the account map, and the next pull fetches its history."
            )
        body += "</p>"
    body += _round_ups_html(family)
    if family.refused_figures:
        body += (
            f'<p class="warn">{_esc(str(family.refused_figures))} printed end-of-day '
            "balance(s) disagreed with their own statement's rows and were not used.</p>"
        )
    if not family.anchors:
        return body
    if not family.differing:
        return body + (
            '<p><span class="pill pill-ok">agrees</span> The rows of the account and its '
            f"Spaces reproduce every {'' if family.nil_day else 'later '}whole-account "
            "balance stated.</p>"
        )
    pattern = (
        "The difference is the same at every later balance, so one movement is "
        "missing or surplus between those two days."
        if family.pattern == "constant"
        else "The difference changes between later balances, so more than one "
        "movement is missing or surplus."
    )
    body += (
        '<p class="warn"><strong>The rows first stop reproducing the stated balance at the '
        f'end of <span class="mono nowrap">{_esc(family.first_differing)}</span>; they last '
        f'agreed at the end of <span class="mono nowrap">{_esc(family.last_agreeing)}'
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
            f'<span class="mono nowrap">{_esc(line.day)}</span>: the stated balance is '
            f'{side} than the rows predict by '
            f'<span class="mono nowrap">{_esc(line.difference)}</span></p>'
            f'<p class="muted">{_esc(", ".join(line.sources))}</p></li>'
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
        '<p><label>Date the balance applies to, the END of that day<br>'
        '<input type="date" name="day" required></label></p>'
        '<p><label>Balance at the end of that day, in pounds and pence<br>'
        f'<span class="muted">{way_round}</span><br>'
        '<input name="amount" inputmode="decimal" autocomplete="off" required>'
        "</label></p>" + submit_button("Save stated balance") + "</form>"
    )
    remove = "".join(
        '<form method="post" action="/ledger-anchor-remove">'
        + hidden
        + f'<input type="hidden" name="day" value="{_esc(day)}">'
        + submit_button(f"Remove the stated balance for the end of {day}")
        + "</form>"
        for day in view.opening.stated_days
    )
    return (
        "<h3>State a balance</h3>"
        '<p class="muted">Stating a balance for a date already stated replaces it. '
        "The amount you type is never shown on any page you can bookmark; after "
        "saving, this page comes back masked.</p>" + save + remove
    )


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
                "agrees": "the whole-account walk agrees at that balance from the same source, "
                "so the difference arises in the step from the whole account to this one's "
                "own (the Spaces' rows taken off)",
                "differs": "the whole-account walk differs at that same balance from the same "
                "source, so the cause is the one explained there",
                "": "the whole-account walk does not hold that balance, so it is tested "
                "against this account's rows alone",
            }[line.walk]
            return (
                '<p class="warn">The account\'s own anchors first stop matching its rows at the '
                f'end of <span class="mono nowrap">{_esc(line.day)}</span>; they last agreed at '
                f'the end of <span class="mono nowrap">{_esc(anchors[at - 1].day)}</span>.</p>'
                f"<p>{stated}, and {walk}.</p>"
            )
    return ""


def _meaning_html(meanings: tuple[Any, ...]) -> str:
    """For each source blind to the Spaces, what its own rows say its stated
    balance means, in counts a person can check."""
    if not meanings:
        return ""
    percent = round(READING_THRESHOLD * 100)
    body = (
        "<h3>What each source's stated balance means</h3>"
        '<p class="muted">A source that cannot see this account\'s Spaces states a balance '
        "that is either the whole account's (it moves with every payment it lists, "
        "including those from a Space) or the main account's own (it skips those and "
        "moves with each transfer to or from a Space). Each is tested against the "
        "source's own consecutive balances; only steps that tell the two apart count, "
        f"and a reading is adopted when it explains at least {percent}% of them.</p>"
    )
    for item in meanings:
        name = f'<span class="mono">{_esc(item.source)}</span>'
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
    return (
        f"<p>Across the account, {clearing.cleared} rows are cleared and {clearing.uncleared} "
        "are not. A row is cleared when a statement, an export, or the bank's own feed lists "
        "it; the aggregator alone does not clear a row, and a pending row is never cleared."
        "</p>"
        f'<details><summary>Cleared and uncleared, month by month</summary><ul class="plain">'
        f"{months}</ul></details>"
    )


def _verification_html(view: Any) -> str:
    """The three dates, what holds agreement back, and the cleared counts."""
    if view.standing is None:
        return ""
    protection = view.protection
    protected = (
        protection.through if protection is not None and protection.state != "none" else None
    )
    return (
        "<h2>Verification</h2>"
        + standing_html(view.standing, view.ref, protected)
        + protection_html(protection, view.ref, view.month)
        + _clearing_html(view.clearing)
    )


def _opening_html(view: Any, unmasked: bool) -> str:
    """The "Opening balance and anchors" section, and the forms that edit it."""
    opening = view.opening
    if opening is None:
        return ""
    body = '<h2 id="opening">Opening balance and anchors</h2>'
    if opening.state == "none":
        body += (
            '<p class="warn"><strong>No opening balance: the figures on this page '
            "start from zero.</strong> That is how they are counted, and it is not "
            "a claim that the account opened empty. No balance has been stated for "
            "it, and neither the bank's records nor a held statement supplies one.</p>"
        )
    else:
        body += _anchors_html(opening.anchors, balance_only=opening.balance_only)
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
                    else "It is the earliest anchor's balance less "
                    "the rows dated on or before that anchor."
                )
                + "</p>"
            )
            if opening.single_anchor and not opening.balance_only:
                body += (
                    '<p class="warn">An opening derived from a single anchor absorbs every '
                    "missing or surplus row before that anchor into the opening figure, and "
                    "nothing here can tell. A second anchor turns it into a test.</p>"
                )
            differing = sum(1 for line in opening.anchors if line.verdict == "differs")
            if differing:
                body += (
                    f'<p class="warn"><strong>{differing} later anchor(s) differ</strong> '
                    "from what the rows predict: rows are missing, duplicated, or "
                    "mis-dated between the anchors.</p>"
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
                body += _anchors_html(opening.anchors, balance_only=opening.balance_only)
                differing = sum(1 for line in opening.anchors if line.verdict == "differs")
                if differing:
                    body += (
                        f'<p class="warn"><strong>{differing} balance(s) differ</strong> '
                        "from what the rows predict: rows are missing, duplicated, or "
                        "mis-dated.</p>"
                    )
            body += (
                '<p class="warn"><strong>No opening balance could be derived:</strong> '
                f"{_esc(opening.withheld)}.</p>"
            )
    if opening.unusable_statements:
        body += (
            f'<p class="muted">{_esc(str(opening.unusable_statements))} held statement(s) '
            "could not supply a balance - unreadable, or its rows do not carry its "
            "opening balance to its closing one - and are not used.</p>"
        )
    return (
        body
        + _bank_html(opening)
        + _meaning_html(opening.meanings)
        + _family_html(opening.family, view.ref)
        + _anchor_forms(view, view.ref, view.month)
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
        "<h2>Unitemised changes</h2>"
        '<p class="muted">This account is tracked by its stated balances. Between two '
        "consecutive ones the balance moved by the difference, less any rows dated "
        "between them, typed or otherwise; what is left is shown here as one change "
        "dated at the later balance. Where the rows explain the whole difference "
        "there is no change. They are worked out from the stated balances each time "
        "and never stored, so removing or restating a balance changes them.</p>"
    )
    if not view.unitemised:
        stated = sum(1 for line in opening.anchors if line.basis == "stated")
        return body + (
            "<p>None: "
            + (
                "the rows between the stated balances explain every difference."
                if stated >= 2
                else "it takes two stated balances for there to be a difference."
            )
            + "</p>"
        )
    items = "".join(
        "<li>"
        f'<div class="txn-head"><span class="mono nowrap">{_esc(line.day)}</span>'
        f'<span class="mono nowrap">'
        f"{_esc(_signed(line.direction, line.direction, line.amount))}</span></div>"
        + (
            f'<p class="muted">since the balance stated for the end of {_esc(line.since)}</p>'
            if line.since
            else ""
        )
        + "</li>"
        for line in view.unitemised
    )
    return body + f'<ul class="txns">{items}</ul>'


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
            tail = '<p><span class="pill pill-quiet">withdrawn</span></p>'
        else:
            tail = (
                '<form method="post" action="/ledger-typed-withdraw">'
                + hidden
                + f'<input type="hidden" name="entry" value="{_esc(line.entry_id)}">'
                + submit_button("Withdraw this typed transaction", secondary=True)
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
            f"<p class=\"muted\">{_esc(str(typed.live_elsewhere))} more typed "
            "transaction(s) are dated in other months: step to that month to withdraw one.</p>"
        )
    if typed.withdrawn_total:
        notes += (
            f"<p class=\"muted\">{_esc(str(typed.withdrawn_total))} typed transaction(s) "
            "withdrawn in all. They stay in the record as evidence and count nowhere.</p>"
        )
    return (
        "<h2>Typed transactions</h2>"
        '<p class="muted">For an account no feed reports, such as a mortgage at another '
        "bank. Each one is kept as evidence and counted like any other row, and "
        "becomes one row with the bank's if a feed later reports the same payment. "
        "The figure and description you type are never shown on any page you can "
        "bookmark; after saving, this page comes back masked.</p>"
        + form
        + (f'<ul class="txns">{items}</ul>' if items else "")
        + notes
    )


def _position_html(position: Any, *, bound: bool) -> str:
    included = position.opening_included
    word = _balance_word if included else _direction_word
    verdict = _NOTHING_SENT if not bound else f"The two positions {_differ(position.differs)}."
    return (
        "<h2>Running position</h2>"
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
    )


_LIMITS = (
    '<h2>What this page does not check</h2><ul class="muted">'
    "<li>A BOOKED row that a source reported once and stopped reporting in later "
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


def _navigation(view: Any, unmasked: bool) -> str:
    """The way on from the foot of the page: more months, the shape page, home."""
    shape = f'<p><a class="tap" href="{_url("/account", ref=view.ref)}">'
    return (
        _month_links(view, unmasked)
        + shape
        + "Shape of this account's data</a></p>"
        + _HOME
    )


def _mode(view: Any, unmasked: bool) -> str:
    if unmasked:
        return (
            '<p class="bad" style="border:2px solid;padding:.6rem;border-radius:.4rem">'
            "VALUES ARE SHOWN on this page. It was produced by your request to show "
            "them, has no address of its own, and is not kept by the browser.</p>"
            f'<p><a class="button secondary" '
            f'href="{_url("/ledger", ref=view.ref, month=view.month)}">'
            "Hide values (masked view)</a></p>"
        )
    return (
        '<p class="muted">Values are masked: every digit shows as 9 and every letter '
        "as X, with length, case, and punctuation kept. A balance or a sum shows "
        f"as {MASKED_TOTAL} whatever its size, since its number of digits would "
        "say how much there is. Counts, dates, sources, directions, and flags "
        "are real.</p>"
        '<form method="post" action="/ledger">'
        f'<input type="hidden" name="ref" value="{_esc(view.ref)}">'
        f'<input type="hidden" name="month" value="{_esc(view.month)}">'
        + submit_button("Show values")
        + "</form>"
    )


def _header(view: Any, *, archive_wired: bool) -> str:
    name = view.label or view.ref
    id_line = f'<br><span class="muted mono">{_esc(view.ref)}</span>' if view.label else ""
    fed = ", ".join(view.sources) or "none"
    binding = (
        "bound to an Actual account"
        if view.actual_bound
        else "NOT bound to an Actual account, so nothing in it is sent"
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
        f"<p><strong>{_esc(name)}</strong>{label}{id_line}<br>"
        f"Fed by: {_esc(fed)}<br>This account is {_esc(binding)}.<br>{held}</p>"
        + (
            archive_controls(
                view.ref, archive, offer=view.state != "unknown", with_date=True
            )
            if archive_wired
            else ""
        )
    )


def render_ledger(
    ledger: Ledger, *, unmasked: bool, archive_wired: bool = False, notice: str = ""
) -> bytes:
    """`notice` is a sentence about what the request just did, escaped here.

    It is for a confirmation that names a date and a basis; a value must never
    be passed in it, since the masked rendering is the one that carries it.
    """
    view = Disclosed(ledger, unmasked=unmasked)
    body = (f'<p class="ok"><strong>{_esc(notice)}</strong></p>' if notice else "") + _header(
        view, archive_wired=archive_wired
    )

    if view.state == "unknown":
        body += (
            '<p class="bad"><strong>Unknown account.</strong> Nothing is held under '
            "this reference and no account is declared with it. This is not an "
            "empty account.</p>" + _HOME
        )
        return render_page("Ledger", body)
    if view.state == "no-rows":
        body += (
            '<p class="warn"><strong>This account holds no transactions at all.</strong> '
            "It is declared, but nothing has been imported or fetched for it, or "
            "its feed has been silent since it was set up. This is not a clean "
            "month.</p>"
            + _verification_html(view)
            + _opening_html(view, unmasked)
            + _unitemised_html(view)
            + _typed_html(view, ref=view.ref, month=view.month)
            + _navigation(view, unmasked)
        )
        return render_page("Ledger", body)

    body += _mode(view, unmasked)
    if BANK_SOURCE in view.sources:
        body += f'<p class="muted">{_esc(_CLOCK_NOTE)}</p>'
    body += _verification_html(view)
    body += _joins_html(view.joins)
    body += f"<h2>{_esc(view.month)}</h2>" + _month_links(view, unmasked)
    if view.state == "empty-month":
        body += (
            '<p class="warn"><strong>No rows are dated in this month.</strong> '
            f"The account holds rows from {_esc(view.oldest_month)} to "
            f"{_esc(view.newest_month)}, so a quiet month here is a gap to explain, "
            "not a clean result.</p>"
        )
    else:
        body += _summary_html(view.summary, bound=view.actual_bound)
    body += _opening_html(view, unmasked)
    body += _position_html(view.position, bound=view.actual_bound)
    body += _unitemised_html(view)
    body += _typed_html(view, ref=view.ref, month=view.month)
    if view.state == "ok":
        body += (
            "<h2>Transactions, newest first</h2>"
            '<ul class="txns">' + "".join(_row_html(row) for row in view.rows) + "</ul>"
        )
    body += _LIMITS
    body += (
        f'<p class="muted">This page costs {QUERIES_PER_PAGE} statements however '
        f"many rows the account holds, plus {ANCHOR_QUERIES} to look for opening "
        "balance anchors and a few more for each held statement or bank record "
        "that has not been read yet. A main account with Spaces adds "
        f"{FAMILY_QUERIES} and one per Space to check the whole account's balances. "
        f"An account the bank's own feed fills adds {FEED_TIME_QUERIES} more to read the "
        "feed's times.</p>"
    )
    body += _navigation(view, unmasked)
    return render_page("Ledger", body)


def _page(title: str, message: str) -> bytes:
    return render_page(title, f"<p>{_esc(message)}</p>{_HOME}")


class LedgerPages:
    """The ledger's routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _ledger_get(self, params: dict[str, list[str]]) -> None:
        # Nothing in the query string can unmask: only `ref` and `month` are
        # read, and the rendering is chosen by which method was used.
        self._ledger(
            (params.get("ref", [""])[0] or "").strip(),
            (params.get("month", [""])[0] or "").strip(),
            unmasked=False,
        )

    def _ledger_post(self, form: dict[str, list[str]]) -> None:
        self._ledger(
            (form.get("ref", [""])[0] or "").strip(),
            (form.get("month", [""])[0] or "").strip(),
            unmasked=True,
        )

    def _anchor_refusal(self, status: int, title: str, message: str) -> None:
        self._respond(status, _page(title, message), no_store=True)

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
            self._anchor_refusal(400, "Balance not saved", f"Nothing was saved. {exc}.")
            return
        except Exception as fault:
            # Not str(fault): an unexpected failure's text is not under this
            # module's control and could quote what was typed.
            say("ledger.anchor.save.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500, "Balance not saved", "Nothing was saved, because of an unexpected fault."
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=f"Saved: a stated balance for the end of {day}. Nothing else changed.",
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
        try:
            removed = hook(ref, day)
        except DataError as exc:
            self._anchor_refusal(400, "Balance not removed", f"Nothing was removed. {exc}.")
            return
        except Exception as fault:
            say("ledger.anchor.remove.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Balance not removed",
                "Nothing was removed, because of an unexpected fault.",
            )
            return
        if not removed:
            self._anchor_refusal(
                404,
                "No such stated balance",
                f"No balance was stated for the end of {day}, so nothing was removed.",
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice=f"Removed: the stated balance for the end of {day}.",
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
                self._anchor_refusal(400, refused_title, f"{refused_lead} {exc}.")
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
            self._anchor_refusal(400, refused_title, f"{refused_lead} {exc}.")
            return
        except Exception as fault:
            say(f"ledger.{hook_name}.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500, refused_title, f"{refused_lead} An unexpected fault stopped it."
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
            refused_title="Protection not withdrawn",
            refused_lead="Nothing was withdrawn.",
            done="Withdrawn: the account's protection. The withdrawal is in its history.",
            question=(
                "Withdraw this account's protection? Changes to its rows will no longer be "
                "reported."
            ),
            label="Withdraw protection",
        )

    def _protect_accept_post(self, form: dict[str, list[str]]) -> None:
        self._protection_action(
            form,
            hook_name="protect_accept",
            refused_title="Change not accepted",
            refused_lead="Nothing was accepted.",
            done="Accepted: the protected span is now as it stands, and the acceptance is "
            "recorded beside the original.",
            question=(
                "Accept the change and protect again? The protection will describe the span as "
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
        try:
            hook(
                ref,
                day,
                (form.get("direction", [""])[0] or "").strip(),
                form.get("amount", [""])[0] or "",
                form.get("description", [""])[0] or "",
            )
        except DataError as exc:
            self._anchor_refusal(400, "Transaction not saved", f"Nothing was saved. {exc}.")
            return
        except Exception as fault:
            say("ledger.typed.save.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Transaction not saved",
                "Nothing was saved, because of an unexpected fault.",
            )
            return
        self._ledger(
            ref,
            day[:7],
            unmasked=False,
            notice=f"Saved: a typed transaction dated {day}. Nothing else changed.",
            no_store=True,
        )

    def _typed_withdraw_post(self, form: dict[str, list[str]]) -> None:
        hook = self.bound_config.typed_withdraw
        if hook is None:
            self._respond(
                404, _page("Not available", "Withdrawing a typed transaction is not wired.")
            )
            return
        ref = (form.get("ref", [""])[0] or "").strip()
        month = (form.get("month", [""])[0] or "").strip()
        try:
            hook(ref, (form.get("entry", [""])[0] or "").strip())
        except DataError as exc:
            self._anchor_refusal(400, "Transaction not withdrawn", f"Nothing was withdrawn. {exc}.")
            return
        except Exception as fault:
            say("ledger.typed.withdraw.fault", kind=type(fault).__name__)
            self._anchor_refusal(
                500,
                "Transaction not withdrawn",
                "Nothing was withdrawn, because of an unexpected fault.",
            )
            return
        self._ledger(
            ref,
            month,
            unmasked=False,
            notice="Withdrawn: one typed transaction. It stays in the record as evidence "
            "and counts nowhere.",
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
        self._respond(
            404 if ledger.state == "unknown" else 200,
            render_ledger(
                ledger,
                unmasked=unmasked,
                archive_wired=self.bound_config.archive_account is not None,
                notice=notice,
            ),
            no_store=unmasked or no_store,
        )
