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
from collections.abc import Mapping, Sequence
from datetime import date

from .account_names import AccountsShown, code_html
from .core.page_times import range_with_span
from .core.plural import plural
from .ingest.statement_extraction import not_yet_extracted_words
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
    if entry.get("not_extracted"):
        # Said in place of every fact, because each of them is read from what extraction gave.
        return f'<p class="bi-preview">This document is {_esc(not_yet_extracted_words())}.</p>'
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


def _listed(source: Mapping[str, object]) -> tuple[str, str] | None:
    days = source.get("listed_days")
    if isinstance(days, list) and len(days) == 2 and days[0] and days[1]:
        return str(days[0]), str(days[1])
    return None


def second_witness_html(
    listing: Sequence[Mapping[str, object]],
    entry: Mapping[str, object],
    part: Mapping[str, object] | None,
    account: str,
    names: AccountsShown,
) -> str:
    """The sentence that a kept document adds only a second witness, or "" where it does not.

    It is said where another kept statement, filed under `account` (whole, or one account of a
    document of several), lists exactly the first and last day this one lists: the same statement
    in another copy, such as a certified and a plain one, or a monthly statement and an export of
    the same days. The bytes differ, so both are kept as evidence; what the second adds is its own
    balances. Statements whose days differ, even where every transaction is held, are said by the
    "already held" line instead (`web_bring_in._held_html`). Found from the kept listing alone.
    """
    source = part if part is not None else entry
    days = _listed(source)
    rows = source.get("rows")
    if days is None or not isinstance(rows, int) or isinstance(rows, bool):
        return ""
    ident = entry.get("id")
    for other in listing:
        if other.get("id") == ident:
            continue
        held: list[Mapping[str, object]] = []
        if other.get("account_ref") == account and not other.get("sections"):
            held.append(other)
        parts = other.get("sections")
        if isinstance(parts, list):
            held.extend(p for p in parts if isinstance(p, Mapping) and p.get("account") == account)
        for item in held:
            if _listed(item) != days:
                continue
            label = f", account {item['label']}" if item is not other and item.get("label") else ""
            return (
                '<p class="bi-guess bi-witness">A statement for '
                f"{names.of(account).inline()} covering {_esc(days[0])} to {_esc(days[1])} "
                f"is already held ({code_html(str(other.get('origin')) + label)}); reading this "
                f"one in adds a second witness to its {plural(rows, 'transaction')} and its own "
                "balances, nothing new.</p>"
            )
    return ""


__all__ = ["preview_html", "second_witness_html", "unreadable_html"]
