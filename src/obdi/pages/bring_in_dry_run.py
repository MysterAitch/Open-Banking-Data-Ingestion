"""What reading a kept statement in would do, a transaction at a time.

The matcher's dry run (`ingest.preview_reconcile`) already resolves every listed transaction
against what the account holds and writes nothing. This says its answer for each: already held
(and by which source), new, one that could be the other leg of a transfer, or one a flag would be
raised for, with the reason the flag would carry. Above the list, one line counts them.

NOTHING A STATEMENT SAYS is shown on a GET. A row is its day, its direction, and its description
masked as the ledger masks one; no amount appears. A deliberate press (`values=True`) shows the
amount and the description as they are, on a page marked as showing values.

A flag is never answered here. The decision is made where the flag lives, after the statement is
read in; this says beforehand that it will be asked for.
"""

from __future__ import annotations

import html

from ..core.masking import mask_text
from ..core.money import format_amount
from ..core.plural import plural
from ..ingest.pipeline import MatcherPreview, RowOutcome, RowPreview
from ..read.account_names import AccountsShown, code_html
from . import values_sitting

_esc = html.escape


def decisions_in(preview: MatcherPreview) -> int:
    """How many listed transactions would raise a flag for a person to decide."""
    return sum(1 for row in preview.rows if row.outcome is RowOutcome.DECISION)


def _transfer(row: RowPreview) -> bool:
    return row.outcome is RowOutcome.NEW and bool(row.transfer_with)


def summary_line(preview: MatcherPreview) -> str:
    """"33 listed: 30 already held, 2 new, 1 would need a decision", leaving out a kind of which
    there are none, and counting each transaction once."""
    rows = preview.rows
    held = sum(1 for row in rows if row.outcome is RowOutcome.HELD)
    legs = sum(1 for row in rows if _transfer(row))
    new = sum(1 for row in rows if row.outcome is RowOutcome.NEW) - legs
    asked = decisions_in(preview)
    parts = [
        f"{held} already held" if held else "",
        f"{new} new" if new else "",
        f"{legs} could be one leg of a transfer" if legs else "",
        f"{asked} would need a decision" if asked else "",
    ]
    return f"{len(rows)} listed: {', '.join(part for part in parts if part)}"


def _outcome(row: RowPreview, names: AccountsShown) -> str:
    if row.outcome is RowOutcome.DECISION:
        return f"would need a decision: {_esc(row.reason)}"
    if row.outcome is RowOutcome.HELD:
        return f"already held - matches a {code_html(row.held_source)} row"
    if _transfer(row):
        return f"new - could be one leg of a transfer with {names.of(row.transfer_with).as_name()}"
    return "new"


def _line(row: RowPreview, names: AccountsShown, *, values: bool) -> str:
    shown = row.description if values else mask_text(row.description)
    figure = f" {_esc(format_amount(abs(row.amount_minor)))}" if values else ""
    return (
        f'<li><span class="mono">{row.day.isoformat()}</span> {row.direction}{figure} '
        f"{_esc(shown)} - {_outcome(row, names)}</li>"
    )


def dry_run_fold(preview: MatcherPreview, names: AccountsShown, artefact: int) -> str:
    """The closed fold under a row: the summary line, a line for each listed transaction, and
    the press that shows the same list with its values.

    Inside a sitting that shows values (`values_sitting`) the lines are the ones the press
    answers with, and the press is left out: the sitting's banner already says values are shown.
    The answer carrying such a fold is a POST's, which is never kept by the browser.
    """
    if not preview.rows:
        return ""
    values = values_sitting.shown()
    lines = "".join(_line(row, names, values=values) for row in preview.rows)
    return (
        '<details class="bi-dry"><summary>What reading it in would do</summary>'
        f'<p class="bi-dry-says">{_esc(summary_line(preview))}.</p>'
        f'<ul class="bi-dry-list">{lines}</ul>'
        f"{values_sitting.unless_sitting(values_button(artefact))}</details>"
    )


def decision_line(preview: MatcherPreview) -> str:
    """Beside a chooser: that reading the statement in will ask for decisions, and where they
    are made; nothing at all where it will ask for none."""
    asked = decisions_in(preview)
    if not asked:
        return ""
    return (
        f'<p class="bi-guess bi-decide">Reading it in will ask you to decide on '
        f'{plural(asked, "transaction")}, from <a class="tap" href="/">Today</a>; nothing is '
        "read in until you press.</p>"
    )


#: The name of the button that asks for a statement's list with its values. It sits inside the
#: one form of choosers, which it submits to `/statement-dry-run` instead, so the account asked
#: about is the one chosen for that statement when it was pressed. (A form of its own would nest
#: inside that form, which a page may not.)
VALUES_BUTTON = "values-for"


def values_button(artefact: int) -> str:
    """The press that asks for the same list with its values, answered by a page of its own."""
    return (
        '<button class="button secondary" type="submit" formaction="/statement-dry-run" '
        f'name="{VALUES_BUTTON}" value="{artefact}">Show values</button>'
    )


def values_page_body(
    preview: MatcherPreview, names: AccountsShown, filename: str, account: str
) -> str:
    """The page a press of `values_form` is answered with: the list as it is, marked so."""
    lines = "".join(_line(row, names, values=True) for row in preview.rows)
    whose = names.of(account).as_name()
    return (
        '<p class="bad shown">VALUES ARE SHOWN on this page. It was produced by your request to '
        "show them, has no address of its own, and is not kept by the browser.</p>"
        f"<h2>What reading {code_html(filename)} in to {whose} would do</h2>"
        f"<p>{_esc(summary_line(preview))}. Nothing was read in.</p>"
        f'<ul class="bi-dry-list">{lines}</ul>'
        '<p><a class="button secondary" href="/bring-in">Back to Bring in</a></p>'
    )


__all__ = [
    "VALUES_BUTTON",
    "decision_line",
    "decisions_in",
    "dry_run_fold",
    "summary_line",
    "values_button",
    "values_page_body",
]
