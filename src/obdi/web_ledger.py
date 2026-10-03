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
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .balance_meaning import READING_THRESHOLD
from .callback import render_page
from .errors import DataError
from .ledger import (
    ANCHOR_QUERIES,
    FAMILY_QUERIES,
    QUERIES_PER_PAGE,
    Ledger,
    LedgerRequestError,
)
from .logs import say
from .masking import MASKED_TOTAL, Disclosed
from .web_accounts import archive_controls, archive_label, submit_button

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
    return (
        "<li>"
        '<div class="txn-head">'
        f'<span class="mono nowrap">{_esc(row.dated.isoformat())}</span>'
        f'<span class="mono nowrap">{figure}</span>'
        "</div>"
        f"{dates}"
        f"<p><strong>{_esc(row.description)}</strong>{counterparty}</p>"
        f'<p class="pills">{_status_pill(row.status)} {sources}{_row_flags(row)}</p>'
        f"{annotation}"
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
            "Sum of the store's rows (void and folded excluded)",
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
    if line.defines_opening:
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


def _family_html(family: Any) -> str:
    """The walk of the whole account's stated balances against the rows of the
    main account and its Spaces together.

    Said once, here: what a family balance is, and what the main account's own
    balance is taken to be from it.
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
            "whole account cannot balance until that Space is recovered: run the "
            '<span class="mono">recover-spaces</span> command or open the '
            '<a class="tap" href="/spaces">Spaces page</a>.</p>'
        )
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
        '<ul class="anchors">'
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
            return (
                '<p class="warn">The account\'s own anchors first stop matching its rows at the '
                f'end of <span class="mono nowrap">{_esc(line.day)}</span>; they last agreed at '
                f'the end of <span class="mono nowrap">{_esc(anchors[at - 1].day)}</span>.</p>'
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


def _opening_html(view: Any, unmasked: bool) -> str:
    """The "Opening balance and anchors" section, and the forms that edit it."""
    opening = view.opening
    if opening is None:
        return ""
    body = "<h2>Opening balance and anchors</h2>"
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
        else:
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
        + _meaning_html(opening.meanings)
        + _family_html(opening.family)
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
        + _count("Rows counted (void and folded excluded)", position.rows_counted)
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
            + _opening_html(view, unmasked)
            + _unitemised_html(view)
            + _typed_html(view, ref=view.ref, month=view.month)
            + _navigation(view, unmasked)
        )
        return render_page("Ledger", body)

    body += _mode(view, unmasked)
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
        f"{FAMILY_QUERIES} and one per Space to check the whole account's balances.</p>"
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
