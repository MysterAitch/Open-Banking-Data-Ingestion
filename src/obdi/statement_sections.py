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
from .core.errors import DataError
from .core.models import Transaction
from .core.namespaces import validate_canonical_name
from .core.plural import agree, plural
from .coverage import (
    MATCHER_AGREES_THRESHOLD,
    DoubtReport,
    agreements,
    assignment_corroboration,
    assignment_doubt,
)
from .declined_items import void_declined_items
from .ingest import ImportSummary, MatcherPreview, preview_reconcile, reconcile_batch
from .parsers.pdf_statements import PdfStatementParser, SectionReading
from .parsers.uk_banks import detect
from .protection import recheck
from .review_settlement import settle_review_flags
from .same_money_fold import fold_same_money
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


@dataclass(frozen=True)
class AssignmentCheck:
    """What assigning a statement's rows to an account was checked against."""

    #: Why the statement probably is not this account's; None when nothing
    #: doubts it. Judged by `coverage.assignment_doubt`.
    doubt: str | None
    #: What it was checked against, in counts, for the result sentence.
    corroboration: str
    #: The matcher's dry run over the rows, when one was needed to judge or to
    #: explain a doubt.
    preview: MatcherPreview | None = None

    @property
    def _stopped_doubt(self) -> str:
        doubt = self.doubt or ""
        return doubt if doubt.endswith(("?", ".")) else f"{doubt}."

    def refusal(self, *, acknowledged: bool) -> str | None:
        """The result sentence for a refused assignment, or None to proceed.

        A doubt the person has acknowledged does not refuse: it is recorded in
        the result sentence by `outcome_note` instead.
        """
        if self.doubt is None or acknowledged:
            return None
        return (
            f"Not assigned: {self._stopped_doubt} Nothing was read in; "
            "choose the account again."
        )

    @property
    def report(self) -> DoubtReport | None:
        """The doubt with the evidence beside it, for the confirmation page."""
        if self.doubt is None:
            return None
        return DoubtReport(
            doubt=self._stopped_doubt,
            evidence=self.preview.describe() if self.preview is not None else "",
        )

    @property
    def outcome_note(self) -> str:
        """What the result sentence says after the counts it was checked by."""
        if self.doubt is None:
            return self.corroboration
        return (
            f"{self.corroboration}; assigned over a stated doubt: "
            f"{self._stopped_doubt}"
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
    previews: list[MatcherPreview] = []
    # Imported here: `family_anchors` reaches the store's readers, which reach `ingest`.
    from .family_anchors import families_of

    def preview() -> MatcherPreview:
        if not previews:
            previews.append(
                preview_reconcile(
                    store, incoming, space_blind=families_of(store, account_map).blind_in
                )
            )
        return previews[0]

    def matcher_agrees() -> bool:
        return preview().share_merged >= MATCHER_AGREES_THRESHOLD

    doubt = assignment_doubt(
        found, source=source, account=account, matcher_agrees=matcher_agrees
    )
    if doubt is not None:
        preview()
    corroboration = assignment_corroboration(found, source=source, account=account)
    if previews and doubt is None:
        corroboration = f"{corroboration}; {previews[0].clause}"
    return AssignmentCheck(
        doubt=doubt,
        corroboration=corroboration,
        preview=previews[0] if previews else None,
    )


def review_section(
    store: Store,
    *,
    artefact_id: int,
    section_key: str,
    account: str,
    account_map: AccountMap,
) -> DoubtReport | None:
    """The doubt assigning this section would raise, if any, writing nothing.

    None for anything `assign_section` would answer without reading rows (no
    account, an invalid name, no such statement or account of it): those
    refusals need no confirmation, and the assignment says them itself.
    """
    try:
        prepared = _prepare_section(
            store,
            artefact_id=artefact_id,
            section_key=section_key,
            account=account,
            account_map=account_map,
        )
    except (DataError, ValueError):
        return None
    if isinstance(prepared, str):
        return None
    return prepared.check.report


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


@dataclass(frozen=True)
class _PreparedSection:
    """A section read and checked, and not yet recorded or read in."""

    digest: str
    origin: str
    parser: PdfStatementParser
    label: str
    key: str
    incoming: list[Transaction]
    destination: str
    check: AssignmentCheck


def _prepare_section(
    store: Store,
    *,
    artefact_id: int,
    section_key: str,
    account: str,
    account_map: AccountMap,
) -> _PreparedSection | str:
    """Read and check one section, or the sentence that answers without reading."""
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
    return _PreparedSection(
        digest=str(row["digest"]),
        origin=str(row["origin"]),
        parser=parser,
        label=chosen.label,
        key=chosen.key,
        incoming=incoming,
        destination=destination,
        check=check,
    )


def assign_section(
    store: Store,
    *,
    artefact_id: int,
    section_key: str,
    account: str,
    account_map: AccountMap,
    doubt_acknowledged: bool = False,
) -> str:
    """Read one section of a kept statement into `account`, and remember the choice.

    Read BEFORE it is recorded, so a section the gate refuses stays waiting
    for an account instead of sitting under one with no rows - which a later
    rebuild would replay into the same refusal. After the rows are in, the
    same two passes follow as after every other import: the Space fold, and
    the settlement of review flags the evidence already answers.

    A doubt about whose the section is stops the assignment unless
    `doubt_acknowledged` says a person has seen it and chose to go on; the
    result sentence then quotes it.
    """
    prepared = _prepare_section(
        store,
        artefact_id=artefact_id,
        section_key=section_key,
        account=account,
        account_map=account_map,
    )
    if isinstance(prepared, str):
        return prepared
    refusal = prepared.check.refusal(acknowledged=doubt_acknowledged)
    if refusal is not None:
        return refusal
    store.assign_statement_section(
        prepared.digest, prepared.key, prepared.destination, prepared.label
    )
    summary = ImportSummary(artefact_new=False)
    # Imported here: `family_anchors` reaches the store's readers, which reach `ingest`.
    from .family_anchors import families_of

    reconcile_batch(
        store,
        prepared.incoming,
        digest=prepared.digest,
        summary=summary,
        space_blind=families_of(store, account_map).blind_in,
    )
    void_declined_items(store)
    summary.folded += fold_space_copies(store, account_map).newly_folded
    summary.same_money_folded += fold_same_money(store, account_map).newly_folded
    settle_review_flags(store)
    recheck(store)
    return (
        f"{prepared.origin}, the account labelled {masked(prepared.label)}, assigned to "
        f"{prepared.destination} and read by {prepared.parser.source}: "
        f"{summary.describe()}; {prepared.check.outcome_note}"
    )


def move_section(store: Store, *, artefact_id: int, section_key: str, account: str) -> str:
    """Move one assigned section of a kept statement to another account, with its rows.

    The section is named by its key or its token. Only a section somebody assigned is moved: one
    still waiting is assigned (`assign_section`), which reads and checks it, and a move reads
    nothing. Returns the account it was under; a refusal is a `DataError`. The rows follow in
    the store's own tables at once, and the owner is told to rebuild from raw so every derived
    view agrees (`Store.move_statement_section`).
    """
    destination = account.strip()
    if not destination:
        raise DataError("No account was named, so nothing was moved.")
    try:
        validate_canonical_name(destination)
    except ValueError as exc:
        raise DataError(f"Not moved: {exc}") from exc
    row = store.connection.execute(
        "SELECT digest FROM raw_artefacts WHERE rowid = ? AND source = 'statement'",
        (artefact_id,),
    ).fetchone()
    if row is None:
        raise DataError(f"No kept statement {artefact_id}.")
    digest = str(row["digest"])
    chosen = next(
        (
            item
            for item in store.statement_section_assignments(digest)
            if section_key in (item.section_key, section_token(item.section_key))
        ),
        None,
    )
    if chosen is None:
        raise DataError(
            "that account of the statement has not been given an account, so there is nothing "
            "to move; give it one instead"
        )
    old = store.move_statement_section(digest, chosen.section_key, destination)
    if old is None:
        raise DataError("that account of the statement is no longer assigned")
    return old


@dataclass
class SectionBatches:
    """What a rebuild reads back out of one multi-account statement."""

    #: (account, rows) per assigned section, in document order.
    batches: list[tuple[str, list[Transaction]]] = field(default_factory=list)
    #: Sections the document holds that nobody has given an account.
    unassigned: int = 0
    #: Sections the document holds, assigned or not; zero for a document that
    #: reads as a single account or cannot be read.
    sections: int = 0
    #: Why an assigned section could not be read back, one line each.
    problems: list[str] = field(default_factory=list)

    @property
    def every_section_assigned(self) -> bool:
        """A several-account document that is waiting for nothing."""
        return self.sections > 0 and self.unassigned == 0


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
                f"{plural(len(assigned), 'assigned section')} of a kept statement could "
                f"not be read back: {exc}"
            )
        return result
    if read is None:
        if assigned:
            result.problems.append(
                f"{plural(len(assigned), 'section')} {agree(len(assigned), 'was')} assigned, "
                "but the statement now "
                "reads as a single account"
            )
        return result
    parser, found = read
    result.sections = len(found)
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
