"""What a kept document IS, in words that name no amount, payee, or balance.

Bring in's one form asks which account each uploaded document is for. A file named by a GUID, or
by a bank's own default, says nothing, so each row carries this preview from the kept-statement
listing: the reader that read it, the days it lists with their length, how many transactions,
whether it adds up by what it lists, the issuer names found in it, and the account label it
prints (digits masked by the listing). The Kept statements page says the same facts a statement
at a time, and its words for the names found are used here, not repeated.

Nothing here reads a figure: every input is a count, a date, a name, or a word the listing already
masks, because the first view of a page is always masked.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import date

from .account_names import code_html
from .page_times import range_with_span
from .plural import plural
from .web_statements import names_found_words

_esc = html.escape


def _days(listed: object) -> str:
    """The days a document lists, with how long they are, or that it lists none."""
    if isinstance(listed, list) and len(listed) == 2 and listed[0]:
        try:
            first, last = date.fromisoformat(str(listed[0])), date.fromisoformat(str(listed[1]))
        except ValueError:
            return "Lists no dated transactions"
        return "Lists " + _esc(range_with_span(first, last))
    return "Lists no dated transactions"


def unreadable_html(ident: int, names: str) -> str:
    """What a kept document no reader reads is, in place of a preview: that it cannot be read in
    yet, the issuer names found in it (`names`, already as markup), and its masked shape, which is
    what a reader is written from. It is offered neither an account nor a dry run."""
    shape = f'<a class="tap" href="/statement-shape?artefact={ident}">Masked shape</a>'
    return (
        '<p class="bi-preview">Cannot be read in yet - no reader for this layout. '
        f"Names found: {names}. {shape}</p>"
    )


def preview_html(
    entry: Mapping[str, object], part: Mapping[str, object] | None = None
) -> str:
    """The preview of a kept document, or of one account (`part`) of a document of several.

    The one paragraph, with the link to the document's masked shape at its end. Where no reader
    reads the file, that is said in place of the rest.
    """
    ident = int(str(entry["id"]))
    shape = f'<a class="tap" href="/statement-shape?artefact={ident}">Masked shape</a>'
    parser = entry.get("parser")
    if not parser:
        return unreadable_html(ident, names_found_words(entry))
    source = part if part is not None else entry
    refusal = str(source.get("refusal") or "")
    rows = source.get("rows")
    reader = f"Read by {code_html(str(parser))}."
    if refusal:
        read = f"It does not add up by what it lists: {_esc(refusal)}"
        said = f"{reader} {read}"
    else:
        count = (
            plural(rows, "transaction")
            if isinstance(rows, int) and not isinstance(rows, bool)
            else ""
        )
        facts = ", ".join(
            fact for fact in (_days(source.get("listed_days")), count) if fact
        )
        said = f"{reader} {facts}; adds up by what it lists."
    names = f" Names found: {names_found_words(entry)}."
    label = str(entry.get("heading_label") or "") if part is None and entry.get("heading") else ""
    prints = f" Prints the account label {code_html(label)}." if label else ""
    return f'<p class="bi-preview">{said}{names}{prints} {shape}</p>'


__all__ = ["preview_html", "unreadable_html"]
