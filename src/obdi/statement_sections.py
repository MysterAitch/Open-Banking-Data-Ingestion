"""Giving each account of an "all accounts" statement its own account.

Some documents cover several accounts one after another. The document is
kept as ONE artefact and stays unassigned as a whole, because an artefact
carries one account and this one has many. What a person decides is made per
SECTION and recorded as declared state (`Store.assign_statement_section`),
keyed by the artefact's digest and the section's key.

READING IN uses the one door every other id-less import uses: the section's
rows become transactions under the chosen account and go through
`reconcile_batch`, the matcher that already merges overlapping exports by
content key and occurrence. There is no second de-duplication here. A section
that overlaps an annual statement of the same account is therefore merged by
exactly the rules that merge two annual statements, and fails in exactly the
cases they fail in (see `tests/test_statement_section_overlap.py`).

REPLAY is part of the same promise a single kept statement makes: a rebuild
reads every assigned section back into its account, from the artefact and the
declared assignment alone, and counts the sections still waiting.

A section the arithmetic gate refuses is never assigned: it stays waiting,
with its reason, and its neighbours are unaffected.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .accounts import AccountMap
from .coverage import agreements, assignment_corroboration, assignment_doubt
from .errors import DataError
from .ingest import ImportSummary, reconcile_batch
from .models import Transaction
from .namespaces import validate_canonical_name
from .parsers.base import StatementParser
from .parsers.pdf_statements import PdfStatementParser, SectionReading
from .parsers.uk_banks import detect
from .review_settlement import settle_review_flags
from .space_attribution import fold_space_copies
from .store import Store


def masked(text: str) -> str:
    """A label with every digit shown as 9, for a page served on a plain GET.

    A loan's label carries its rate and a savings label may carry anything a
    person typed into a product name; digits are the part of a label that can
    be a figure, and the page's rule is that figures appear only in answer to a
    POST.
    """
    return re.sub(r"\d", "9", text)


def section_token(key: str) -> str:
    """What a page carries in place of a section's key.

    The key is the account's label reduced to its letters and digits, so a
    page that carried it would carry the label's digits unmasked. A short hash
    is enough to find the section again within one document, and says nothing
    about the label. `assign_section` accepts either, so a caller holding the
    key (a test, a script) needs no token.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def trial_sections(
    parser: StatementParser, payload: bytes
) -> list[SectionReading] | str | None:
    """A document's sections for a listing, or why they cannot be told apart.

    None is a document read whole. A string is the refusal of a document whose
    boundaries are ambiguous, digit-masked because it is shown on a GET.
    """
    if not isinstance(parser, PdfStatementParser):
        return None
    try:
        return parser.sections(payload)
    except (DataError, ValueError) as exc:
        return masked(str(exc))[:300]


@dataclass(frozen=True)
class AssignmentCheck:
    """What assigning a statement's rows to an account was checked against."""

    #: Why the statement probably is not this account's; None when nothing
    #: doubts it. Judged by `coverage.assignment_doubt`.
    doubt: str | None
    #: What it was checked against, in counts, for the result sentence.
    corroboration: str

    @property
    def refusal(self) -> str | None:
        """The result sentence for a refused assignment, or None to proceed."""
        if self.doubt is None:
            return None
        stop = "" if self.doubt.endswith(("?", ".")) else "."
        return (
            f"Not assigned: {self.doubt}{stop} Nothing was read in; "
            "choose the account again."
        )


def check_assignment(
    store: Store,
    *,
    incoming: list[Transaction],
    source: str,
    account: str,
    account_map: AccountMap,
) -> AssignmentCheck:
    """Ask whether these rows belong to `account`, BEFORE anything is written.

    The question the upload preview asks of a file, asked the same way: the
    rows against every OTHER source of the account, with the rows `source`
    already holds in this account left out (a statement of the same source
    already read in would otherwise corroborate the next one with itself),
    and every other account kept in as the sibling pool that lets a row be
    recognised as another account's.
    """
    held = [
        row
        for row in store.transactions_by_sighting()
        if not (row.account_id == account and row.source == source)
    ]
    found = agreements(held + incoming, sibling_accounts=account_map.accounts_by_source())
    return AssignmentCheck(
        doubt=assignment_doubt(found, source=source, account=account),
        corroboration=assignment_corroboration(found, source=source, account=account),
    )


def read_sections(payload: bytes) -> tuple[PdfStatementParser, list[SectionReading]] | None:
    """The parser and the sections of a multi-account document, or None.

    None means the document is read whole: a single account, a format that has
    no sections, or one nothing recognises. A document whose sections cannot be
    told apart raises `ParseError` for the whole of it.
    """
    parser = detect(payload)
    if not isinstance(parser, PdfStatementParser):
        return None
    found = parser.sections(payload)
    return None if found is None else (parser, found)


def assign_section(
    store: Store,
    *,
    artefact_id: int,
    section_key: str,
    account: str,
    account_map: AccountMap,
) -> str:
    """Read one section of a kept statement into `account`, and remember the choice.

    Read BEFORE it is recorded, so a section the gate refuses stays waiting
    for an account instead of sitting under one with no rows - which a later
    rebuild would replay into the same refusal. After the rows are in, the
    same two passes follow as after every other import: the Space fold, and
    the settlement of review flags the evidence already answers.
    """
    destination = account.strip()
    if not destination:
        return "No account was named, so nothing was assigned."
    try:
        validate_canonical_name(destination)
    except ValueError as exc:
        return f"Not assigned: {exc}"
    row = store.connection.execute(
        "SELECT digest, origin, payload FROM raw_artefacts "
        "WHERE rowid = ? AND source = 'statement'",
        (artefact_id,),
    ).fetchone()
    if row is None:
        return f"No kept statement {artefact_id}."
    payload = bytes(row["payload"])
    read = read_sections(payload)
    if read is None:
        raise DataError(
            "this statement is not divided into accounts, so it is assigned "
            "whole rather than by section"
        )
    parser, found = read
    chosen = next(
        (
            item
            for item in found
            if section_key in (item.key, section_token(item.key))
        ),
        None,
    )
    if chosen is None:
        raise DataError("the statement holds no such account")
    incoming = list(parser.parse_section(payload, chosen.key, account_id=destination))
    check = check_assignment(
        store,
        incoming=incoming,
        source=parser.source,
        account=destination,
        account_map=account_map,
    )
    if check.refusal is not None:
        return check.refusal
    digest = str(row["digest"])
    store.assign_statement_section(digest, chosen.key, destination, chosen.label)
    summary = ImportSummary(artefact_new=False)
    reconcile_batch(store, incoming, digest=digest, summary=summary)
    summary.folded += fold_space_copies(store, account_map).newly_folded
    settle_review_flags(store)
    return (
        f"{row['origin']}, the account labelled {masked(chosen.label)}, assigned to "
        f"{destination} and read by {parser.source}: {summary.describe()}; "
        f"{check.corroboration}"
    )


@dataclass
class SectionBatches:
    """What a rebuild reads back out of one multi-account statement."""

    #: (account, rows) per assigned section, in document order.
    batches: list[tuple[str, list[Transaction]]] = field(default_factory=list)
    #: Sections the document holds that nobody has given an account.
    unassigned: int = 0
    #: Why an assigned section could not be read back, one line each.
    problems: list[str] = field(default_factory=list)


def replay_batches(
    store: Store, digest: str, payload: bytes, resolve: Callable[[str], str]
) -> SectionBatches:
    """Every assigned section of one kept statement, as rows under its account.

    The document is read only if a section of it is assigned or the format
    could have sections; a statement of a format with none costs nothing here.
    `resolve` maps a stored account reference through the current account map,
    the way a single artefact's filing is mapped at replay.
    """
    result = SectionBatches()
    assigned = {
        item.section_key: item for item in store.statement_section_assignments(digest)
    }
    try:
        read = read_sections(payload)
    except (DataError, ValueError) as exc:
        if assigned:
            result.problems.append(
                f"{len(assigned)} assigned section(s) of a kept statement could "
                f"not be read back: {exc}"
            )
        return result
    if read is None:
        if assigned:
            result.problems.append(
                f"{len(assigned)} section(s) were assigned, but the statement now "
                "reads as a single account"
            )
        return result
    parser, found = read
    for item in found:
        held = assigned.pop(item.key, None)
        if held is None:
            result.unassigned += 1
            continue
        if item.refusal:
            result.problems.append(
                f"the section {masked(item.label)} assigned to {held.account_ref} "
                f"is now refused: {item.refusal}"
            )
            continue
        account = resolve(held.account_ref)
        result.batches.append((account, list(parser.rows_of(item.reading, account))))
    for held in assigned.values():
        result.problems.append(
            f"the section {masked(held.label)} assigned to {held.account_ref} is "
            "no longer in the statement"
        )
    return result
